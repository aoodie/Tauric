"""Position lifecycle and close recovery against PostgreSQL and offline broker facts."""

from decimal import Decimal
from threading import Event, Thread
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_broker_execution import FixtureBroker, create, enable

from tauric import broker_execution, broker_positions
from tauric.broker_adapters import AlpacaPaper, BrokerError
from tauric.broker_contracts import BrokerOrderView, BrokerPositionView
from tauric.broker_models import BrokerCloseRequest, BrokerOrderEvent, BrokerPositionCursor
from tauric.models import Owner, now


class LifecycleBroker(FixtureBroker):
    def __init__(self):
        super().__init__()
        self.orders = {}
        self.activities = {}
        self.children = []
        self.close_error = False
        self.cancel_error = False
        self.close_hook = None
        self.entry_id = None
        self.external = Decimal(7)
        self.holding_override = None
        self.replacement_orders = {}

    def submit(self, order):
        self.submissions.append(order.client_order_id)
        reducing = order.position_effect == "reduce"
        if reducing and self.close_hook:
            self.close_hook()
        value = BrokerOrderView(
            provider="alpaca",
            broker_order_id=order.client_order_id,
            client_order_id=order.client_order_id,
            symbol=order.symbol,
            side=order.side,
            requested_quantity=order.quantity,
            filled_quantity="0" if reducing and self.close_error else order.quantity,
            average_fill_price=None
            if reducing and self.close_error
            else "160"
            if reducing
            else "150",
            status="pending" if reducing and self.close_error else "filled",
            broker_status="filled",
            observed_at=now(),
        )
        self.orders[order.client_order_id] = value
        if not reducing:
            self.entry_id = order.client_order_id
        if Decimal(value.filled_quantity):
            self.fill(value)
        if reducing and self.close_error:
            raise BrokerError("Unknown close outcome", unknown=True)
        return value

    def fill(self, order):
        self.activities[order.broker_order_id] = [
            {
                "id": "fill-" + order.broker_order_id,
                "activity_type": "FILL",
                "order_id": order.broker_order_id,
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.filled_quantity,
                "price": order.average_fill_price,
                "transaction_time": now().isoformat(),
            }
        ]

    def order(self, client_id):
        if client_id not in self.orders:
            raise BrokerError("Close order not found", not_found=True)
        return self.orders[client_id].model_copy(update={"observed_at": now()})

    view_order = AlpacaPaper.view_order

    def request(self, method, path, params=None, **kwargs):
        if method == "POST" and path == "/v2/orders":
            body = kwargs["body"]
            raw = {
                "id": body["client_order_id"],
                "client_order_id": body["client_order_id"],
                "symbol": body["symbol"],
                "side": body["side"],
                "qty": body["qty"],
                "type": body["type"],
                "status": "accepted",
                "filled_qty": "0",
                "filled_avg_price": None,
                "limit_price": body.get("limit_price"),
                "stop_price": body.get("stop_price"),
                "legs": [],
            }
            self.replacement_orders[raw["id"]] = raw
            self.orders[raw["id"]] = self.view_order(raw)
            return raw
        if method == "GET" and path.rsplit("/", 1)[-1] in self.replacement_orders:
            return self.replacement_orders[path.rsplit("/", 1)[-1]]
        if method == "GET" and path == "/v2/account/activities":
            return self.activities.get(params["order_id"], [])
        if method == "DELETE":
            self.cancellations.append(path)
            if self.cancel_error:
                # Broker accepted cancellation before the response was lost.
                for child in self.children:
                    child["status"] = "canceled"
                self.cancel_error = False
                raise BrokerError("Unknown protection cancellation", unknown=True)
            for child in self.children:
                child["status"] = "canceled"
            return None
        if method == "GET" and path.startswith("/v2/orders/"):
            parent = self.orders[path.rsplit("/", 1)[1]]
            return {
                "id": parent.broker_order_id,
                "client_order_id": parent.client_order_id,
                "symbol": parent.symbol,
                "qty": parent.requested_quantity,
                "side": parent.side,
                "filled_qty": parent.filled_quantity,
                "filled_avg_price": parent.average_fill_price,
                "status": parent.status,
                "legs": self.children,
            }
        raise AssertionError((method, path))

    def positions(self):
        entries = sum(
            (Decimal(o.filled_quantity) for o in self.orders.values() if o.side == "buy"),
            Decimal(0),
        )
        exits = sum(
            (
                Decimal(row["qty"])
                for rows in self.activities.values()
                for row in rows
                if row["side"] == "sell"
            ),
            Decimal(0),
        )
        quantity = (
            self.holding_override
            if self.holding_override is not None
            else self.external + entries - exits
        )
        if quantity <= 0:
            return []
        return [
            BrokerPositionView(
                symbol="AAPL",
                side="long",
                quantity=str(quantity),
                average_entry_price="145",
                unrealized_pnl="10",
            )
        ]

    def protect(self, filled="0"):
        self.children = [
            {
                "id": "protect-1",
                "client_order_id": "protection",
                "symbol": "AAPL",
                "side": "sell",
                "type": "limit",
                "limit_price": "180",
                "qty": str(max(Decimal(2), Decimal(filled))),
                "filled_qty": filled,
                "filled_avg_price": "180" if Decimal(filled) else None,
                "status": "filled"
                if filled == "2"
                else "partially_filled"
                if Decimal(filled)
                else "accepted",
            }
        ]
        if Decimal(filled):
            self.fill(self.view_order(self.children[0]))


@pytest.fixture
def lifecycle(monkeypatch, signed_in):
    assert (
        signed_in.put(
            "/api/v1/market-connections/alpaca",
            json={
                "environment": "paper",
                "credential_action": "replace",
                "key": "fixture-key",
                "secret": "fixture-secret",
                "expected_revision": 0,
            },
        ).status_code
        == 200
    )
    broker = LifecycleBroker()
    monkeypatch.setattr(broker_execution, "adapter", lambda *args, **kwargs: broker)
    enable(signed_in)
    return broker


def entry(signed_in):
    return create(signed_in)[1]


def close(signed_in, entry_id, quantity="1", **extra):
    body = {
        "request_id": str(uuid4()),
        "quantity": quantity,
        "reason": "Owner attributed demo close",
        **extra,
    }
    response = signed_in.post(f"/api/v1/brokers/orders/{entry_id}/close", json=body)
    return body, response


def test_partial_final_close_actual_pnl_preserves_external_holdings(signed_in, lifecycle, database):
    opened = entry(signed_in)
    position = signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/position").json()
    assert position["entry_quantity"] == "2" and position["broker_holding_quantity"] == "9"
    body, response = close(signed_in, opened["id"])
    assert response.status_code == 200 and response.json()["status"] == "filled", response.text
    repeated = signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/close", json=body)
    assert repeated.json()["id"] == response.json()["id"] and len(lifecycle.submissions) == 2
    partial = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert (partial["status"], partial["remaining_quantity"], partial["realized_gross_pnl"]) == (
        "partially_closed",
        "1",
        "10",
    )
    assert close(signed_in, opened["id"])[1].json()["status"] == "filled"
    final = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert final["status"] == "closed" and final["realized_gross_pnl"] == "20"
    assert lifecycle.positions()[0].quantity == "7"
    with Session(database[0]) as db:
        cursor = db.get(BrokerPositionCursor, opened["id"])
        assert cursor.status == "closed"
        for statement in [
            "UPDATE broker_close_requests SET payload='{}'",
            "DELETE FROM broker_close_requests",
            "UPDATE broker_position_cursors SET status='open'",
        ]:
            with pytest.raises(DBAPIError), db.begin_nested():
                db.execute(text(statement))


def test_unknown_close_never_repeats_even_when_broker_cannot_find_it(signed_in, lifecycle):
    opened = entry(signed_in)
    lifecycle.close_error = True
    body, response = close(signed_in, opened["id"])
    assert response.json()["status"] == "unknown"
    closing_id = lifecycle.submissions[-1]
    del lifecycle.orders[closing_id]
    for _ in range(2):
        result = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
        assert result.status_code == 200 and result.json()["status"] == "unknown"
        assert (
            signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/close", json=body).json()["id"]
            == response.json()["id"]
        )
    assert len(lifecycle.submissions) == 2
    assert close(signed_in, opened["id"])[1].status_code == 409


def test_protection_cancellation_timeout_recovers_without_repeat(signed_in, lifecycle):
    opened = entry(signed_in)
    lifecycle.protect()
    lifecycle.cancel_error = True
    _, response = close(signed_in, opened["id"])
    assert response.json()["status"] == "unknown" and len(lifecycle.submissions) == 1
    recovered = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
    assert recovered.json()["status"] == "filled", recovered.text
    assert len(lifecycle.cancellations) == 1 and len(lifecycle.submissions) == 2


def test_protection_fills_during_close_preparation_reduces_mutation(
    signed_in, lifecycle, monkeypatch
):
    opened = entry(signed_in)
    actual = broker_positions.process_close
    monkeypatch.setattr(
        broker_positions,
        "process_close",
        lambda db, owner, close_id: broker_positions.close_view(
            db, db.get(BrokerCloseRequest, close_id)
        ),
    )
    _, prepared = close(signed_in, opened["id"], "2")
    monkeypatch.setattr(broker_positions, "process_close", actual)
    lifecycle.protect("1")
    result = signed_in.post(f"/api/v1/brokers/closes/{prepared.json()['id']}/reconcile")
    assert result.json()["status"] == "filled", result.text
    assert lifecycle.orders[lifecycle.submissions[-1]].requested_quantity == "1"
    position = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert position["closed_quantity"] == "2" and position["realized_gross_pnl"] == "40"


def test_protection_fulfills_prepared_close_without_mutation(signed_in, lifecycle, monkeypatch):
    opened = entry(signed_in)
    actual = broker_positions.process_close
    monkeypatch.setattr(
        broker_positions,
        "process_close",
        lambda db, owner, close_id: broker_positions.close_view(
            db, db.get(BrokerCloseRequest, close_id)
        ),
    )
    _, prepared = close(signed_in, opened["id"], "2")
    monkeypatch.setattr(broker_positions, "process_close", actual)
    lifecycle.protect("2")
    result = signed_in.post(f"/api/v1/brokers/closes/{prepared.json()['id']}/reconcile")
    assert result.json()["status"] == "filled" and len(lifecycle.submissions) == 1


def test_holdings_shortfall_and_excess_protection_fills_block_close(signed_in, lifecycle):
    opened = entry(signed_in)
    lifecycle.holding_override = Decimal(1)
    position = signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/position").json()
    assert position["holding_check"] == "shortfall"
    assert close(signed_in, opened["id"])[1].status_code == 409
    lifecycle.protect("3")
    assert signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/position").status_code == 409
    assert len(lifecycle.submissions) == 1


def test_worker_tracks_filled_entry_and_closed_cursor_is_monotonic(signed_in, lifecycle, database):
    opened = entry(signed_in)
    with Session(database[0]) as db:
        assert broker_positions.process_one(db)
    lifecycle.protect("2")
    with Session(database[0]) as db:
        db.execute(
            text("UPDATE broker_position_cursors SET updated_at=now()-interval '30 seconds'")
        )
        db.commit()
        assert broker_positions.process_one(db)
    assert signed_in.get("/api/v1/brokers/journal").json()["positions"][0]["status"] == "closed"
    lifecycle.children = []
    lifecycle.activities.pop("protect-1")
    assert signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/position").status_code == 409


def test_competing_workers_commit_authority_before_exactly_one_close(
    signed_in, lifecycle, database, monkeypatch
):
    opened = entry(signed_in)
    actual = broker_positions.process_close
    monkeypatch.setattr(
        broker_positions,
        "process_close",
        lambda db, owner, close_id: broker_positions.close_view(
            db, db.get(BrokerCloseRequest, close_id)
        ),
    )
    _, prepared = close(signed_in, opened["id"])
    close_id = prepared.json()["id"]
    monkeypatch.setattr(broker_positions, "process_close", actual)
    started, release = Event(), Event()
    results, errors = [], []

    def prove_durable():
        with Session(database[0]) as fresh:
            request = fresh.get(BrokerCloseRequest, close_id)
            assert request.status == "submitting"
            receipt = fresh.scalar(
                select(BrokerOrderEvent).where(BrokerOrderEvent.kind == "close_submission_started")
            )
            assert receipt.payload["close_request_id"] == close_id
        started.set()
        assert release.wait(10)

    lifecycle.close_hook = prove_durable

    def run():
        try:
            with Session(database[0]) as db:
                results.append(actual(db, db.scalar(select(Owner)), close_id).status)
        except Exception as error:
            errors.append(error)

    worker = Thread(target=run)
    worker.start()
    try:
        assert started.wait(10)
        with Session(database[0]) as db:
            assert actual(db, db.scalar(select(Owner)), close_id).status == "submitting"
    finally:
        release.set()
        worker.join(10)
    assert not worker.is_alive() and errors == [] and results == ["filled"]
    assert len(lifecycle.submissions) == 2
    with Session(database[0]) as db:
        request = db.get(BrokerCloseRequest, close_id)
        assert request.status == "filled"
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(
                text("UPDATE broker_close_requests SET status='submitting' WHERE id=:id"),
                {"id": close_id},
            )


class OandaLifecycleBroker(FixtureBroker):
    def __init__(self):
        super().__init__()
        self.provider, self.current_status, self.filled, self.price = (
            "oanda",
            "filled",
            "10",
            "1.12345",
        )
        self.remaining = Decimal(10)
        self.transactions = {}
        self.close_calls = []
        self.close_timeout = False
        self.trade_client_override = None

    def account(self):
        return super().account().model_copy(update={"currency": "GBP"})

    def order(self, client_id):
        return (
            super().order(client_id).model_copy(update={"symbol": "EUR_USD", "trade_ids": ["100"]})
        )

    def positions(self):
        return [
            BrokerPositionView(
                symbol="EUR_USD",
                side="long",
                quantity=str(self.remaining + 3),
                average_entry_price="1.12",
                unrealized_pnl="1",
            )
        ]

    def path(self, suffix):
        return "/v3/accounts/fixture" + suffix

    def request(self, method, path, body=None, **kwargs):
        if method == "PUT":
            assert path.endswith("/trades/100/close")
            self.close_calls.append((path, body))
            qty = Decimal(body["units"])
            self.remaining -= qty
            tx_id = str(200 + len(self.transactions))
            piece = {
                "tradeID": "100",
                "units": str(qty),
                "realizedPL": "1.25",
                "financing": "-0.01",
            }
            transaction = {
                "id": tx_id,
                "instrument": "EUR_USD",
                "type": "ORDER_FILL",
                "orderID": tx_id,
                "price": "1.15",
                "time": now().isoformat(),
            }
            transaction["tradeReduced" if self.remaining else "tradesClosed"] = (
                piece if self.remaining else [piece]
            )
            self.transactions[tx_id] = transaction
            if self.close_timeout:
                raise BrokerError("OANDA close response lost", unknown=True)
            return {}
        if "/transactions/" in path:
            return {"transaction": self.transactions[path.rsplit("/", 1)[1]]}
        return {
            "trade": {
                "id": "100",
                "instrument": "EUR_USD",
                "initialUnits": "10",
                "currentUnits": str(self.remaining),
                "price": "1.12345",
                "state": "OPEN" if self.remaining else "CLOSED",
                "realizedPL": str(Decimal("1.25") * len(self.transactions)),
                "unrealizedPL": "0",
                "financing": "-0.02",
                "clientExtensions": {"id": self.trade_client_override or self.last_client_id},
                "closingTransactionIDs": list(self.transactions),
                "stopLossOrder": {
                    "id": "90",
                    "tradeID": "100",
                    "state": "PENDING" if self.remaining else "CANCELLED",
                    "price": "1.05",
                },
            }
        }


@pytest.fixture
def oanda_lifecycle(monkeypatch, signed_in):
    assert (
        signed_in.put(
            "/api/v1/market-connections/oanda",
            json={
                "environment": "practice",
                "credential_action": "replace",
                "token": "fixture-token",
                "account_id": "101-001-123-001",
                "expected_revision": 0,
            },
        ).status_code
        == 200
    )
    broker = OandaLifecycleBroker()
    monkeypatch.setattr(broker_execution, "adapter", lambda *args, **kwargs: broker)
    response = signed_in.put(
        "/api/v1/brokers/oanda/policy",
        json={
            "request_id": str(uuid4()),
            "expected_revision": 0,
            "enabled": True,
            "reason": "Owner demo authorization",
        },
    )
    assert response.status_code == 200
    response = signed_in.post(
        "/api/v1/brokers/oanda/orders",
        json={
            "request_id": str(uuid4()),
            "expected_connection_revision": 1,
            "symbol": "EUR_USD",
            "side": "buy",
            "quantity": "10",
            "time_in_force": "fok",
            "reason": "Owner actual trade fixture",
        },
    )
    assert response.status_code == 201, response.text
    return broker, response.json()["id"]


def test_oanda_partial_final_closes_target_only_owned_trade_home_pnl(signed_in, oanda_lifecycle):
    broker, entry_id = oanda_lifecycle
    position = signed_in.post(f"/api/v1/brokers/orders/{entry_id}/position").json()
    assert position["currency"] == "GBP" and position["notional_currency"] == "USD"
    assert position["entry_notional"] == "11.23450"
    assert close(signed_in, entry_id, "4")[1].json()["status"] == "filled"
    partial = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert partial["remaining_quantity"] == "6" and partial["broker_realized_pnl_home"] == "1.25"
    assert partial["protections"][0]["status"] == "pending"
    assert close(signed_in, entry_id, "6")[1].json()["status"] == "filled"
    final = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert final["status"] == "closed" and final["broker_realized_pnl_home"] == "2.50"
    assert final["broker_financing_home"] == "-0.02" and final["realized_gross_pnl"] is None
    assert broker.positions()[0].quantity == "3" and len(broker.close_calls) == 2


def test_oanda_lost_close_response_recovers_from_actual_trade_exits(signed_in, oanda_lifecycle):
    broker, entry_id = oanda_lifecycle
    broker.close_timeout = True
    _, response = close(signed_in, entry_id, "4")
    assert response.json()["status"] == "unknown", response.text
    recovered = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
    assert recovered.json()["status"] == "filled" and len(broker.close_calls) == 1
    broker.trade_client_override = "unrelated-trade"
    assert signed_in.post(f"/api/v1/brokers/orders/{entry_id}/position").status_code == 409
    assert len(broker.close_calls) == 1


def test_uncertain_close_partial_fills_update_journal_then_complete_without_repeat(
    signed_in, lifecycle
):
    opened = entry(signed_in)
    lifecycle.close_error = True
    _, response = close(signed_in, opened["id"], "2")
    close_id, client_id = response.json()["id"], lifecycle.submissions[-1]
    closing = lifecycle.orders[client_id].model_copy(
        update={"status": "partially_filled", "filled_quantity": "1", "average_fill_price": "160"}
    )
    lifecycle.orders[client_id] = closing
    lifecycle.fill(closing)
    assert (
        signed_in.post(f"/api/v1/brokers/closes/{close_id}/reconcile").json()["status"] == "pending"
    )
    partial = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert partial["status"] == "partially_closed" and partial["closed_quantity"] == "1"
    closing = closing.model_copy(update={"status": "filled", "filled_quantity": "2"})
    lifecycle.orders[client_id] = closing
    lifecycle.fill(closing)
    assert (
        signed_in.post(f"/api/v1/brokers/closes/{close_id}/reconcile").json()["status"] == "filled"
    )
    assert len(lifecycle.submissions) == 2


def test_full_protective_exit_keeps_tracking_until_companion_terminal(signed_in, lifecycle):
    opened = entry(signed_in)
    lifecycle.protect("2")
    lifecycle.children.append(
        {
            **lifecycle.children[0],
            "id": "protect-2",
            "client_order_id": "protect-2",
            "type": "stop",
            "status": "accepted",
            "filled_qty": "0",
            "filled_avg_price": None,
        }
    )
    position = signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/position").json()
    assert position["remaining_quantity"] == "0" and position["status"] == "unknown"
    lifecycle.children[1].update(status="filled", filled_qty="2", filled_avg_price="140")
    lifecycle.fill(lifecycle.view_order(lifecycle.children[1]))
    assert signed_in.post(f"/api/v1/brokers/orders/{opened['id']}/position").status_code == 409
    history = signed_in.get(f"/api/v1/brokers/orders/{opened['id']}/history").json()
    assert "exceed" in history[-1]["payload"]["detail"]
    assert len(lifecycle.submissions) == 1
