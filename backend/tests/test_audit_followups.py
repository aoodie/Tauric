"""Review follow-ups use real disposable storage and offline broker evidence."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from test_broker_execution import broker as broker
from test_broker_execution import create, enable
from test_broker_positions import lifecycle as lifecycle
from test_live_readiness import observe

from tauric import broker_execution, live_trading
from tauric.broker_adapters import BrokerError
from tauric.broker_models import BrokerOrderIntent, BrokerPositionCursor
from tauric.models import Owner, now


@pytest.mark.parametrize("failure", ["account", "positions"])
@pytest.mark.parametrize("rotate", [False, True])
def test_drawdown_survives_multiple_failed_observations_and_recovery(
    signed_in, broker, monkeypatch, failure, rotate
):
    state = {"equity": "10000", "failed": False}
    original_account, original_positions = broker.account, broker.positions

    def account():
        if state["failed"] and failure == "account":
            raise BrokerError("Offline account observation unavailable")
        return original_account().model_copy(update={"equity": state["equity"]})

    def positions():
        if state["failed"] and failure == "positions":
            raise BrokerError("Offline holdings observation unavailable")
        return original_positions()

    monkeypatch.setattr(broker, "account", account)
    monkeypatch.setattr(broker, "positions", positions)
    observe(signed_in)
    state["equity"] = "8000"
    observe(signed_in)
    state["failed"] = True
    for _ in range(2):
        failed = observe(signed_in)[0]["payload"]["accounts"][0]
        assert not failed["complete"] and failed["equity"] is None
        assert "account_fingerprint" not in failed
    if rotate:
        response = signed_in.put(
            "/api/v1/market-connections/alpaca",
            json={
                "environment": "paper",
                "credential_action": "replace",
                "key": "replacement-offline-key",
                "secret": "replacement-offline-secret",
                "expected_revision": 1,
            },
        )
        assert response.status_code == 200, response.text
        observe(signed_in)
    state["failed"] = False
    recovered = observe(signed_in)[0]["payload"]["accounts"][0]
    assert recovered["marked_peak_equity"] == "10000"
    assert recovered["marked_max_drawdown_percent"] == "20.0"
    broker.current_fingerprint = "b" * 64
    separate = observe(signed_in)[0]["payload"]["accounts"][0]
    assert separate["marked_peak_equity"] == "8000"
    assert separate["marked_max_drawdown_percent"] == "0"


@pytest.mark.parametrize("terminal", ["filled", "cancelled"])
def test_fill_after_unfilled_refresh_cannot_disappear_from_netted_attribution(
    signed_in, lifecycle, database, monkeypatch, terminal
):
    lifecycle.external = 0
    first = create(signed_in, quantity="10")[1]
    original_submit = lifecycle.submit

    def pending(order):
        result = original_submit(order).model_copy(
            update={"status": "pending", "filled_quantity": "0", "average_fill_price": None}
        )
        lifecycle.orders[order.client_order_id] = result
        lifecycle.activities.pop(result.broker_order_id)
        return result

    monkeypatch.setattr(lifecycle, "submit", pending)
    second = create(signed_in, quantity="10")[1]
    path = f"/api/v1/brokers/orders/{second['id']}"
    assert signed_in.post(path + "/position").json()["status"] == "unfilled"
    order = lifecycle.orders[second["client_order_id"]].model_copy(
        update={
            "status": terminal,
            "filled_quantity": "10" if terminal == "filled" else "5",
            "average_fill_price": "150",
        }
    )
    lifecycle.orders[order.client_order_id] = order
    lifecycle.fill(order)
    assert signed_in.post(path + "/reconcile").status_code == 200
    with Session(database[0]) as db:
        cursor = db.get(BrokerPositionCursor, second["id"])
        assert cursor.status == "unknown" and cursor.updated_at < now() - timedelta(seconds=15)
        # Reproduce an older persisted cursor as well as testing new observations.
        cursor.status = "unfilled"
        db.commit()
    lifecycle.holding_override = 10
    response = signed_in.post(f"/api/v1/brokers/orders/{first['id']}/position")
    assert response.status_code == 200, response.text
    assert response.json()["holding_check"] == "shortfall"
    assert response.json()["attributed_symbol_quantity"] == ("20" if terminal == "filled" else "15")
    close = signed_in.post(
        f"/api/v1/brokers/orders/{first['id']}/close",
        json={"request_id": str(uuid4()), "quantity": "1", "reason": "Inspect shared holdings"},
    )
    assert close.status_code == 409


@pytest.mark.parametrize("provider,environment", [("alpaca", "paper"), ("oanda", "practice")])
def test_saved_demo_drawdown_caps_block_new_entries_but_allow_reductions(
    signed_in, broker, monkeypatch, provider, environment
):
    if provider == "oanda":
        response = signed_in.put(
            "/api/v1/market-connections/oanda",
            json={
                "environment": "practice",
                "credential_action": "replace",
                "token": "offline-practice-token",
                "account_id": "001-001-1234567-001",
                "expected_revision": 0,
            },
        )
        assert response.status_code == 200, response.text
        broker.provider = "oanda"
    original = broker.account
    state = {"equity": "10000"}
    monkeypatch.setattr(
        broker, "account", lambda: original().model_copy(update={"equity": state["equity"]})
    )
    policy = signed_in.put(
        f"/api/v1/brokers/{provider}/policy",
        json={
            "request_id": str(uuid4()),
            "expected_revision": 0,
            "enabled": True,
            "no_owner_limits": False,
            "maximum_risk_percent": "5",
            "maximum_drawdown_percent": "15",
            "reason": "Offline explicit demo account limits",
        },
    )
    assert policy.status_code == 200, policy.text
    body = {
        "request_id": str(uuid4()),
        "expected_connection_revision": 1,
        "symbol": "AAPL" if provider == "alpaca" else "EUR_USD",
        "side": "buy",
        "quantity": "2",
        "reason": "Check saved demo drawdown enforcement",
    }
    path = f"/api/v1/brokers/{provider}/orders"
    assert signed_in.post(path, json=body).status_code == 409
    observe(signed_in)
    state["equity"] = "8000"
    observe(signed_in)
    response = signed_in.post(path, json={**body, "request_id": str(uuid4())})
    assert response.status_code == 409 and "drawdown limit" in response.text
    assert broker.submissions == []
    # Exit authority does not depend on a fresh drawdown observation.
    monkeypatch.setattr(
        broker_execution,
        "submit",
        lambda db, owner, key: broker_execution.view(db, db.get(BrokerOrderIntent, key)),
    )
    response = signed_in.post(
        path,
        json={**body, "request_id": str(uuid4()), "side": "sell", "position_effect": "reduce"},
    )
    assert response.status_code == 201, response.text


def test_prepared_demo_entry_rechecks_drawdown_before_submission(
    signed_in, broker, database, monkeypatch
):
    enable(
        signed_in,
        no_owner_limits=False,
        maximum_risk_percent="5",
        maximum_drawdown_percent="15",
    )
    state = {"equity": "10000"}
    original_account = broker.account
    monkeypatch.setattr(
        broker,
        "account",
        lambda: original_account().model_copy(update={"equity": state["equity"]}),
    )
    observe(signed_in)
    actual_submit = broker_execution.submit
    monkeypatch.setattr(
        broker_execution,
        "submit",
        lambda db, owner, key: broker_execution.view(db, db.get(BrokerOrderIntent, key)),
    )
    _, prepared = create(signed_in)
    assert prepared["status"] == "prepared"
    state["equity"] = "8000"
    observe(signed_in)
    with Session(database[0]) as db:
        owner = db.get(Owner, db.get(BrokerOrderIntent, prepared["id"]).owner_id)
        response = actual_submit(db, owner, prepared["id"])
        assert response.status == "rejected"
    assert broker.submissions == []


def test_capped_demo_entry_rejects_a_stale_valuation(signed_in, broker, monkeypatch):
    enable(
        signed_in,
        no_owner_limits=False,
        maximum_risk_percent="5",
        maximum_drawdown_percent="15",
    )
    observe(signed_in)
    future = now() + timedelta(seconds=121)
    monkeypatch.setattr(live_trading, "now", lambda: future)
    response = signed_in.post(
        "/api/v1/brokers/alpaca/orders",
        json={
            "request_id": str(uuid4()),
            "expected_connection_revision": 1,
            "symbol": "AAPL",
            "side": "buy",
            "quantity": "2",
            "reason": "Inspect freshness of saved drawdown evidence",
        },
    )
    assert response.status_code == 409 and "recent marked" in response.text
    assert broker.submissions == []
