"""Regression cases from the audit, with offline brokers and disposable storage."""

from datetime import datetime, timedelta
from types import SimpleNamespace as Object
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.requests import Request
from test_broker_execution import create
from test_broker_positions import LifecycleBroker
from test_broker_positions import lifecycle as lifecycle
from test_broker_reprotection import protected as protected
from test_market_adapters import bar, dt

from tauric import agent_tasks, auth, broker_execution, paper, strategy_rules, valuation
from tauric.agent_models import AgentTask, AgentTaskWake
from tauric.broker_contracts import BrokerQuote
from tauric.broker_models import BrokerCloseRequest
from tauric.config import runtime_config
from tauric.market_calendars import default_context, fx_sessions, periods
from tauric.market_contracts import Candle
from tauric.models import ChatTurn, Owner, now
from tauric.strategy_contracts import BreakoutRules


def test_two_entries_cannot_each_claim_the_same_netted_shares(signed_in, lifecycle):
    lifecycle.external = 0
    entries = [create(signed_in, quantity="10")[1] for _ in range(2)]
    lifecycle.holding_override = 10
    for entry in entries:
        response = signed_in.post(f"/api/v1/brokers/orders/{entry['id']}/position")
        assert response.status_code == 200, response.text
        assert response.json()["remaining_quantity"] == "10"
        assert response.json()["holding_check"] == "shortfall"
        response = signed_in.post(
            f"/api/v1/brokers/orders/{entry['id']}/close",
            json={"request_id": str(uuid4()), "quantity": "1", "reason": "Check attribution"},
        )
        assert response.status_code == 409
    assert len(lifecycle.submissions) == 2


@pytest.mark.parametrize("lost_response", [False, True])
def test_entry_protection_repair_needs_no_close_and_never_repeats(
    signed_in, protected, database, lost_response
):
    broker, entry_id = protected
    for child in broker.children:
        child["status"] = "expired"
    broker.protect_timeout = lost_response
    body = {"request_id": str(uuid4()), "reason": "Restore original expired protection"}
    path = f"/api/v1/brokers/orders/{entry_id}/repair-protection"
    response = signed_in.post(path, json=body)
    assert response.status_code == (409 if lost_response else 200), response.text
    response = signed_in.post(path, json=body)
    assert response.status_code == 200, response.text
    assert response.json()["protection_status"] == "covered"
    assert len(broker.protect_calls) == 1
    assert broker.protect_calls[0]["time_in_force"] == "gtc"
    assert broker.protect_calls[0]["stop_loss"]["stop_price"] == "140"
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(BrokerCloseRequest)) == 0


def test_entry_repair_does_not_duplicate_held_protection(signed_in, protected):
    broker, entry_id = protected
    broker.children[0]["status"] = "held"
    response = signed_in.post(
        f"/api/v1/brokers/orders/{entry_id}/repair-protection",
        json={"request_id": str(uuid4()), "reason": "Inspect uncertain protection"},
    )
    assert response.status_code == 409
    assert broker.protect_calls == []


def test_successful_chat_without_task_transition_consumes_recovery_budget(database):
    from test_agent_tasks import create as create_task

    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = create_task(db, owner, max_attempts=2)
        row = db.get(AgentTask, task["id"])
        for attempt in (1, 2):
            row.status = "running"
            turn = ChatTurn(
                id=str(uuid4()),
                owner_id=owner.id,
                request_id=str(uuid4()),
                conversation_id=row.conversation_id,
                user_text="Continue existing work",
                status="succeeded",
                profile="local",
                routing_fingerprint="a" * 64,
            )
            db.add(turn)
            db.flush()
            db.add(AgentTaskWake(turn_id=turn.id, task_id=row.id, task_revision=row.revision))
            db.commit()
            stamp = now()
            assert agent_tasks.tick(db)
            assert row.attempts == attempt
            if attempt == 1:
                assert row.status == "retry_scheduled"
                assert row.next_check_at >= stamp + timedelta(seconds=20)
            else:
                assert row.status == "requires_owner" and row.next_check_at is None


@pytest.mark.parametrize(
    "peer,forwarded,expected",
    [
        ("172.20.0.4", "203.0.113.2", "203.0.113.2"),
        ("172.20.0.5", "203.0.113.2", "172.20.0.5"),
        ("172.20.0.4", "203.0.113.2, 203.0.113.3", "172.20.0.4"),
    ],
)
def test_client_address_requires_the_actual_configured_proxy(
    monkeypatch, peer, forwarded, expected
):
    monkeypatch.setattr(auth, "runtime_config", lambda: Object(trusted_proxy_hosts=["web"]))
    monkeypatch.setattr(
        auth.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("172.20.0.4", 0))]
    )
    request = Request(
        {
            "type": "http",
            "client": (peer, 1234),
            "headers": [
                (b"x-tauric-client-ip", forwarded.encode()),
                (b"x-forwarded-for", b"192.0.2.1"),
            ],
        }
    )
    assert auth.client_ip(request) == expected


def test_proxy_visitors_have_independent_persistent_login_throttles(client, monkeypatch):
    monkeypatch.setenv("TAURIC_TRUSTED_PROXY_HOSTS", '["web"]')
    runtime_config.cache_clear()
    monkeypatch.setattr(
        auth.socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("172.20.0.4", 0))]
    )
    with TestClient(
        client.app, headers={"Origin": "http://testserver"}, client=("172.20.0.4", 1234)
    ) as proxy:
        for _ in range(5):
            response = proxy.post(
                "/api/v1/session",
                headers={"X-Tauric-Client-IP": "203.0.113.1"},
                json={"username": "unknown", "password": "wrong"},
            )
            assert response.status_code == 401
        response = proxy.post(
            "/api/v1/session",
            headers={"X-Tauric-Client-IP": "203.0.113.1"},
            json={"username": "unknown", "password": "wrong"},
        )
        assert response.status_code == 429
        response = proxy.post(
            "/api/v1/session",
            headers={"X-Tauric-Client-IP": "203.0.113.2"},
            json={"username": "test-owner", "password": "Test-only-password-2026"},
        )
        assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "symbol,asset", [("XAU_USD", "commodity"), ("WTICO_USD", "cfd"), ("SPX500_USD", "cfd")]
)
def test_known_product_sessions_preserve_weekends_and_detect_real_missing_bars(symbol, asset):
    from tauric.data_quality import verify

    context = default_context(Object(asset_type=asset, provider_symbol=symbol))
    assert context.calendar_id == "oanda_product"
    opening, ending = dt("2026-10-15T04:00"), dt("2026-10-19T12:00")
    bars = [bar(op, close, sides=True) for op, close in periods(context, "1h", opening, ending)]
    assert verify(bars, "1h", context, ending + timedelta(hours=1)).missing_bars == 0
    rules = BreakoutRules(
        engine="closed_bar_breakout_v1",
        interval="1h",
        entry_lookback=20,
        exit_lookback=10,
        minimum_volume_ratio="1",
        take_profit_r="2",
        signal_ttl_seconds=600,
    )
    result = strategy_rules.evaluate_closed_bars(
        [Candle.model_validate(b) for b in bars],
        rules,
        data_context=context.model_dump(mode="json"),
    )
    assert result["action"] != "blocked", result
    assert verify(bars[:3] + bars[4:], "1h", context, ending + timedelta(hours=1)).missing_bars == 1


@pytest.mark.parametrize(
    "session,stale_nav,eligible",
    [(False, False, True), (True, False, False), (None, False, False), (False, True, False)],
)
def test_closed_market_keeps_nav_coverage_without_inventing_exposure_marks(
    monkeypatch, session, stale_nav, eligible
):
    broker = LifecycleBroker()
    broker.external = 10
    broker.quote = lambda name: BrokerQuote(
        symbol=name, bid="149", ask="150", observed_at=now(), tradable=False
    )
    broker.session_open = lambda name: session
    if stale_nav:
        account = broker.account()
        broker.account = lambda: account.model_copy(
            update={"observed_at": now() - timedelta(minutes=2)}
        )
    monkeypatch.setattr(
        broker_execution, "stored_connection", lambda *a: (Object(environment="paper"), 1, None)
    )
    monkeypatch.setattr(broker_execution, "adapter", lambda *a: broker)
    account = valuation.broker_account(Object(scalars=lambda *a: []), Object(id="owner"), "alpaca")
    assert account["complete"] is False
    assert account["observation_complete"] is eligible
    assert account["positions"][0].get("mark") is None
    payload = {"accounts": [account]}
    row = Object(payload=payload, checksum=paper.checksum(payload), created_at=now())
    db = Object(scalars=lambda *a: [row])
    assert valuation.history(db, Object(id="owner"), account["key"]) == []
    assert len(
        valuation.history(db, Object(id="owner"), account["key"], include_closed=True)
    ) == int(eligible)


@pytest.mark.parametrize("fingerprint,expected", [("a" * 64, "20"), ("b" * 64, "0")])
def test_drawdown_continues_across_credentials_but_never_across_accounts(
    monkeypatch, fingerprint, expected
):
    identity = {
        "provider": "alpaca",
        "mode": "paper",
        "currency": "USD",
        "account_fingerprint": "a" * 64,
    }
    previous = Object(
        payload={
            "accounts": [
                {
                    **identity,
                    "key": "alpaca:paper:1",
                    "marked_peak_equity": "10000",
                    "marked_max_drawdown_percent": "20",
                    "marked_max_drawdown_amount": "2000",
                }
            ]
        }
    )
    previous.checksum = paper.checksum(previous.payload)
    secret = Object(get_secret_value=lambda: "offline-fixture")
    monkeypatch.setattr(
        broker_execution,
        "stored_connection",
        lambda db, provider: (
            Object(token=secret) if provider == "oanda" else Object(key=secret, secret=secret),
            0 if provider == "oanda" else 2,
            None,
        ),
    )
    monkeypatch.setattr(
        valuation,
        "broker_account",
        lambda *a: {
            **identity,
            "account_fingerprint": fingerprint,
            "key": "alpaca:paper:2",
            "complete": True,
            "equity": "8000",
            "gross_exposure": "0",
        },
    )
    monkeypatch.setattr(
        valuation, "append", lambda db, owner, kind, request, payload: Object(payload=payload)
    )
    db = Object(
        scalar=Mock(side_effect=[None, previous, None]), scalars=lambda *a: [], commit=lambda: None
    )
    result = valuation.capture(db, Object(id="owner"), Object(request_id=uuid4()))
    assert result.payload["accounts"][0]["marked_max_drawdown_percent"] == expected


def test_two_weekends_keep_coverage_but_a_six_minute_observation_outage_fails():
    start = datetime.fromisoformat("2026-10-05T00:00:00+00:00")
    rows = []
    for minute in range(14 * 24 * 60 + 1):
        stamp = start + timedelta(minutes=minute)
        opened = any(op <= stamp < close for op, close in fx_sessions(stamp, stamp))
        payload = {
            "accounts": [
                {
                    "key": "oanda:practice:1",
                    "complete": opened,
                    "observation_complete": True,
                    "equity": "10000",
                }
            ]
        }
        rows.append(Object(created_at=stamp, payload=payload, checksum=paper.checksum(payload)))
    db = Object(scalars=lambda *a: iter(rows))
    observations = valuation.history(
        db, Object(id="owner"), "oanda:practice:1", include_closed=True
    )
    assert valuation.coverage_complete(observations)
    assert not valuation.coverage_complete(
        valuation.history(db, Object(id="owner"), "oanda:practice:1")
    )
    del rows[1000:1006]
    assert not valuation.coverage_complete(
        valuation.history(db, Object(id="owner"), "oanda:practice:1", include_closed=True)
    )
