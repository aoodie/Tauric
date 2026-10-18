"""Offline promotion boundaries; synthetic evidence never enables a real account."""

import copy
import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_broker_review import complete, queue, submit
from test_broker_review import configured as configured
from test_broker_review import lifecycle as lifecycle
from test_broker_review import ready as ready
from test_broker_review import setup as setup
from test_paper import buy
from test_paper import setup as paper_setup_fixture

from tauric import broker_execution, live_trading, valuation
from tauric.live_models import TradingReadinessRecord
from tauric.models import MarketSnapshot, Owner, WorkerStatus, now


@pytest.fixture
def paper_setup(signed_in, database):
    return paper_setup_fixture.__wrapped__(signed_in, database)


def mark(database, value):
    with Session(database[0]) as db:
        prior = db.scalar(select(MarketSnapshot).order_by(MarketSnapshot.received_at.desc()))
        data = copy.deepcopy(prior.data)
        data["candles"][-1].update(
            open=str(value), close=str(value), high=str(value + 1), low=str(value - 1)
        )
        db.add(
            MarketSnapshot(
                instrument_id=prior.instrument_id,
                interval="1m",
                received_at=now(),
                provider_time=now(),
                data=data,
                checksum=hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest(),
            )
        )
        db.commit()


def observe(client, request_id=None):
    body = {"request_id": request_id or str(uuid4()), "reason": "Observe verified portfolio marks"}
    response = client.post("/api/v1/trading-readiness/valuation", json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def test_readiness_state_reports_live_authority_and_current_observation_workers(
    signed_in, database, monkeypatch
):
    stamp = now()
    monkeypatch.setattr(live_trading, "now", lambda: stamp)
    initial = signed_in.get("/api/v1/trading-readiness")
    assert initial.status_code == 200, initial.text
    assert (
        not initial.json()["valuation_worker_ready"] and not initial.json()["forward_worker_ready"]
    )
    assert all(b["native_required"] and not b["live_enabled"] for b in initial.json()["brokers"])
    with Session(database[0]) as db:
        db.add(WorkerStatus(id="valuation", heartbeat_at=stamp, version="0.12.0"))
        db.add(
            WorkerStatus(
                id="forward_validation",
                heartbeat_at=stamp - timedelta(seconds=61),
                version="0.12.0",
            )
        )
        db.commit()
    state = signed_in.get("/api/v1/trading-readiness").json()
    assert state["valuation_worker_ready"] and not state["forward_worker_ready"]


def test_marked_equity_includes_open_loss_and_entry_fee(signed_in, paper_setup, database):
    account, deployment, *_ = paper_setup
    fill = buy(signed_in, deployment)["payload"]
    mark(database, 95)
    snapshot, body = observe(signed_in)
    result = next(a for a in snapshot["payload"]["accounts"] if a["account_id"] == account["id"])
    q, price, fee = map(Decimal, (fill["quantity"], fill["price"], fill["fee"]))
    expected = q * (Decimal(95) - price) - fee
    assert Decimal(result["unrealized_pnl"]) == expected
    assert Decimal(result["equity"]) == Decimal(account["starting_balance"]) + expected
    assert Decimal(result["realized_pnl"]) == 0 and Decimal(result["fees"]) == fee
    assert Decimal(result["marked_max_drawdown_amount"]) == -expected
    assert (
        signed_in.post("/api/v1/trading-readiness/valuation", json=body).json()["id"]
        == snapshot["id"]
    )
    assert (
        signed_in.post(
            "/api/v1/trading-readiness/valuation", json={**body, "reason": "Different input"}
        ).status_code
        == 409
    )


def test_missing_mark_remains_incomplete_and_keeps_previous_drawdown(
    signed_in, paper_setup, database, monkeypatch
):
    _, deployment, *_ = paper_setup
    buy(signed_in, deployment)
    mark(database, 95)
    first, _ = observe(signed_in)
    original = valuation.paper.quote

    def missing(*args, **kwargs):
        raise HTTPException(409, "The market mark is stale.")

    monkeypatch.setattr(valuation.paper, "quote", missing)
    failed, _ = observe(signed_in)
    result = failed["payload"]["accounts"][0]
    assert not result["complete"] and result["equity"] is None
    assert result["unrealized_pnl"] is None and failed["payload"]["totals"][0]["equity"] is None
    assert (
        result["marked_max_drawdown_amount"]
        == first["payload"]["accounts"][0]["marked_max_drawdown_amount"]
    )
    monkeypatch.setattr(valuation.paper, "quote", original)
    mark(database, 105)
    recovered, _ = observe(signed_in)
    account = recovered["payload"]["accounts"][0]
    assert account["complete"] and account["drawdown_observations"] == 2
    assert account["marked_max_drawdown_amount"] == result["marked_max_drawdown_amount"]


def test_valuation_history_is_immutable_under_application_and_admin_roles(signed_in, database):
    snapshot, _ = observe(signed_in)
    for engine in database:
        with engine.begin() as connection:
            with pytest.raises(DBAPIError):
                connection.execute(
                    text("UPDATE trading_readiness_records SET payload='{}' WHERE id=:id"),
                    {"id": snapshot["id"]},
                )


def test_openclaw_reduces_native_quantity_without_changing_stop(signed_in, database, ready):
    _, job = queue(signed_in, ready, submit=False)
    result = complete(signed_in, database, ready, job, changes={"risk_scale": "0.503"})
    assert result["status"] == "succeeded", result
    response = submit(signed_in, job)
    assert response.status_code == 200, response.text
    assert response.json()["request"]["quantity"] == "100"
    assert response.json()["request"]["stop_loss"] == "9"
    assert len(ready[1].submissions) == 1


@pytest.mark.parametrize("scale", ["1.001", "0", "NaN", "0.001"])
def test_openclaw_cannot_raise_native_ceiling_or_create_invalid_quantity(
    signed_in, database, ready, scale
):
    _, job = queue(signed_in, ready)
    result = complete(signed_in, database, ready, job, changes={"risk_scale": scale})
    assert result["status"] == "failed" and result["decision"] is None
    assert ready[1].submissions == []


def test_forward_protocol_is_prospective_and_incomplete_runs_cannot_activate(
    signed_in, database, ready, monkeypatch
):
    native, broker, stamp, *_ = ready
    # Registration requires a dedicated account; the existing fixture has external holdings.
    request = {
        "request_id": str(uuid4()),
        "reason": "Synthetic prospective validation",
        "provider": "alpaca",
        "strategy_version_id": native["request"]["broker_context"]["assessment_input"]["strategy"][
            "id"
        ],
        "instrument_id": native["instrument_id"],
        "minimum_days": 1,
        "minimum_closed_trades": 1,
    }
    assert signed_in.post("/api/v1/trading-readiness/forward", json=request).status_code == 409
    broker.external = 0
    original_request = broker.request

    def empty_activity(method, path, **kwargs):
        if method == "GET" and path in {"/v2/orders", "/v2/account/activities"}:
            return []
        return original_request(method, path, **kwargs)

    monkeypatch.setattr(broker, "request", empty_activity)
    response = signed_in.post("/api/v1/trading-readiness/forward", json=request)
    assert response.status_code == 201, response.text
    protocol = response.json()
    assert (
        signed_in.post("/api/v1/trading-readiness/forward", json=request).json()["id"]
        == protocol["id"]
    )
    evaluated = signed_in.post(
        f"/api/v1/trading-readiness/forward/{protocol['id']}/evaluate",
        json={"request_id": str(uuid4()), "reason": "Check incomplete prospective run"},
    )
    assert evaluated.status_code == 200, evaluated.text
    report = evaluated.json()
    criteria = {c["name"]: c["status"] for c in report["payload"]["criteria"]}
    assert report["payload"]["status"] == "incomplete"
    assert criteria["prospective_duration"] == criteria["prospective_sample"] == "incomplete"
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        with pytest.raises(HTTPException):
            live_trading.eligible_report(db, owner, report["id"], "alpaca")
        # Current history queries are constrained to the requested observation window.
        assert (
            valuation.history(db, owner, "no-matching-account", stamp, stamp + timedelta(days=1))
            == []
        )
        assert (
            db.get(TradingReadinessRecord, protocol["id"]).created_at
            <= db.get(TradingReadinessRecord, report["id"]).created_at
        )
    assert broker.submissions == []


def test_live_policy_requires_explicit_acknowledgment_and_passing_evidence(
    signed_in, ready, monkeypatch
):
    # Use stored dummy credentials only, with the offline broker fixture.
    stored = broker_execution.stored_connection
    monkeypatch.setattr(
        broker_execution,
        "stored_connection",
        lambda db, provider: (
            stored(db, provider)[0].model_copy(update={"environment": "live"}),
            *stored(db, provider)[1:],
        ),
    )
    broker = ready[1]
    account = broker.account
    monkeypatch.setattr(
        broker, "account", lambda: account().model_copy(update={"environment": "live"})
    )
    response = signed_in.put(
        "/api/v1/brokers/alpaca/policy",
        json={
            "request_id": str(uuid4()),
            "expected_revision": 1,
            "enabled": True,
            "reason": "Attempt live activation without qualifying evidence",
        },
    )
    assert response.status_code in {409, 422} and broker.submissions == []


def test_active_positions_prevent_account_switch_but_allow_verified_credential_rotation(
    signed_in, database, ready
):
    _, job = queue(signed_in, ready, submit=False)
    complete(signed_in, database, ready, job)
    assert submit(signed_in, job).status_code == 200
    body = {
        "environment": "live",
        "credential_action": "replace",
        "key": "other-key",
        "secret": "other-secret",
        "expected_revision": 1,
    }
    response = signed_in.put("/api/v1/market-connections/alpaca", json=body)
    assert response.status_code == 409
    response = signed_in.put(
        "/api/v1/market-connections/alpaca", json={**body, "environment": "paper"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 2
    # Rotation preserves the existing account's reconciliation lineage.
    entry_id = signed_in.get("/api/v1/brokers").json()["orders"][0]["id"]
    assert signed_in.post(f"/api/v1/brokers/orders/{entry_id}/position").status_code == 200
