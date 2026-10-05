"""Owner boundaries and durable delivery, with offline model/channel fixtures."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from tauric import chat, governance
from tauric.config import runtime_config
from tauric.models import AuditEvent, ChatConfigVersion, ChatTurn, GatewayStatus, Owner, now


@pytest.fixture
def ready(signed_in, database, monkeypatch, tmp_path):
    tokens = {k: "fixture-private-" + "x" * 50 for k in ("platform_token", "gateway_token")}
    secret = tmp_path / "private.json"
    secret.write_text(json.dumps(tokens))
    profiles = tmp_path / "profiles.json"
    profiles.write_text(
        json.dumps(
            {
                "local": {
                    "provider": "openai_compatible",
                    "base_url": "http://127.0.0.1:9/v1",
                    "quick_model": "fixture-model",
                    "deep_model": "fixture-model",
                    "api_key": "fixture-model-key",
                }
            }
        )
    )
    monkeypatch.setenv("TAURIC_OPENCLAW_PRIVATE_FILE", str(secret))
    monkeypatch.setenv("TAURIC_MODEL_PROFILES_FILE", str(profiles))
    runtime_config.cache_clear()
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        db.add(
            GatewayStatus(
                id="openclaw",
                fingerprint=governance.routing(db, owner)["fingerprint"],
                heartbeat_at=now(),
            )
        )
        db.commit()
        chat.heartbeat(db)
    return signed_in


def send(client, text="Hello OpenClaw", conversation=None, request_id=None):
    return client.post(
        "/api/v1/chat/messages",
        json={
            "request_id": request_id or str(uuid4()),
            "conversation_id": conversation or str(uuid4()),
            "text": text,
        },
    )


def config(revision=0):
    return {
        "expected_revision": revision,
        "profile": "local",
        "telegram": {
            "enabled": True,
            "owner_id": "12345678",
            "token_action": "replace",
            "token": "fixture-telegram-secret",
        },
        "discord": {
            "enabled": True,
            "owner_id": "123456789012345678",
            "token_action": "replace",
            "token": "fixture-discord-secret",
        },
    }


def test_owner_and_csrf(client, ready):
    client.headers.pop("X-CSRF-Token")
    assert send(client).status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/chat/settings").status_code == 401
    assert client.get("/api/v1/chat/conversations").status_code == 401
    assert client.get("/internal/openclaw/chat-context").status_code == 401


def test_encrypted_tokens_masked_and_cas(ready, database):
    response = ready.put("/api/v1/chat/settings", json=config())
    assert response.status_code == 200
    assert response.json()["telegram"]["has_token"]
    assert not response.json()["gateway_ready"]
    assert "fixture-telegram-secret" not in response.text
    assert ready.put("/api/v1/chat/settings", json=config()).status_code == 409
    with Session(database[0]) as db:
        saved = db.scalar(select(ChatConfigVersion))
        assert "fixture-telegram-secret" not in saved.encrypted_config
        value, revision = chat.load_config(db)
        assert revision == 1 and value.telegram.token == "fixture-telegram-secret"
        assert "fixture-telegram-secret" not in json.dumps(
            [a.details for a in db.scalars(select(AuditEvent))]
        )
    body = config(1)
    for key in ("telegram", "discord"):
        body[key].update(token_action="keep", token="")
    assert ready.put("/api/v1/chat/settings", json=body).status_code == 200
    body["expected_revision"] = 2
    body["telegram"].update(enabled=False, token_action="clear")
    assert ready.put("/api/v1/chat/settings", json=body).json()["telegram"]["has_token"] is False


@pytest.mark.parametrize("owner_id", ["*", "@user", "0", "-1", "１２３"])
def test_no_public_or_ambiguous_owner_ids(ready, owner_id):
    body = config()
    body["telegram"]["owner_id"] = owner_id
    assert ready.put("/api/v1/chat/settings", json=body).status_code == 422


def test_enabled_needs_token_and_owner(ready):
    body = config()
    body["telegram"].update(token_action="keep", token="")
    assert ready.put("/api/v1/chat/settings", json=body).status_code == 422


def test_idempotency_conversation_queue_and_history(ready):
    request_id, conversation = str(uuid4()), str(uuid4())
    first = send(ready, conversation=conversation, request_id=request_id)
    assert first.status_code == 202
    assert (
        send(ready, conversation=conversation, request_id=request_id).json()["id"]
        == first.json()["id"]
    )
    assert send(ready, "Changed text", conversation, request_id).status_code == 409
    assert send(ready, conversation=conversation).status_code == 409
    for _ in range(3):
        assert send(ready).status_code == 202
    assert send(ready).status_code == 429
    assert ready.get("/api/v1/chat/conversations").json()[0]["title"] == "Hello OpenClaw"
    assert len(ready.get("/api/v1/chat/conversations/" + conversation).json()) == 1
    assert ready.get("/api/v1/chat/conversations/" + str(uuid4())).json() == []


def test_worker_success_and_lease_fencing(ready, database, monkeypatch):
    from tauric import chat_worker

    row = send(ready).json()
    calls = []

    async def fake(body, timeout):
        calls.append(body)
        assert body["model"] == "openclaw:tauric-chat"
        assert body["messages"][-1]["content"] == "Hello OpenClaw"
        instructions = body["messages"][0]["content"]
        assert '"daily_native_research": true' in instructions
        assert '"forex_policy_currencies": ["EUR", "GBP", "JPY", "USD"]' in instructions
        assert (
            '"unsupported_scopes": ["commodities", "weekly", "historical", "intraday", "futures"]'
            in instructions
        )
        assert (
            "Do not run that alternative without the owner explicitly requesting it" in instructions
        )
        return "Hello from offline fixture", {"total_tokens": 123}

    monkeypatch.setattr(chat_worker, "gateway_call", fake)
    with Session(database[0]) as db:
        claimed = chat.claim(db)
        assert chat.claim(db) is None
        assert not chat.finish(db, row["id"], "wrong-lease", result=("Invalid", {}))
    asyncio.run(chat_worker.process(*claimed))
    with Session(database[0]) as db:
        turn = db.get(ChatTurn, row["id"])
        assert turn.status == "succeeded" and turn.assistant_text == "Hello from offline fixture"
        assert not chat.finish(db, *claimed, result=("Overwritten", {}))
    assert len(calls) == 1


def test_changed_connection_never_calls_model(ready, database, monkeypatch):
    from tauric import chat_worker

    row = send(ready).json()
    assert ready.put("/api/v1/chat/settings", json=config()).status_code == 200

    calls = []

    async def forbidden(*_):
        calls.append(True)
        raise AssertionError("Should not call a model")

    monkeypatch.setattr(chat_worker, "gateway_call", forbidden)
    with Session(database[0]) as db:
        claimed = chat.claim(db)
    asyncio.run(chat_worker.process(*claimed))
    with Session(database[0]) as db:
        assert db.get(ChatTurn, row["id"]).status == "failed"
    assert calls == []


def test_lost_lease_is_interrupted_without_replay(ready, database):
    row = send(ready).json()
    with Session(database[0]) as db:
        claimed = chat.claim(db)
        turn = db.get(ChatTurn, row["id"])
        turn.heartbeat_at = now() - timedelta(seconds=50)
        db.commit()
        assert not chat.renew(db, *claimed)
        assert chat.claim(db) is None
        assert db.get(ChatTurn, row["id"]).status == "interrupted"


def test_private_status_is_read_only_and_contains_no_secrets(ready):
    headers = {"Authorization": "Bearer fixture-private-" + "x" * 50}
    response = ready.get("/internal/openclaw/chat-context", headers=headers)
    assert response.status_code == 200
    assert response.json()["authority"] == "read_only"
    assert response.json()["live_execution_enabled"] is False
    assert "fixture-model-key" not in response.text
    assert (
        ready.get(
            "/internal/openclaw/chat-context?report_id=" + str(uuid4()), headers=headers
        ).status_code
        == 404
    )
