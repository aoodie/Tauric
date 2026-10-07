"""Private memory, ownership, source integrity and durable Honcho delivery."""

import json
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_assistant_management import HEADERS, READ, action
from test_assistant_management import invocation as invocation
from test_chat import ready as ready
from test_chat import send

from tauric import chat, chat_worker, governance, honcho_memory, knowledge
from tauric.config import runtime_config
from tauric.knowledge_models import (
    HonchoDelivery,
    KnowledgeDocument,
    MemorySettingsVersion,
    MemorySyncState,
)
from tauric.models import ChatTurn, Owner, StrategyVersion

BASE = "/api/v1/knowledge"


def controls(client, **changes):
    before = client.get(BASE + "/settings").json()
    response = client.put(
        BASE + "/settings",
        json={**before["settings"], **changes, "expected_revision": before["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def note(
    client,
    title="Liquidity hypothesis",
    body="Test whether illiquid breakouts survive spread and slippage.",
    kind="wiki",
):
    response = client.post(
        BASE + "/notes",
        json={"request_id": str(uuid4()), "title": title, "body": body, "kind": kind},
    )
    assert response.status_code == 200, response.text
    return response.json()


def owner(db):
    return db.scalar(select(Owner).where(Owner.username == "test-owner"))


@pytest.fixture
def memory_provider(monkeypatch, tmp_path):
    path = tmp_path / "memory.json"
    path.write_text(
        json.dumps(
            {
                "url": "http://127.0.0.1:8023",
                "token": "fixture-local-memory-token-" + "x" * 40,
                "learning_enabled": True,
            }
        )
    )
    monkeypatch.setenv("TAURIC_MEMORY_PRIVATE_FILE", str(path))
    monkeypatch.setenv("TAURIC_KNOWLEDGE_VAULT_DIRECTORY", str(tmp_path / "vault"))
    monkeypatch.setenv("TAURIC_KNOWLEDGE_VAULT_DISPLAY", str(tmp_path / "vault"))
    (tmp_path / "vault").mkdir()
    runtime_config.cache_clear()
    return path


def test_owner_csrf_and_extra_permissions_rejected(signed_in):
    signed_in.headers.pop("X-CSRF-Token")
    assert (
        signed_in.post(
            BASE + "/notes",
            json={"request_id": str(uuid4()), "title": "A note", "body": "Some evidence"},
        ).status_code
        == 403
    )
    signed_in.cookies.clear()
    assert signed_in.get(BASE).status_code == 401
    assert signed_in.get(BASE + "/settings").status_code == 401


def test_note_cas_idempotency_and_search(signed_in):
    body = {
        "request_id": str(uuid4()),
        "title": "Liquidity hypothesis",
        "body": "Illiquid breakouts require realistic spread costs.",
    }
    first = signed_in.post(BASE + "/notes", json=body)
    assert first.status_code == 200
    row = first.json()
    assert signed_in.post(BASE + "/notes", json=body).json()["id"] == row["id"]
    assert (
        signed_in.post(BASE + "/notes", json={**body, "title": "Different hypothesis"}).status_code
        == 409
    )
    assert (
        signed_in.get(BASE, params={"query": "liquidity spread"}).json()["items"][0]["id"]
        == row["id"]
    )
    assert not signed_in.get(BASE, params={"query": "seasonality"}).json()["items"]
    edit = {
        **body,
        "expected_revision": 1,
        "body": "Illiquid breakouts require spread, fees and slippage.",
    }
    assert signed_in.put(BASE + "/" + row["id"], json=edit).json()["revision"] == 2
    assert signed_in.put(BASE + "/" + row["id"], json=edit).status_code == 409
    assert (
        signed_in.put(
            BASE + "/" + row["id"], json={**edit, "expected_revision": 2, "override_tauric": True}
        ).status_code
        == 422
    )


def test_other_owner_notes_are_invisible(signed_in, database):
    saved = note(signed_in)
    # Tauric has one persisted owner. Foreign caller objects must still fail
    # every ownership check; no second login/owner is fabricated in production.
    with Session(database[0]) as db:
        foreign = Owner(id=str(uuid4()), username="other-owner", password_hash="unused")
        assert not knowledge.search(db, foreign).items
        with pytest.raises(HTTPException) as caught:
            knowledge.owned(db, foreign, saved["id"])
        assert caught.value.status_code == 404


def test_control_history_cas_and_immutable(database, signed_in):
    first = controls(signed_in, enabled=True, honcho_enabled=True)
    assert not first["settings"]["allow_external_context"]
    assert (
        signed_in.put(
            BASE + "/settings", json={**first["settings"], "expected_revision": 0}
        ).status_code
        == 409
    )
    assert (
        signed_in.put(
            BASE + "/settings",
            json={**first["settings"], "expected_revision": 1, "enabled": "true"},
        ).status_code
        == 422
    )
    with Session(database[0]) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE memory_settings_versions SET revision=20"))
        db.rollback()
        assert db.scalar(select(func.count()).select_from(MemorySettingsVersion)) == 1


def test_collect_keeps_originals_and_redacts_secrets(ready, database, invocation):
    assert action(ready, invocation).status_code == 200
    controls(ready, enabled=True)
    with Session(database[0]) as db:
        turn = db.scalar(select(ChatTurn))
        chat.finish(
            db, turn.id, turn.lease_id, result=("First test the baseline.", "fixture-model")
        )
    secret_turn = send(ready, "Use clear labels. api_key=sk-fixture-secret-value-0123456789").json()
    with Session(database[0]) as db:
        claimed = chat.claim(db)
        assert claimed[0] == secret_turn["id"]
        chat.finish(db, *claimed, result=("I will use clear labels.", "fixture-model"))
        before = db.scalar(select(StrategyVersion))
        original = (before.payload, before.checksum)
    assert ready.post(BASE + "/collect").json()["copied"] == 3
    assert ready.post(BASE + "/collect").json()["copied"] == 0
    with Session(database[0]) as db:
        before = db.scalar(select(StrategyVersion))
        assert (before.payload, before.checksum) == original
        copy = db.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.source_id == secret_turn["id"])
        )
        assert "sk-fixture" not in copy.body and "[credential removed]" in copy.body
        assert not copy.provenance["native_evidence"]
        assert "sk-fixture" in db.get(ChatTurn, secret_turn["id"]).user_text
        strategy_copy = db.scalar(
            select(KnowledgeDocument).where(KnowledgeDocument.kind == "strategy")
        )
        key = strategy_copy.id
    assert (
        ready.put(
            BASE + "/" + key,
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "title": "Changed strategy",
                "body": "Change source settings",
            },
        ).status_code
        == 409
    )


def test_pagination_and_hidden_notes(signed_in, database):
    with Session(database[0]) as db:
        own = owner(db)
        for i in range(28):
            knowledge.add_copy(
                db,
                own.id,
                "obsidian",
                f"note-{i}",
                "wiki",
                f"Research {i}",
                "Liquidity research data",
                {},
            )
        db.commit()
    first = signed_in.get(BASE).json()
    second = signed_in.get(BASE, params={"offset": 25}).json()
    assert len(first["items"]) == 25 and first["has_more"]
    assert len(second["items"]) == 3 and not second["has_more"]
    assert not {r["id"] for r in first["items"]} & {r["id"] for r in second["items"]}
    row = first["items"][0]
    assert (
        signed_in.put(
            BASE + "/" + row["id"] + "/visibility", json={"expected_revision": 1, "excluded": True}
        ).status_code
        == 200
    )
    assert row["id"] not in {r["id"] for r in signed_in.get(BASE).json()["items"]}
    assert row["id"] in {
        r["id"] for r in signed_in.get(BASE, params={"include_hidden": True}).json()["items"]
    }


def test_remote_profile_label_does_not_authorize_private_recall(
    ready, database, invocation, monkeypatch
):
    row = note(ready)
    controls(ready, enabled=True)
    with Session(database[0]) as db:
        runtime = {
            "chat": {"profile": "local"},
            "routes": {
                "local": {"provider": "openai_compatible", "base_url": "https://remote.invalid/v1"}
            },
        }
        monkeypatch.setattr(governance, "routing", lambda *args: runtime)
        assert not knowledge.context(db, owner(db), "liquidity", runtime=runtime)
    response = ready.post(
        READ,
        headers=HEADERS,
        json={**invocation, "resource": "knowledge", "resource_id": row["id"]},
    )
    assert response.status_code == 403
    assert "Liquidity hypothesis" not in response.text
    assert ready.get(BASE + "/settings").json()["chat_recall_allowed"] is False
    controls(ready, allow_external_context=True)
    assert (
        ready.post(
            READ,
            headers=HEADERS,
            json={**invocation, "resource": "knowledge", "query": "liquidity"},
        ).json()["items"][0]["id"]
        == row["id"]
    )


def test_local_recall_preferences_portfolio_boundary_and_exclusions(
    ready, database, memory_provider
):
    controls(ready, enabled=True, honcho_enabled=True)
    pref = note(ready, "Explanation preference", "Keep explanations clear.", "preference")
    note(ready)
    with Session(database[0]) as db:
        own = owner(db)
        state = MemorySyncState(
            owner_id=own.id,
            representation="Might prefer careful validation.",
            honcho_selection_hash=honcho_memory.selection_hash(
                db, own.id, knowledge.settings(db, own.id)[0]
            ),
        )
        db.add(state)
        db.commit()
        runtime = {
            "chat": {"profile": "local"},
            "routes": {"local": {"provider": "ollama", "base_url": "http://127.0.0.1:11434"}},
        }
        full = knowledge.context(db, own, "liquidity", runtime=runtime)
        assert full["related_saved_notes"] and full["honcho_inferred_preferences"]
        scoped = knowledge.context(db, own, "liquidity", portfolio_id=str(uuid4()), runtime=runtime)
        assert len(scoped["explicit_owner_preferences"]) == 1
        assert not scoped["related_saved_notes"] and not scoped["honcho_inferred_preferences"]
    controls(ready, record_chats=False)
    assert not ready.get(BASE + "/settings").json()["honcho_representation"]
    ready.put(
        BASE + "/" + pref["id"] + "/visibility", json={"expected_revision": 1, "excluded": True}
    )
    assert not ready.get(BASE, params={"kind": "preference"}).json()["items"]


def test_obsidian_import_export_conflicts_and_symlinks(ready, database, memory_provider, tmp_path):
    controls(ready, enabled=True)
    saved = note(ready)
    with Session(database[0]) as db:
        own = owner(db)
        knowledge.collect(db, own)
        assert knowledge.export_vault(db, own) == 0
        folder = knowledge.vault_root(own.id)
        exported = next((folder / "Notes").glob("*.md"))
        assert "Liquidity hypothesis" in exported.name
        legacy = folder / "Notes" / (saved["id"] + ".md")
        exported.replace(legacy)
        assert knowledge.export_vault(db, own) == 0
        assert exported.exists() and not legacy.exists()
        exported.write_text("My edits must survive.")
        assert knowledge.export_vault(db, own) == 1
        assert exported.read_text() == "My edits must survive."
        (folder / "Inbox" / "research.md").write_text(
            "# Volatility hypothesis\n\nTest volatility across held-out regimes."
        )
        outside = tmp_path / "secret.md"
        outside.write_text("Never import this secret.")
        (folder / "Inbox" / "secret.md").symlink_to(outside)
        (folder / "Inbox" / "too-large.md").write_text("a" * 80001)
        assert knowledge.import_vault(db, own) == 1
        assert knowledge.search(db, own, "volatility").items[0].source_kind == "obsidian"
        assert not knowledge.search(db, own, "secret").items
        with pytest.raises(ValueError):
            knowledge.safe_file(folder, "../secret.md")
        with pytest.raises(ValueError):
            knowledge.safe_file(folder, "Inbox/secret.md")


class LocalHonchoFixture:
    def __init__(self, lost=False):
        self.stored = []
        self.posts = 0
        self.deletes = 0
        self.lost = lost
        self.peers = {}

    def __call__(self, config, method, path, body=None):
        assert config["url"] in honcho_memory.LOCAL_ENDPOINTS
        if method == "DELETE":
            self.stored = []
            self.deletes += 1
            return None
        if path == "/v3/workspaces/list?size=1":
            return {"items": [{"id": body["filters"]["id"]}]}
        if path.endswith("/peers"):
            self.peers[body["id"]] = body["configuration"]
            return body
        if "/sessions/list" in path:
            return {"items": []}
        if "/messages/list" in path:
            tags = body["filters"]["metadata"]
            return {
                "items": [
                    m
                    for m in self.stored
                    if all(m["metadata"].get(k) == v for k, v in tags.items())
                ]
            }
        if path.endswith("/messages"):
            self.posts += 1
            added = [{**m, "id": str(uuid4())} for m in body["messages"]]
            self.stored.extend(added)
            if self.lost:
                self.lost = False
                raise RuntimeError("Response lost after saving")
            return added
        if path.endswith("/representation"):
            return {"representation": "Owner may prefer clear labels."}
        return body


def test_honcho_lost_receipt_reconciles_without_duplicate(
    ready, database, memory_provider, monkeypatch
):
    controls(ready, enabled=True, honcho_enabled=True)
    row = note(ready)
    fixture = LocalHonchoFixture(lost=True)
    monkeypatch.setattr(honcho_memory, "request", fixture)
    with Session(database[0]) as db:
        own = owner(db)
        knowledge.collect(db, own)
        with pytest.raises(RuntimeError):
            honcho_memory.sync(db, own)
        assert db.get(HonchoDelivery, row["id"]).status == "uncertain"
        original = len(fixture.stored)
        honcho_memory.sync(db, own)
        assert fixture.posts == 1 and len(fixture.stored) == original
        assert db.get(HonchoDelivery, row["id"]).status == "sent"
        assert fixture.peers == {
            "owner": {"observe_me": True},
            "assistant": {"observe_me": False},
            "project": {"observe_me": False},
        }
    assert ready.get(BASE + "/settings").json()["honcho_representation"]
    ready.put(
        BASE + "/" + row["id"] + "/visibility", json={"expected_revision": 1, "excluded": True}
    )
    assert not ready.get(BASE + "/settings").json()["honcho_representation"]
    with Session(database[0]) as db:
        honcho_memory.sync(db, owner(db))
    assert fixture.deletes == 1 and not fixture.stored


def test_partial_first_sync_source_change_rebuilds(ready, database, memory_provider, monkeypatch):
    controls(ready, enabled=True, honcho_enabled=True)
    row = note(ready)
    fixture = LocalHonchoFixture(lost=True)
    monkeypatch.setattr(honcho_memory, "request", fixture)
    with Session(database[0]) as db:
        own = owner(db)
        knowledge.collect(db, own)
        with pytest.raises(RuntimeError):
            honcho_memory.sync(db, own)
    ready.put(
        BASE + "/" + row["id"] + "/visibility", json={"expected_revision": 1, "excluded": True}
    )
    with Session(database[0]) as db:
        honcho_memory.sync(db, owner(db))
    assert fixture.deletes == 1 and not fixture.stored


def test_honcho_unapproved_endpoint_and_redirect_rejected(memory_provider):
    value = json.loads(memory_provider.read_text())
    value["url"] = "https://api.honcho.dev"
    memory_provider.write_text(json.dumps(value))
    assert honcho_memory.private_config() is None
    with pytest.raises(ValueError):
        honcho_memory.request(value, "POST", "/v3/workspaces", {})
    assert (
        honcho_memory.NoRedirect().redirect_request(
            None, None, 302, "redirect", {}, "https://external.invalid"
        )
        is None
    )


def test_honcho_retirement_waits_for_deletion_and_uses_a_fresh_workspace(
    ready, database, memory_provider, monkeypatch
):
    controls(ready, enabled=True, honcho_enabled=True)
    row = note(ready)
    fixture = LocalHonchoFixture()
    monkeypatch.setattr(honcho_memory, "request", fixture)
    with Session(database[0]) as db:
        own = owner(db)
        knowledge.collect(db, own)
        honcho_memory.sync(db, own)
        state = db.get(MemorySyncState, own.id)
        obsolete = state.honcho_workspace

    prefix = "/v3/workspaces/" + obsolete
    deletions = []
    removed = False

    def asynchronous_service(config, method, path, body=None):
        if path == "/v3/workspaces/list?size=1":
            assert body == {"filters": {"id": obsolete}}
            return {"items": [] if removed else [{"id": obsolete}]}
        if path == prefix + "/sessions/list?size=100":
            return {"items": [{"id": "old-chat"}]}
        if method == "DELETE":
            deletions.append(path)
            return None
        return fixture(config, method, path, body)

    monkeypatch.setattr(honcho_memory, "request", asynchronous_service)
    ready.put(
        BASE + "/" + row["id"] + "/visibility", json={"expected_revision": 1, "excluded": True}
    )
    with Session(database[0]) as db:
        own = owner(db)
        honcho_memory.sync(db, own)
        state = db.get(MemorySyncState, own.id)
        current = state.honcho_workspace
        assert current != obsolete
        assert state.honcho_retired_workspaces == [{"id": obsolete, "requested": True}]
        assert deletions == [prefix + "/sessions/old-chat", prefix]
        honcho_memory.sync(db, own)
        assert state.honcho_workspace == current
        assert len(deletions) == 2
        removed = True
        honcho_memory.sync(db, own)
        assert not state.honcho_retired_workspaces
        assert state.honcho_workspace == current
        assert len(deletions) == 2


def test_non_utf8_inbox_file_does_not_block_valid_notes(ready, database, memory_provider):
    with Session(database[0]) as db:
        own = owner(db)
        knowledge.collect(db, own)
        knowledge.export_vault(db, own)
        inbox = knowledge.vault_root(own.id) / "Inbox"
        (inbox / "bad.md").write_bytes(b"\xff\xfe\x00")
        (inbox / "valid.md").write_text("# Valid research\n\nTest execution costs.")
        assert knowledge.import_vault(db, own) == 1
        notes = knowledge.search(db, own, query="execution costs").items
        assert len(notes) == 1 and notes[0].title == "Valid research"


def test_obsidian_title_change_preserves_edited_file(ready, database, memory_provider):
    saved = note(ready, title="EUR/USD: breakout idea?")
    with Session(database[0]) as db:
        own = owner(db)
        knowledge.collect(db, own)
        knowledge.export_vault(db, own)
        folder = knowledge.vault_root(own.id) / "Notes"
        exported = next(folder.glob("*.md"))
        assert exported.name.startswith("EUR USD breakout idea -- ")
        exported.write_text("Owner's Obsidian edits.")
        result = ready.put(
            BASE + "/" + saved["id"],
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "title": "Revised hypothesis",
                "body": "Add a counter-evidence test.",
            },
        )
        assert result.status_code == 200
        db.expire_all()
        assert knowledge.export_vault(db, own) == 1
        assert exported.read_text() == "Owner's Obsidian edits."
        assert list(folder.glob("*.md")) == [exported]


def test_worker_private_tokens_not_exposed(ready, memory_provider):
    value = controls(ready, enabled=True, honcho_enabled=True)
    assert value["honcho_configured"]
    assert "fixture-local-memory-token" not in json.dumps(value)
    assert "fixture-local-memory-token" not in ready.get(BASE).text


def test_chat_survives_optional_memory_failure(ready, database, monkeypatch):
    import asyncio

    from test_chat import send

    result = send(ready).json()
    with Session(database[0]) as db:
        claimed = chat.claim(db)

    def fail_memory(*args):
        raise RuntimeError("Optional memory unavailable")

    async def gateway(*args):
        return ("Reply remains available.", "fixture-model")

    monkeypatch.setattr(knowledge, "context", fail_memory)
    monkeypatch.setattr(chat_worker, "gateway_call", gateway)
    asyncio.run(chat_worker.process(*claimed))
    # Read the durable turn rather than depending on a UI polling route.
    with Session(database[0]) as db:
        turn = db.get(ChatTurn, result["id"])
        assert turn.status == "succeeded" and turn.assistant_text == "Reply remains available."
