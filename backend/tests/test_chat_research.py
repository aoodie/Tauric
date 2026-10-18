"""Chat admission uses trusted identities and the original immutable native queue."""

import copy
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_chat import config, send

from tauric import analysis, chat, chat_research
from tauric.models import AnalysisJob, ChatResearch, ChatTurn, Instrument, Owner, WorkerStatus, now
from tauric.native_runner import artifact

HEADERS = {"Authorization": "Bearer fixture-private-" + "x" * 50}
PATH = "/internal/openclaw/research"
pytest_plugins = ["test_chat"]


@pytest.fixture
def invocation(ready, database):
    turn = send(ready, "Run daily BTC research").json()
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
        chat.claim(db)
        owner = db.scalar(select(Owner))
        return {
            "instrument_id": "binance:spot:BTCUSDT",
            "channel": "webchat",
            "agent_id": "tauric-chat",
            "session_key": f"agent:tauric-chat:openai-user:tauric-web-{owner.id}-{turn['conversation_id']}",
            "sender_is_owner": True,
            "account_id": "default",
            "tool_call_id": "call-" + uuid4().hex,
        }


def request(client, body):
    return client.post(PATH, headers=HEADERS, json=body)


def counts(database):
    with Session(database[0]) as db:
        return tuple(
            db.scalar(select(func.count()).select_from(m)) for m in (AnalysisJob, ChatResearch)
        )


@pytest.mark.parametrize(
    "change,status",
    [
        ({"sender_is_owner": False}, 403),
        ({"sender_is_owner": "true"}, 422),
        ({"agent_id": "governor-local"}, 422),
        ({"channel": "cron"}, 422),
        ({"account_id": "other"}, 403),
        ({"session_key": "agent:tauric-chat:telegram:default:group:12345678"}, 403),
        ({"session_key": "agent:tauric-chat:openai-user:tauric-web-other-owner"}, 403),
        ({"instrument_id": "oanda:spot:EURUSD"}, 404),
        ({"instrument_id": "oanda:fx:XAU_USD"}, 404),
        ({"instrument_id": "x" * 101}, 422),
        ({"scope": "weekly"}, 422),
        ({"profile": "cloud"}, 422),
        ({"trade_date": "2020-01-01"}, 422),
    ],
)
def test_rejects_untrusted_identity_and_scope(ready, database, invocation, change, status):
    assert request(ready, {**invocation, **change}).status_code == status
    assert counts(database) == (0, 0)


def test_needs_private_auth_and_active_lease(ready, database, invocation):
    assert ready.post(PATH, json=invocation).status_code == 401
    with Session(database[0]) as db:
        turn = db.scalar(select(ChatTurn))
        turn.heartbeat_at = now() - timedelta(seconds=50)
        db.commit()
    assert request(ready, invocation).status_code == 409
    assert counts(database) == (0, 0)


@pytest.mark.parametrize("instrument_id", ["alpaca:equity:AAPL", "alpaca:etf:SPY"])
def test_chat_requests_registered_stock_and_etf_research(
    ready, database, invocation, instrument_id
):
    response = request(ready, {**invocation, "instrument_id": instrument_id})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["job"]["instrument_id"] == instrument_id
    assert result["execution_authority"] is False
    assert counts(database) == (1, 1)
    repeated = request(ready, {**invocation, "instrument_id": instrument_id}).json()
    assert repeated["reused"] and repeated["job"]["id"] == result["job"]["id"]
    assert counts(database) == (1, 1)


def test_chat_capabilities_follow_registry_and_admit_new_stock(ready, database, invocation):
    with Session(database[0]) as db:
        definition = copy.deepcopy(db.get(Instrument, "alpaca:equity:AAPL").definition)
        definition.update(
            id="alpaca:equity:NVDA",
            symbol="NVDA",
            name="NVIDIA",
            provider_symbol="NVDA",
            base_currency="NVDA",
            native_symbol="NVDA",
        )
        db.add(Instrument(id=definition["id"], definition=definition))
        db.commit()
    context = ready.get("/internal/openclaw/chat-context", headers=HEADERS).json()
    capabilities = context["research_capabilities"]
    rows = {row["id"]: row for row in capabilities["registered_instruments"]}
    assert capabilities["registered_count"] == 10 and not capabilities["registry_truncated"]
    assert rows[definition["id"]]["chat_request_enabled"]
    assert rows["oanda:fx:EUR_USD"]["daily_native_research"]
    assert rows["oanda:fx:EUR_USD"]["chat_request_enabled"]
    assert "weekly" in capabilities["unsupported_native_scopes"]
    assert "commodities" in capabilities["unsupported_native_scopes"]
    assert capabilities["open_research"]["enabled"]
    assert capabilities["open_research"]["registration_required"] is False
    assert capabilities["open_research"]["native_report_created"] is False
    assert "crypto research only" not in context["research_scope"]
    assert counts(database) == (0, 0)
    response = request(ready, {**invocation, "instrument_id": definition["id"]})
    assert response.status_code == 202, response.text
    assert counts(database) == (1, 1)


def test_repeat_calls_and_cross_channel_reuse_share_budget(ready, database, invocation):
    body = config()
    body["daily_research_allowance"] = 1
    assert ready.put("/api/v1/chat/settings", json=body).status_code == 200
    first = request(ready, invocation)
    assert first.status_code == 202, first.text
    first = first.json()
    assert not first["reused"] and first["execution_authority"] is False
    assert first["job"]["request"]["chat_context"]["purpose"] == "daily_research_only"
    assert request(ready, invocation).json()["job"]["id"] == first["job"]["id"]
    for channel in ("telegram", "discord"):
        owner_id = body[channel]["owner_id"]
        native = {
            **invocation,
            "channel": channel,
            "session_key": f"agent:tauric-chat:{channel}:default:direct:{owner_id}",
            "sender_id": channel + ":" + owner_id,
            "tool_call_id": uuid4().hex,
        }
        reused = request(ready, native)
        assert reused.status_code == 202, reused.text
        assert reused.json()["reused"] and reused.json()["job"]["id"] == first["job"]["id"]
        assert request(ready, {**native, "sender_id": "999"}).status_code == 403
        assert (
            request(
                ready,
                {**native, "session_key": f"agent:tauric-chat:{channel}:default:group:{owner_id}"},
            ).status_code
            == 403
        )
    assert counts(database) == (1, 3)
    settings = ready.get("/api/v1/chat/settings").json()
    assert settings["research_used_today"] == 1
    assert (
        request(ready, {**invocation, "instrument_id": "binance:spot:ETHUSDT"}).status_code == 409
    )
    other = {**invocation, "instrument_id": "binance:spot:ETHUSDT", "tool_call_id": uuid4().hex}
    assert request(ready, other).status_code == 429
    # Config revisions cannot reset usage; zero still permits reuse, never new work.
    body["expected_revision"] = 1
    body["daily_research_allowance"] = 0
    for channel in ("telegram", "discord"):
        body[channel].update(token_action="keep", token="")
    assert ready.put("/api/v1/chat/settings", json=body).status_code == 200
    assert request(ready, other).status_code == 429
    assert request(ready, {**invocation, "tool_call_id": uuid4().hex}).json()["reused"]


@pytest.mark.parametrize("fault,code", [("worker", 503), ("queue", 429), ("model", 422)])
def test_failed_admission_never_charges(
    ready, database, invocation, monkeypatch, tmp_path, fault, code
):
    if fault == "model":
        from tauric.config import runtime_config

        path = tmp_path / "empty-profiles.json"
        path.write_text("{}")
        monkeypatch.setenv("TAURIC_MODEL_PROFILES_FILE", str(path))
        runtime_config.cache_clear()
    else:
        with Session(database[0]) as db:
            if fault == "worker":
                db.get(WorkerStatus, "native").heartbeat_at = now() - timedelta(seconds=60)
            else:
                from tauric.market_contracts import AnalysisRequest

                owner = db.scalar(select(Owner))
                for _ in range(4):
                    analysis.enqueue(
                        db,
                        owner,
                        AnalysisRequest(
                            request_id=uuid4(),
                            instrument_id="binance:spot:ETHUSDT",
                            profile="local",
                        ),
                    )
            db.commit()
    assert request(ready, invocation).status_code == code
    assert counts(database)[1] == 0


def test_original_report_links_immutable_history_and_no_retries(ready, database, invocation):
    first = request(ready, invocation).json()
    job_id = first["job"]["id"]
    with Session(database[0]) as db:
        assert analysis.claim(db) == job_id
        job = db.get(AnalysisJob, job_id)
        saved_request = copy.deepcopy(job.request)
        chat_research.validate_admission(db, job)
        result = artifact(
            {"market_report": "Original text exactly", "final_trade_decision": "Original Hold"},
            "Hold",
            job.request,
            {"llm_provider": "openai_compatible"},
            "0.5.2",
        )
        assert analysis.finish(db, job_id, result)
    status = ready.get(PATH + "/" + job_id, headers=HEADERS).json()
    assert status["job"]["status"] == "succeeded"
    report = ready.get("/api/v1/analysis-artifacts/" + status["original_report"]["id"]).json()
    assert report["payload"]["reports"]["market_report"] == "Original text exactly"
    assert report["payload"]["execution_authority"] is False
    with Session(database[0]) as db:
        turn = db.scalar(select(ChatTurn))
        view = chat.view(db, turn)
        assert len(view.research_jobs) == 1 and view.research_jobs[0].artifact_id == report["id"]
        assert db.get(AnalysisJob, job_id).request == saved_request
    assert request(ready, {**invocation, "tool_call_id": uuid4().hex}).json()["reused"]
    for sql in (
        "UPDATE chat_research SET charged=false",
        "DELETE FROM chat_research",
        "TRUNCATE chat_research CASCADE",
    ):
        with pytest.raises(DBAPIError), database[1].begin() as conn:
            conn.execute(text(sql))
    assert counts(database) == (1, 2)


@pytest.mark.parametrize("state", ["failed", "interrupted"])
def test_failed_runs_are_reused_without_hidden_model_retries(ready, database, invocation, state):
    job_id = request(ready, invocation).json()["job"]["id"]
    with Session(database[0]) as db:
        analysis.claim(db)
        if state == "failed":
            analysis.finish(db, job_id, error="Offline fixture failure")
        else:
            db.get(AnalysisJob, job_id).heartbeat_at = now() - timedelta(seconds=60)
            db.commit()
            analysis.claim(db)
    again = request(ready, {**invocation, "tool_call_id": uuid4().hex}).json()
    assert again["reused"] and again["job"]["id"] == job_id and again["job"]["status"] == state
    assert counts(database) == (1, 2)


def test_disabled_native_channel_denied(ready, database, invocation):
    native = {
        **invocation,
        "channel": "telegram",
        "sender_id": "12345678",
        "session_key": "agent:tauric-chat:telegram:default:direct:12345678",
    }
    assert request(ready, native).status_code == 403
    assert counts(database) == (0, 0)


def test_connection_revision_change_rejects_before_execution(
    ready, database, invocation, monkeypatch
):
    job_id = request(ready, invocation).json()["job"]["id"]
    with Session(database[0]) as db:
        job = db.get(AnalysisJob, job_id)
        chat_research.validate_admission(db, job)
        monkeypatch.setattr(
            chat_research, "connection_head", lambda *_: SimpleNamespace(revision=99)
        )
        with pytest.raises(ValueError, match="connection changed"):
            chat_research.validate_admission(db, job)


def test_allowance_uses_utc_midnight(ready, database, invocation, monkeypatch):
    assert request(ready, invocation).status_code == 202
    instant = now()
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        assert chat_research.charged_today(db, owner) == 1
        monkeypatch.setattr(chat_research, "now", lambda: instant + timedelta(days=1))
        assert chat_research.charged_today(db, owner) == 0


@pytest.mark.parametrize("pair", ["EUR_USD", "NZD_USD", "AUD_CAD", "SEK_NOK"])
def test_owner_can_queue_any_registered_fx_pair_with_bound_daily_evidence(
    ready, database, invocation, monkeypatch, pair
):
    from test_forex_macro import HTML, feed
    from test_native_fx import daily_fixture

    from tauric import forex_macro, forex_sources, market

    instrument_id = "oanda:fx:" + pair
    with Session(database[0]) as db:
        definition = copy.deepcopy(db.get(Instrument, "oanda:fx:EUR_USD").definition)
        base, quote = pair.split("_")
        definition.update(
            id=instrument_id,
            symbol=base + "/" + quote,
            provider_symbol=pair,
            base_currency=base,
            quote_currency=quote,
            native_symbol=base + quote + "=X",
        )
        if not db.get(Instrument, instrument_id):
            db.add(Instrument(id=instrument_id, definition=definition))
            db.commit()
    monkeypatch.setattr(
        market.PROVIDERS["oanda_v20"],
        "collect",
        lambda instrument, interval, **kwargs: daily_fixture(instrument),
    )

    def primary(client, url):
        for currency, (_, _, news, calendar) in forex_macro.SOURCES.items():
            if url == news:
                return feed(currency, published=now() - timedelta(days=1))
            if url == calendar:
                return HTML[currency]
        raise AssertionError("Unknown currency must not construct an external URL")

    monkeypatch.setattr(forex_macro, "_fetch", primary)
    # No configured supplemental credentials: explicit unavailable receipts, no external calls.
    response = request(ready, {**invocation, "instrument_id": instrument_id})
    assert response.status_code == 202, response.text
    result = response.json()
    assert result["execution_authority"] is False and not result["reused"]
    binding = result["job"]["request"]["forex_context"]
    context = forex_macro.validate(binding["macro_snapshot"], definition)
    forex_sources.validate(binding["supplemental_snapshot"], definition)
    assert context["currencies"] == [base, quote]
    assert binding["instrument_id"] == instrument_id and binding["interval"] == "1d"
    assert result["job"]["request"]["chat_context"]["purpose"] == "daily_research_only"
    for row in context["sources"]:
        if row["currency"] not in forex_macro.SOURCES:
            assert row["status"] == "unavailable" and row["items"] == []
    repeated = request(ready, {**invocation, "instrument_id": instrument_id}).json()
    assert repeated["reused"] and repeated["job"]["id"] == result["job"]["id"]
    assert counts(database) == (1, 1)
    capabilities = ready.get("/internal/openclaw/chat-context", headers=HEADERS).json()[
        "research_capabilities"
    ]
    assert next(
        row for row in capabilities["registered_instruments"] if row["id"] == instrument_id
    )["chat_request_enabled"]
