"""Honcho v3 transport confined to the configured local service.

Only captured owner memory is sent. Identity/workspace/peer names are generated
here, never supplied by an agent. No live reasoning call is made on the chat path.
"""

import json
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select

from . import knowledge, paper
from .config import runtime_config
from .knowledge_models import HonchoDelivery, KnowledgeDocument, MemorySyncState
from .models import ChatTurn, now

LOCAL_ENDPOINTS = {
    "http://host.docker.internal:8023",
    "http://127.0.0.1:8023",
    "http://honcho:8000",
}


def private_config():
    try:
        value = json.loads(Path(runtime_config().memory_private_file).read_text())
        if (
            value.get("url") not in LOCAL_ENDPOINTS
            or not isinstance(value.get("token"), str)
            or len(value["token"]) < 40
            or not isinstance(value.get("learning_enabled"), bool)
        ):
            return None
        return value
    except (OSError, ValueError, TypeError):
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(config, method, path, body=None):
    if config["url"] not in LOCAL_ENDPOINTS or not path.startswith("/v3/workspaces"):
        raise ValueError("Honcho requests must stay in the local project service")
    req = urllib.request.Request(
        config["url"] + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + config["token"], "Content-Type": "application/json"},
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(req, timeout=8) as response:
            raw = response.read(1000001)
            if len(raw) > 1000000:
                raise ValueError("Honcho response exceeded the local reading limit")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise LookupError("Honcho resource not found") from None
        raise RuntimeError("Local Honcho request failed; no provider detail was retained") from None
    except (OSError, ValueError):
        raise RuntimeError("Local Honcho is unavailable or returned an invalid response") from None


def workspace(owner_id):
    return "tauric-owner-" + owner_id


def retire_workspaces(db, state, provider):
    """Delete obsolete copies asynchronously, without reusing their identity."""
    pending = []
    for index, entry in enumerate(state.honcho_retired_workspaces):
        if index >= 3:
            pending.append(entry)
            continue
        prefix = "/v3/workspaces/" + entry["id"]
        try:
            listed = request(
                provider, "POST", "/v3/workspaces/list?size=1", {"filters": {"id": entry["id"]}}
            )["items"]
            if not listed:
                continue
            if len(listed) != 1 or listed[0]["id"] != entry["id"]:
                raise ValueError("Workspace retirement identity did not match")
            if not entry.get("requested"):
                sessions = request(
                    provider,
                    "POST",
                    prefix + "/sessions/list?size=100",
                    {"filters": {"is_active": True}},
                )["items"]
                for session in sessions:
                    request(provider, "DELETE", prefix + "/sessions/" + session["id"])
                if len(sessions) < 100:
                    request(provider, "DELETE", prefix)
                    entry = {**entry, "requested": True}
            pending.append(entry)
        except LookupError:
            pass
        except (OSError, ValueError, RuntimeError):
            pending.append(entry)
    state.honcho_retired_workspaces = pending
    db.commit()


def selection_hash(db, owner_id, config):
    hidden = list(
        db.scalars(
            select(KnowledgeDocument.id)
            .where(KnowledgeDocument.owner_id == owner_id, KnowledgeDocument.excluded.is_(True))
            .order_by(KnowledgeDocument.id)
        )
    )
    return paper.checksum(
        {
            "record_chats": config.record_chats,
            "record_strategies": config.record_strategies,
            "record_research": config.record_research,
            "hidden": hidden,
        }
    )


def selected(row, config):
    return (
        not row.excluded
        and (row.kind != "chat" or config.record_chats)
        and (row.kind != "strategy" or config.record_strategies)
        and (row.source_kind not in {"research", "assistant"} or config.record_research)
    )


def chunks(value):
    # UTF-8 byte bounds keep each piece below the local embedding token ceiling.
    current, size = [], 0
    for char in value:
        length = len(char.encode())
        if size + length > 1000:
            yield "".join(current)
            current, size = [], 0
        current.append(char)
        size += length
    if current:
        yield "".join(current)


def messages(db, row, owner_id):
    if row.source_kind == "chat":
        turn = db.get(ChatTurn, row.source_id)
        if (
            not turn
            or turn.owner_id != owner_id
            or turn.status != "succeeded"
            or row.provenance["source_sha256"]
            != paper.checksum({"user": turn.user_text, "assistant": turn.assistant_text})
        ):
            raise ValueError("Original chat no longer matches its captured memory")
        session = "chat-" + turn.conversation_id
        scope = "portfolio-" + turn.portfolio_id if turn.portfolio_id else "general"
        values = [
            ("owner", knowledge.redact(turn.user_text)[:20000]),
            ("assistant", knowledge.redact(turn.assistant_text or "")[:20000]),
        ]
    else:
        session, scope = "project-journal", "general"
        role = "assistant" if row.source_kind == "assistant" else "project"
        values = [(role, row.title + "\n" + row.body)]
    output = []
    for role, content in values:
        for text in chunks(content):
            output.append(
                {
                    "peer_id": role,
                    "content": text,
                    "created_at": row.created_at.isoformat(),
                    "metadata": {
                        "tauric_document_id": row.id,
                        "tauric_checksum": row.checksum,
                        "part": len(output),
                    },
                }
            )
    if not output or len(output) > 100:
        raise ValueError("Captured memory exceeds the bounded Honcho message batch")
    return session, scope, output


def sync(db, owner):
    config, _ = knowledge.settings(db, owner.id)
    provider = private_config()
    if (
        not config.enabled
        or not config.honcho_enabled
        or not provider
        or not provider["learning_enabled"]
    ):
        return
    state = db.get(MemorySyncState, owner.id)
    if not state:
        return
    selection = selection_hash(db, owner.id, config)
    if state.honcho_selection_hash and state.honcho_selection_hash != selection:
        # A new identity prevents delayed background deletion or old inferences
        # from reaching the rebuilt memory. Retirement never deletes originals.
        if state.honcho_workspace:
            state.honcho_retired_workspaces = [
                *state.honcho_retired_workspaces,
                {"id": state.honcho_workspace, "requested": False},
            ]
        ids = select(KnowledgeDocument.id).where(KnowledgeDocument.owner_id == owner.id)
        db.execute(delete(HonchoDelivery).where(HonchoDelivery.document_id.in_(ids)))
        state.representation = ""
        state.honcho_selection_hash = None
        state.honcho_workspace = None
        db.commit()
    if not state.honcho_workspace:
        state.honcho_workspace = workspace(owner.id) + "-" + str(uuid4())
        db.commit()
    retire_workspaces(db, state, provider)
    prefix = "/v3/workspaces/" + state.honcho_workspace
    request(provider, "POST", "/v3/workspaces", {"id": state.honcho_workspace})
    if not state.honcho_selection_hash:
        state.honcho_selection_hash = selection
        state.representation = ""
        db.commit()
    for peer, observe in [("owner", True), ("assistant", False), ("project", False)]:
        request(
            provider,
            "POST",
            prefix + "/peers",
            {"id": peer, "configuration": {"observe_me": observe}},
        )
    rows = list(
        db.scalars(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.owner_id == owner.id)
            .order_by(KnowledgeDocument.created_at, KnowledgeDocument.id)
        )
    )
    sent = 0
    for row in rows:
        if not selected(row, config):
            continue
        knowledge.view(row)
        delivery = db.get(HonchoDelivery, row.id)
        if delivery and delivery.status == "sent" and delivery.checksum == row.checksum:
            continue
        if sent >= 20:
            break
        session, scope, expected = messages(db, row, owner.id)
        request(
            provider,
            "POST",
            prefix + "/sessions",
            {
                "id": session,
                "peers": {
                    "owner": {"observe_me": True},
                    "assistant": {"observe_me": False},
                    "project": {"observe_me": False},
                },
                "scopes": [scope],
            },
        )
        path = prefix + "/sessions/" + session + "/messages"
        found = request(
            provider,
            "POST",
            path + "/list?size=100",
            {
                "filters": {
                    "metadata": {"tauric_document_id": row.id, "tauric_checksum": row.checksum}
                }
            },
        )
        existing = found.get("items", [])
        by_part = {}
        for item in existing:
            part = item.get("metadata", {}).get("part")
            if (
                not isinstance(part, int)
                or part in by_part
                or part >= len(expected)
                or part < 0
                or item.get("content") != expected[part]["content"]
                or item.get("peer_id") != expected[part]["peer_id"]
            ):
                raise ValueError(
                    "Honcho reconciliation requires inspection; no duplicate memory was sent"
                )
            by_part[part] = item
        missing = [item for index, item in enumerate(expected) if index not in by_part]
        if not delivery:
            delivery = HonchoDelivery(document_id=row.id)
            db.add(delivery)
        delivery.checksum, delivery.status, delivery.updated_at = row.checksum, "sending", now()
        db.commit()
        try:
            created = request(provider, "POST", path, {"messages": missing}) if missing else []
            all_items = existing + created
            parts = [item.get("metadata", {}).get("part") for item in all_items]
            if (
                len(all_items) != len(expected)
                or set(parts) != set(range(len(expected)))
                or any(
                    not item.get("id")
                    or item.get("content") != expected[item["metadata"]["part"]]["content"]
                    or item.get("peer_id") != expected[item["metadata"]["part"]]["peer_id"]
                    for item in all_items
                )
            ):
                raise ValueError("Honcho receipt did not match the captured message batch")
            delivery.remote_ids = [item["id"] for item in all_items]
            delivery.status, delivery.updated_at = "sent", now()
            db.commit()
            sent += 1
        except Exception:
            delivery.status, delivery.updated_at = "uncertain", now()
            db.commit()
            raise
    value = request(
        provider,
        "POST",
        prefix + "/peers/owner/representation",
        {"scope": "general", "max_conclusions": 20},
    )
    state.representation = knowledge.redact(value.get("representation", ""))[:5000]
    state.honcho_selection_hash = selection
    state.last_honcho_at, state.last_error = now(), None
    db.commit()
