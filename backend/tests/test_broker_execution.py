"""Durable demo broker workflow under real PostgreSQL guards, offline transport."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import broker_execution
from tauric.broker_adapters import BrokerError
from tauric.broker_contracts import BrokerAccountView, BrokerOrderView
from tauric.broker_models import BrokerOrderEvent, BrokerOrderIntent
from tauric.models import Owner, now


class FixtureBroker:
    def __init__(self):
        self.submissions = []
        self.cancellations = []
        self.lookups = []
        self.failure = None
        self.current_status = "pending"
        self.current_fingerprint = "a" * 64
        self.provider = "alpaca"
        self.quantity = "2"
        self.filled = "0"
        self.price = None
        self.last_client_id = None
        self.not_found = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def account(self):
        return BrokerAccountView(
            provider=self.provider,
            environment="paper" if self.provider == "alpaca" else "practice",
            account_fingerprint=self.current_fingerprint,
            currency="USD",
            cash="10000",
            equity="10000",
            buying_power="10000",
            trading_enabled=True,
            observed_at=now(),
        )

    def positions(self):
        return []

    def validate(self, order):
        pass

    def submit(self, order):
        self.submissions.append(order.client_order_id)
        self.last_client_id = order.client_order_id
        self.quantity = order.quantity
        if self.failure:
            raise BrokerError("Fixture unknown outcome", unknown=True)
        return self.order(order.client_order_id)

    def order(self, client_id):
        self.lookups.append(client_id)
        if self.not_found:
            raise BrokerError("Order not found", not_found=True)
        return BrokerOrderView(
            provider=self.provider,
            broker_order_id="fixture-order",
            client_order_id=client_id,
            symbol="AAPL",
            side="buy",
            requested_quantity=self.quantity,
            filled_quantity=self.filled,
            average_fill_price=self.price,
            status=self.current_status,
            broker_status=self.current_status,
            observed_at=now(),
        )

    def cancel(self, client_id):
        self.cancellations.append(client_id)
        self.current_status = "cancelled"
        return self.order(client_id)


@pytest.fixture
def broker(monkeypatch, signed_in):
    response = signed_in.put(
        "/api/v1/market-connections/alpaca",
        json={
            "environment": "paper",
            "credential_action": "replace",
            "key": "fixture-key",
            "secret": "fixture-secret",
            "expected_revision": 0,
        },
    )
    assert response.status_code == 200
    value = FixtureBroker()
    monkeypatch.setattr(broker_execution, "adapter", lambda *args, **kwargs: value)
    return value


def enable(signed_in, **values):
    body = {
        "request_id": str(uuid4()),
        "expected_revision": 0,
        "enabled": True,
        "reason": "Owner authorizes demo trading with no extra caps",
        **values,
    }
    response = signed_in.put("/api/v1/brokers/alpaca/policy", json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


def create(signed_in, **values):
    body = {
        "request_id": str(uuid4()),
        "expected_connection_revision": 1,
        "symbol": "AAPL",
        "side": "buy",
        "quantity": "2",
        "reason": "Explicit owner demo order",
        **values,
    }
    response = signed_in.post("/api/v1/brokers/alpaca/orders", json=body)
    assert response.status_code == 201, response.text
    return body, response.json()


def test_authentication_and_csrf(client):
    assert client.get("/api/v1/brokers").status_code == 401
    assert client.post("/api/v1/brokers/alpaca/check").status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert client.post("/api/v1/brokers/alpaca/check").status_code == 403


def test_default_disabled_requires_policy(signed_in, broker):
    assert signed_in.get("/api/v1/brokers").json()["policies"] == []
    response = signed_in.post(
        "/api/v1/brokers/alpaca/orders",
        json={
            "request_id": str(uuid4()),
            "expected_connection_revision": 1,
            "symbol": "AAPL",
            "side": "buy",
            "quantity": "2",
            "reason": "Demo entry request",
        },
    )
    assert response.status_code == 409 and broker.submissions == []


def test_no_limits_policy_idempotent_disable_and_history(signed_in, broker):
    body, saved = enable(signed_in)
    assert saved["settings"]["no_owner_limits"] is True
    assert saved["settings"]["automatic_entries_enabled"] is False
    assert signed_in.put("/api/v1/brokers/alpaca/policy", json=body).json()["id"] == saved["id"]
    altered = {**body, "enabled": False}
    assert signed_in.put("/api/v1/brokers/alpaca/policy", json=altered).status_code == 409
    assert (
        signed_in.put(
            "/api/v1/brokers/alpaca/policy",
            json={**body, "request_id": str(uuid4()), "expected_revision": 1, "enabled": False},
        ).status_code
        == 200
    )


def test_intent_committed_before_network_and_exactly_one_submission(signed_in, broker, database):
    enable(signed_in)
    original = broker.submit

    def prove_committed(order):
        with Session(database[0]) as independent:
            row = independent.scalar(
                select(BrokerOrderIntent).where(
                    BrokerOrderIntent.client_order_id == order.client_order_id
                )
            )
            assert row is not None and row.status == "submitting"
            receipts = independent.scalars(
                select(BrokerOrderEvent).where(BrokerOrderEvent.intent_id == row.id)
            ).all()
            assert [r.kind for r in receipts] == ["prepared", "submission_started"]
        return original(order)

    broker.submit = prove_committed
    body, result = create(signed_in)
    repeated = signed_in.post("/api/v1/brokers/alpaca/orders", json=body)
    assert repeated.json()["id"] == result["id"] and len(broker.submissions) == 1
    assert result["status"] == "pending"
    assert (
        signed_in.post("/api/v1/brokers/alpaca/orders", json={**body, "quantity": "3"}).status_code
        == 409
    )
    history = signed_in.get(f"/api/v1/brokers/orders/{result['id']}/history").json()
    assert [r["sequence"] for r in history] == [1, 2, 3]


def test_unknown_timeout_reconciles_existing_order_without_resubmission(signed_in, broker):
    enable(signed_in)
    broker.failure = True
    body, result = create(signed_in)
    assert result["status"] == "unknown"
    assert signed_in.post("/api/v1/brokers/alpaca/orders", json=body).json()["status"] == "unknown"
    broker.failure = None
    broker.current_status, broker.filled, broker.price = "filled", "2", "150.123456"
    reconciled = signed_in.post(f"/api/v1/brokers/orders/{result['id']}/reconcile")
    assert reconciled.status_code == 200 and reconciled.json()["status"] == "filled"
    assert reconciled.json()["latest_order"]["average_fill_price"] == "150.123456"
    assert len(broker.submissions) == 1


def test_timeout_not_found_never_permits_resubmission(signed_in, broker):
    enable(signed_in)
    broker.failure = True
    body, result = create(signed_in)
    broker.not_found = True
    for _ in range(2):
        response = signed_in.post(f"/api/v1/brokers/orders/{result['id']}/reconcile")
        assert response.json()["status"] == "unknown"
        signed_in.post("/api/v1/brokers/alpaca/orders", json=body)
    assert len(broker.submissions) == 1


def test_partial_fill_and_cancel_preserve_cumulative_fills(signed_in, broker):
    enable(signed_in)
    broker.current_status, broker.filled, broker.price = "partially_filled", "1", "150"
    _, result = create(signed_in)
    response = signed_in.post(f"/api/v1/brokers/orders/{result['id']}/cancel")
    assert response.json()["status"] == "cancelled"
    assert response.json()["latest_order"]["filled_quantity"] == "1"
    assert len(broker.cancellations) == 1
    signed_in.post(f"/api/v1/brokers/orders/{result['id']}/cancel")
    assert len(broker.cancellations) == 1


def test_changed_connection_or_account_blocks_new_orders_and_wrong_account_reconciliation(
    signed_in, broker
):
    enable(signed_in)
    _, result = create(signed_in)
    broker.current_fingerprint = "b" * 64
    response = signed_in.post(f"/api/v1/brokers/orders/{result['id']}/reconcile")
    assert response.json()["status"] == "unknown"
    response = signed_in.post(
        "/api/v1/brokers/alpaca/orders",
        json={
            "request_id": str(uuid4()),
            "expected_connection_revision": 1,
            "symbol": "AAPL",
            "side": "buy",
            "quantity": "2",
            "reason": "Demo order after account change",
        },
    )
    assert response.status_code == 409 and len(broker.submissions) == 1
    broker.current_fingerprint = "a" * 64
    signed_in.put(
        "/api/v1/market-connections/alpaca",
        json={"environment": "paper", "credential_action": "keep", "expected_revision": 1},
    )
    response = signed_in.post(
        "/api/v1/brokers/alpaca/orders",
        json={
            "request_id": str(uuid4()),
            "expected_connection_revision": 2,
            "symbol": "AAPL",
            "side": "buy",
            "quantity": "2",
            "reason": "Demo order after credential revision",
        },
    )
    assert response.status_code == 409 and len(broker.submissions) == 1


def test_broker_history_and_intent_authority_protected_in_database(signed_in, broker, database):
    enable(signed_in)
    _, result = create(signed_in)
    for sql in [
        "UPDATE broker_order_intents SET payload='{}'",
        "DELETE FROM broker_order_intents",
        "TRUNCATE broker_order_intents CASCADE",
        "UPDATE broker_policy_versions SET payload='{}'",
        "DELETE FROM broker_order_events",
        "TRUNCATE broker_policy_versions CASCADE",
    ]:
        with pytest.raises(DBAPIError), database[1].begin() as admin:
            admin.execute(text(sql))
    assert signed_in.get(f"/api/v1/brokers/orders/{result['id']}/history").status_code == 200


def test_owner_isolation(signed_in, broker, database):
    enable(signed_in)
    _, result = create(signed_in)
    with Session(database[0]) as db:
        foreign = Owner(id=str(uuid4()), username="other-owner", password_hash="not-a-valid-hash")
        with pytest.raises(HTTPException) as caught:
            broker_execution.owned(db, foreign, result["id"])
        assert caught.value.status_code == 404


def test_reconciliation_rejects_decreasing_fills(signed_in, broker):
    enable(signed_in)
    broker.current_status, broker.filled, broker.price = "partially_filled", "1", "150"
    _, result = create(signed_in)
    broker.filled = "0"
    response = signed_in.post(f"/api/v1/brokers/orders/{result['id']}/reconcile")
    assert response.json()["status"] == "unknown"
    assert "decreased" in response.json()["detail"]


def test_terminal_order_cannot_be_reactivated(signed_in, broker, database):
    enable(signed_in)
    broker.current_status, broker.filled, broker.price = "filled", "2", "150"
    _, result = create(signed_in)
    with pytest.raises(DBAPIError), database[1].begin() as admin:
        admin.execute(
            text("UPDATE broker_order_intents SET status='prepared' WHERE id=:id"),
            {"id": result["id"]},
        )
    broker.current_status = "pending"
    response = signed_in.post(f"/api/v1/brokers/orders/{result['id']}/reconcile")
    assert response.json()["status"] == "filled"
    assert len(broker.submissions) == 1


def test_worker_recovers_committed_prepared_intent_and_deduplicates_observations(
    signed_in, broker, database, monkeypatch
):
    from datetime import timedelta

    from tauric.models import now

    enable(signed_in)
    actual_submit = broker_execution.submit
    monkeypatch.setattr(
        broker_execution,
        "submit",
        lambda db, owner, intent_id: broker_execution.view(
            db, broker_execution.owned(db, owner, intent_id)
        ),
    )
    _, result = create(signed_in)
    assert result["status"] == "prepared" and broker.submissions == []
    monkeypatch.setattr(broker_execution, "submit", actual_submit)
    with Session(database[0]) as db:
        row = db.get(BrokerOrderIntent, result["id"])
        row.updated_at = now() - timedelta(seconds=30)
        db.commit()
        assert broker_execution.process_one(db)
        broker_execution.heartbeat(db)
        owner = db.get(Owner, row.owner_id)
        assert broker_execution.state(db, owner).worker_ready
        first_count = len(broker_execution.events(db, row))
        broker_execution.reconcile(db, owner, row.id)
        broker_execution.reconcile(db, owner, row.id)
        assert len(broker_execution.events(db, row)) == first_count
    assert len(broker.submissions) == 1


def test_same_account_credential_rotation_allows_reconciliation_without_new_orders(
    signed_in, broker
):
    enable(signed_in)
    _, result = create(signed_in)
    response = signed_in.put(
        "/api/v1/market-connections/alpaca",
        json={
            "environment": "paper",
            "credential_action": "replace",
            "key": "rotated-fixture-key",
            "secret": "rotated-fixture-secret",
            "expected_revision": 1,
        },
    )
    assert response.status_code == 200
    response = signed_in.post(f"/api/v1/brokers/orders/{result['id']}/reconcile")
    assert response.status_code == 200 and response.json()["status"] == "pending"
    assert len(broker.submissions) == 1


def test_committed_submission_cannot_return_to_prepared(signed_in, broker, database):
    enable(signed_in)
    _, result = create(signed_in)
    with pytest.raises(DBAPIError), database[1].begin() as admin:
        admin.execute(
            text("UPDATE broker_order_intents SET status='prepared' WHERE id=:id"),
            {"id": result["id"]},
        )
    assert len(broker.submissions) == 1


def test_competing_submitters_claim_the_same_prepared_intent_once(
    signed_in, broker, database, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor

    enable(signed_in)
    actual_submit = broker_execution.submit
    monkeypatch.setattr(
        broker_execution,
        "submit",
        lambda db, owner, intent_id: broker_execution.view(
            db, broker_execution.owned(db, owner, intent_id)
        ),
    )
    _, result = create(signed_in)
    monkeypatch.setattr(broker_execution, "submit", actual_submit)

    def submit_in_new_session():
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            return actual_submit(db, owner, result["id"]).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: submit_in_new_session(), range(2)))
    assert values == [result["id"], result["id"]]
    assert len(broker.submissions) == 1


def test_disabled_policy_revokes_unsubmitted_intent(signed_in, broker, database, monkeypatch):
    enable(signed_in)
    actual_submit = broker_execution.submit
    monkeypatch.setattr(
        broker_execution,
        "submit",
        lambda db, owner, intent_id: broker_execution.view(
            db, broker_execution.owned(db, owner, intent_id)
        ),
    )
    _, result = create(signed_in)
    monkeypatch.setattr(broker_execution, "submit", actual_submit)
    enable(signed_in, expected_revision=1, enabled=False)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        assert actual_submit(db, owner, result["id"]).status == "rejected"
    assert broker.submissions == []
