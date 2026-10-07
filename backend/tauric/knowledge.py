"""Private, searchable memory copies with provenance and explicit owner controls.

The original strategy, chat and research records remain authoritative. Nothing
in this module issues qualifications, changes risk, or participates in execution.
"""

import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import exists, func, literal_column, select

from . import paper, services
from .config import runtime_config
from .knowledge_contracts import KnowledgeSearch, KnowledgeView, MemorySettings, MemoryState
from .knowledge_models import KnowledgeDocument, MemorySettingsVersion, MemorySyncState
from .models import AssistantRecord, ChatTurn, StrategyVersion, now
from .research_models import ResearchRecord

KINDS = {"strategy", "chat", "research", "preference", "wiki"}


def lock(db, owner_id):
    # Separate from every trading/owner lock: a memory outage cannot hold a trade lock.
    key = int.from_bytes(hashlib.sha256(("tauric-memory:" + owner_id).encode()).digest()[:7])
    db.execute(select(func.pg_advisory_xact_lock(key)))


def settings(db, owner_id):
    row = db.scalar(
        select(MemorySettingsVersion)
        .where(MemorySettingsVersion.owner_id == owner_id)
        .order_by(MemorySettingsVersion.revision.desc())
        .limit(1)
    )
    if not row:
        return MemorySettings(), 0
    if paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Memory settings integrity failed.")
    return MemorySettings.model_validate(row.payload), row.revision


def save_settings(db, owner, body):
    lock(db, owner.id)
    _, revision = settings(db, owner.id)
    if revision != body.expected_revision:
        raise HTTPException(409, "Memory controls changed. Reload before saving.")
    payload = body.model_dump(exclude={"expected_revision"}, mode="json")
    db.add(
        MemorySettingsVersion(
            owner_id=owner.id,
            revision=revision + 1,
            payload=payload,
            checksum=paper.checksum(payload),
        )
    )
    services.audit(
        db,
        owner,
        "memory.settings",
        "memory",
        owner.id,
        "Owner saved memory controls",
        {"revision": revision + 1},
    )
    db.commit()
    return state(db, owner)


def redact(value):
    # Best effort only. No model/connection files or credentials are ingested.
    text = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{16,}|hch-[A-Za-z0-9_-]{16,})\b",
        "[credential removed]",
        value.replace("\x00", ""),
    )
    text = re.sub(
        r"(?i)((?:api[_ -]?key|password|access[_ -]?token|bearer)\s*[:= ]\s*)[^\s,;\n]+",
        r"\1[credential removed]",
        text,
    )
    return text


def digest(row):
    return paper.checksum(
        {"kind": row.kind, "title": row.title, "body": row.body, "provenance": row.provenance}
    )


def view(row, excerpt=None):
    if digest(row) != row.checksum:
        raise HTTPException(409, "Knowledge copy integrity failed.")
    return KnowledgeView(
        id=row.id,
        kind=row.kind,
        source_kind=row.source_kind,
        title=row.title,
        body=row.body if excerpt is None else row.body[:excerpt],
        provenance=row.provenance,
        checksum=row.checksum,
        revision=row.revision,
        excluded=row.excluded,
        updated_at=row.updated_at,
    )


def owned(db, owner, key):
    row = db.get(KnowledgeDocument, str(key))
    if not row or row.owner_id != owner.id:
        raise HTTPException(404, "Knowledge note not found.")
    view(row)
    return row


def search(
    db,
    owner,
    query="",
    kind=None,
    include_hidden=False,
    limit=25,
    excerpt=None,
    exclude_chats=False,
    offset=0,
):
    if len(query) > 300 or kind and kind not in KINDS:
        raise HTTPException(422, "Use a short search and a supported note category.")
    config, _ = settings(db, owner.id)
    statement = select(KnowledgeDocument).where(KnowledgeDocument.owner_id == owner.id)
    if not include_hidden:
        statement = statement.where(KnowledgeDocument.excluded.is_(False))
    if not config.record_chats:
        statement = statement.where(KnowledgeDocument.kind != "chat")
    if not config.record_strategies:
        statement = statement.where(KnowledgeDocument.kind != "strategy")
    if not config.record_research:
        statement = statement.where(KnowledgeDocument.source_kind.not_in(["research", "assistant"]))
    if kind:
        statement = statement.where(KnowledgeDocument.kind == kind)
    if exclude_chats:
        statement = statement.where(KnowledgeDocument.kind != "chat")
    if query.strip():
        vector = func.to_tsvector(
            literal_column("'english'"), KnowledgeDocument.title + " " + KnowledgeDocument.body
        )
        terms = func.websearch_to_tsquery(literal_column("'english'"), query.strip())
        statement = statement.where(vector.op("@@")(terms)).order_by(
            func.ts_rank_cd(vector, terms).desc()
        )
    rows = list(
        db.scalars(
            statement.order_by(KnowledgeDocument.updated_at.desc(), KnowledgeDocument.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    return KnowledgeSearch(
        query=query, items=[view(row, excerpt) for row in rows[:limit]], has_more=len(rows) > limit
    )


def save_note(db, owner, body, document_id=None):
    lock(db, owner.id)
    key = str(uuid5(NAMESPACE_URL, "tauric-knowledge:" + owner.id + ":" + str(body.request_id)))
    row = owned(db, owner, document_id) if document_id else db.get(KnowledgeDocument, key)
    title, text = redact(body.title), redact(body.body)
    if row:
        if row.source_kind != "note":
            raise HTTPException(409, "Edit the original source or add a separate research note.")
        if not document_id:
            if (row.kind, row.title, row.body) != (body.kind, title, text):
                raise HTTPException(409, "This note request already saved different content.")
            return view(row)
        if row.revision != body.expected_revision:
            raise HTTPException(409, "This note changed. Reload before editing.")
        row.revision += 1
    else:
        if body.expected_revision:
            raise HTTPException(409, "Start a new note at revision zero.")
        row = KnowledgeDocument(
            id=key,
            owner_id=owner.id,
            source_kind="note",
            source_id=str(body.request_id),
            revision=1,
        )
        db.add(row)
    row.kind, row.title, row.body = body.kind, title, text
    row.provenance = {
        "author": "owner",
        "confidence": "explicit_owner_preference"
        if body.kind == "preference"
        else "owner_research_note",
        "native_evidence": False,
    }
    row.updated_at = now()
    row.checksum = digest(row)
    services.audit(
        db,
        owner,
        "memory.note",
        "knowledge",
        row.id,
        "Owner saved a knowledge note",
        {"kind": row.kind, "revision": row.revision},
    )
    db.commit()
    return view(row)


def visibility(db, owner, key, body):
    lock(db, owner.id)
    row = owned(db, owner, key)
    if row.revision != body.expected_revision:
        raise HTTPException(409, "This note changed. Reload its visibility.")
    row.excluded, row.revision, row.updated_at = body.excluded, row.revision + 1, now()
    # Never inject a cached profile after its source selection changes.
    sync = db.get(MemorySyncState, owner.id)
    if sync:
        sync.representation = ""
    services.audit(
        db,
        owner,
        "memory.visibility",
        "knowledge",
        row.id,
        "Owner changed wiki visibility",
        {"excluded": body.excluded},
    )
    db.commit()
    return view(row)


def add_copy(db, owner_id, source_kind, source_id, kind, title, body, provenance):
    key = str(uuid5(NAMESPACE_URL, "tauric-copy:" + owner_id + ":" + source_kind + ":" + source_id))
    row = db.get(KnowledgeDocument, key)
    text = redact(body)[:20000]
    provenance = {**provenance, "native_evidence": False, "body_truncated": len(body) > 20000}
    title = redact(title)[:160]
    new_digest = paper.checksum(
        {"kind": kind, "title": title, "body": text, "provenance": provenance}
    )
    if row and row.checksum == new_digest:
        return row
    if not row:
        row = KnowledgeDocument(
            id=key,
            owner_id=owner_id,
            source_kind=source_kind,
            source_id=source_id,
            revision=1,
            excluded=False,
        )
        db.add(row)
    else:
        row.revision += 1
    row.kind, row.title, row.body, row.provenance, row.checksum = (
        kind,
        title,
        text,
        provenance,
        new_digest,
    )
    row.updated_at = now()
    return row


def pending(db, model, owner_id, source_kind, *conditions):
    already = exists().where(
        KnowledgeDocument.owner_id == owner_id,
        KnowledgeDocument.source_kind == source_kind,
        KnowledgeDocument.source_id == model.id,
    )
    return db.scalars(
        select(model)
        .where(model.owner_id == owner_id, ~already, *conditions)
        .order_by(model.created_at, model.id)
        .limit(100)
    )


def collect(db, owner):
    lock(db, owner.id)
    config, _ = settings(db, owner.id)
    if not config.enabled:
        return 0
    count = 0
    if config.record_strategies:
        for row in pending(db, StrategyVersion, owner.id, "strategy"):
            if paper.checksum(row.payload) != row.checksum:
                raise HTTPException(
                    409, "Original strategy integrity failed; no memory copy created."
                )
            text = (
                row.payload["parameters"]["description"]
                + "\n\nSaved version settings:\n```json\n"
                + json.dumps(row.payload["parameters"], indent=2)
                + "\n```"
            )
            add_copy(
                db,
                owner.id,
                "strategy",
                row.id,
                "strategy",
                f"{row.name} · version {row.revision}",
                text,
                {
                    "source_sha256": row.checksum,
                    "strategy_id": row.strategy_id,
                    "strategy_version_id": row.id,
                    "original_page": "strategies",
                },
            )
            count += 1
    if config.record_chats:
        for row in pending(db, ChatTurn, owner.id, "chat", ChatTurn.status == "succeeded"):
            text = "## You\n\n" + row.user_text + "\n\n## OpenClaw\n\n" + (row.assistant_text or "")
            add_copy(
                db,
                owner.id,
                "chat",
                row.id,
                "chat",
                row.user_text.strip()[:120],
                text,
                {
                    "source_sha256": paper.checksum(
                        {"user": row.user_text, "assistant": row.assistant_text}
                    ),
                    "conversation_id": row.conversation_id,
                    "portfolio_id": row.portfolio_id,
                    "turn_id": row.id,
                    "original_page": "chat",
                },
            )
            count += 1
    if config.record_research:
        for row in pending(db, ResearchRecord, owner.id, "research"):
            if paper.checksum(row.payload) != row.checksum:
                raise HTTPException(
                    409, "Original research integrity failed; no memory copy created."
                )
            content = {k: v for k, v in row.payload.items() if k not in {"candles", "result"}}
            if "result" in row.payload:
                content["result"] = {
                    k: v
                    for k, v in row.payload["result"].items()
                    if k not in {"equity", "trades", "fills", "signals", "financing_events"}
                }
            add_copy(
                db,
                owner.id,
                "research",
                row.id,
                "research",
                row.name,
                json.dumps(content, indent=2),
                {
                    "source_sha256": row.checksum,
                    "research_kind": row.kind,
                    "record_id": row.id,
                    "original_page": "research",
                },
            )
            count += 1
        for row in pending(
            db,
            AssistantRecord,
            owner.id,
            "assistant",
            AssistantRecord.stage == "completed",
            AssistantRecord.operation == "save_research_note",
        ):
            if paper.checksum(row.payload) != row.checksum:
                raise HTTPException(409, "Original assistant receipt integrity failed.")
            note = row.payload["result"]
            parts = [note.get("hypothesis", "")]
            for field, label in [
                ("evidence", "Evidence"),
                ("counter_evidence", "Counter-evidence"),
                ("test_plan", "Planned tests"),
            ]:
                parts.append(
                    "## " + label + "\n" + "\n".join("- " + v for v in note.get(field, []))
                )
            parts.append("Decision: " + str(note.get("decision", "needs_data")))
            add_copy(
                db,
                owner.id,
                "assistant",
                row.id,
                "research",
                note.get("title", row.title),
                "\n\n".join(parts),
                {"source_sha256": row.checksum, "receipt_id": row.id, "original_page": "chat"},
            )
            count += 1
    sync = db.get(MemorySyncState, owner.id)
    if not sync:
        sync = MemorySyncState(owner_id=owner.id)
        db.add(sync)
    sync.heartbeat_at = sync.last_collected_at = now()
    db.commit()
    return count


def vault_root(owner_id):
    config = runtime_config()
    root = Path(config.knowledge_vault_directory)
    if not root.is_dir():
        return None
    root = root.resolve()
    folder = root / str(UUID(owner_id))
    if folder.is_symlink():
        raise ValueError("Knowledge owner folders cannot be links")
    return folder


def safe_file(folder, relative):
    root = folder.resolve()
    target = folder / relative
    if any(
        (folder / Path(*Path(relative).parts[:i])).is_symlink()
        for i in range(1, len(Path(relative).parts) + 1)
    ) or not target.resolve().is_relative_to(root):
        raise ValueError("Knowledge files must stay inside their owner folder")
    return target


def import_vault(db, owner):
    folder = vault_root(owner.id)
    if folder is None:
        return 0
    inbox = safe_file(folder, "Inbox")
    if not inbox.is_dir():
        return 0
    count = 0
    for path in sorted(inbox.glob("*.md"))[:200]:
        if path.name.startswith(".") or path.is_symlink():
            continue
        safe_file(folder, "Inbox/" + path.name)
        try:
            raw = path.read_bytes()
            if len(raw) > 80000:
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeError):
            # A removed, unreadable or non-Markdown file must not pause learning.
            continue
        title = next(
            (line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("# ")),
            path.stem,
        )
        add_copy(
            db,
            owner.id,
            "obsidian",
            "Inbox/" + path.name,
            "wiki",
            title or path.stem,
            text,
            {
                "file": "Inbox/" + path.name,
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "confidence": "owner_file_not_validated",
            },
        )
        count += 1
    db.commit()
    return count


def export_vault(db, owner):
    folder = vault_root(owner.id)
    if folder is None:
        return 0
    folder.mkdir(parents=True, exist_ok=True)
    inbox = safe_file(folder, "Inbox")
    inbox.mkdir(exist_ok=True)
    readme = safe_file(folder, "Start here.md")
    if not readme.exists():
        readme.write_text(
            "# Tauric knowledge\n\nSaved strategies, chats and research are mirrored into their folders. Put your own Markdown research notes in **Inbox**; the local memory worker indexes them automatically. Generated copies are context, not native trading evidence. Editing a copy never changes a Tauric strategy. Edited generated files are preserved and reported as conflicts.\n"
        )
    conflicts = 0
    for row in db.scalars(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.owner_id == owner.id, KnowledgeDocument.source_kind != "obsidian")
        .order_by(KnowledgeDocument.id)
    ):
        view(row)
        name = {
            "strategy": "Strategies",
            "chat": "Chats",
            "research": "Research",
            "preference": "Preferences",
            "wiki": "Notes",
        }[row.kind]
        title = re.sub(r'[\\/:*?"<>|]+', " ", row.title)
        title = re.sub(r"\s+", " ", title).strip(" .")[:70] or "Saved note"
        path = safe_file(folder, name + "/" + title + " -- " + row.id + ".md")
        path.parent.mkdir(exist_ok=True)
        text = (
            "---\n"
            + "\n".join(
                f"{key}: {json.dumps(value)}"
                for key, value in {
                    "title": row.title,
                    "tauric_document_id": row.id,
                    "kind": row.kind,
                    "revision": row.revision,
                    "checksum": row.checksum,
                    "hidden_from_wiki": row.excluded,
                    "authority": "memory_context_only",
                }.items()
            )
            + "\n---\n\n# "
            + row.title
            + "\n\n"
            + row.body
            + "\n\n---\nSource: "
            + row.source_kind
            + " · "
            + row.source_id
            + "\n"
        )
        expected = hashlib.sha256(text.encode()).hexdigest()
        previous = (
            [path]
            if path.exists()
            else [
                safe_file(folder, name + "/" + candidate.name)
                for candidate in path.parent.glob("*.md")
                if candidate.name == row.id + ".md"
                or candidate.name.endswith(" -- " + row.id + ".md")
            ]
        )
        if len(previous) > 1:
            conflicts += 1
            continue
        if previous:
            original = previous[0]
            actual = hashlib.sha256(original.read_bytes()).hexdigest()
            if actual != expected and actual != row.export_hash:
                conflicts += 1
                continue
            if original != path:
                original.replace(path)
            if actual == expected:
                row.export_hash = expected
                continue
        temporary = safe_file(folder, name + "/" + row.id + ".tmp")
        temporary.write_text(text)
        temporary.replace(path)
        row.export_hash = expected
    sync = db.get(MemorySyncState, owner.id)
    if sync:
        sync.vault_conflicts = conflicts
    db.commit()
    return conflicts


def recalled_representation(db, owner_id, config, sync):
    from .honcho_memory import selection_hash

    if not sync or not config.enabled or not config.honcho_enabled:
        return ""
    if sync.honcho_selection_hash != selection_hash(db, owner_id, config):
        return ""
    return sync.representation[:5000]


def recall_allowed(db, owner, config=None, runtime=None):
    """Profile names never establish privacy; inspect the actual model endpoint."""
    from . import governance

    config = config or settings(db, owner.id)[0]
    if not config.enabled or not config.use_in_chat:
        return False, "Memory recall is paused."
    if config.allow_external_context:
        return True, "Selected memory may be sent to the configured OpenClaw chat model."
    try:
        route = runtime or governance.routing(db, owner)
        profile = route["routes"].get(route["chat"]["profile"], {})
        endpoint = urlparse(profile.get("base_url", ""))
        if (
            profile.get("provider") in {"openai_compatible", "ollama"}
            and endpoint.scheme == "http"
            and endpoint.hostname
            in {"127.0.0.1", "localhost", "::1", "host.docker.internal", "memory-model"}
        ):
            return True, "Selected memory is available to the local OpenClaw chat model."
    except (KeyError, ValueError, OSError, HTTPException):
        pass
    return (
        False,
        "The current OpenClaw chat model is remote. Saved memory stays local; chat recall is blocked.",
    )


def state(db, owner):
    from .honcho_memory import private_config

    config, revision = settings(db, owner.id)
    sync = db.get(MemorySyncState, owner.id)
    provider = private_config()
    counts = dict(
        db.execute(
            select(KnowledgeDocument.kind, func.count())
            .where(KnowledgeDocument.owner_id == owner.id, KnowledgeDocument.excluded.is_(False))
            .group_by(KnowledgeDocument.kind)
        ).all()
    )
    folder = vault_root(owner.id)
    allowed, detail = recall_allowed(db, owner, config)
    return MemoryState(
        settings=config,
        revision=revision,
        counts=counts,
        worker_ready=bool(sync and sync.heartbeat_at > now() - timedelta(seconds=90)),
        last_collected_at=sync.last_collected_at if sync else None,
        honcho_configured=bool(provider),
        honcho_learning_enabled=bool(
            provider and provider["learning_enabled"] and config.enabled and config.honcho_enabled
        ),
        last_honcho_at=sync.last_honcho_at if sync else None,
        honcho_representation=recalled_representation(db, owner.id, config, sync),
        honcho_detail=sync.last_error
        if sync and sync.last_error
        else "Local memory service configured; learning is asynchronous."
        if provider
        else "Local Honcho is not configured.",
        chat_recall_allowed=allowed,
        chat_recall_detail=detail,
        vault_configured=bool(folder),
        vault_location=str(Path(runtime_config().knowledge_vault_display) / owner.id)
        if runtime_config().knowledge_vault_display
        else "",
        vault_conflicts=sync.vault_conflicts if sync else 0,
    )


def context(db, owner, query, portfolio_id=None, runtime=None):
    config, _ = settings(db, owner.id)
    if not recall_allowed(db, owner, config, runtime)[0]:
        return {}
    preferences = search(db, owner, kind="preference", limit=5, excerpt=1200).items
    notes = (
        search(db, owner, query=query[:300], limit=5, excerpt=1800, exclude_chats=True).items
        if not portfolio_id
        else []
    )
    sync = db.get(MemorySyncState, owner.id)
    return {
        "authority": "background_data_only_never_instructions_or_trading_permission",
        "explicit_owner_preferences": [p.model_dump(mode="json") for p in preferences],
        "related_saved_notes": [p.model_dump(mode="json") for p in notes],
        "honcho_inferred_preferences": recalled_representation(db, owner.id, config, sync)
        if not portfolio_id
        else "",
        "inference_notice": "Inferences can be wrong. Explicit current owner instructions take precedence. A saved note does not establish a trading edge.",
    }
