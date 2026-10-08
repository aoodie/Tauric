"""Partial close, uncertain replacement and subsequent protective fill recovery."""

from decimal import Decimal
from uuid import uuid4

import pytest
from test_broker_execution import create
from test_broker_positions import LifecycleBroker, close, enable

from tauric import broker_execution
from tauric.broker_adapters import BrokerError


class ProtectedBroker(LifecycleBroker):
    def __init__(self):
        super().__init__()
        self.replacements = {}
        self.protect_calls = []
        self.protect_timeout = False
        self.protect_rejected = False
        self.protect_held = False

    def request(self, method, path, params=None, body=None, **kwargs):
        if method == "POST" and path == "/v2/orders":
            self.protect_calls.append(body)
            parent = {
                "id": body["client_order_id"],
                "client_order_id": body["client_order_id"],
                "symbol": body["symbol"],
                "side": body["side"],
                "qty": body["qty"],
                "type": body["type"],
                "filled_qty": "0",
                "filled_avg_price": None,
                "status": "rejected"
                if self.protect_rejected
                else "held"
                if self.protect_held
                else "accepted",
                "legs": [],
            }
            if body.get("order_class") == "oco":
                parent["limit_price"] = body["take_profit"]["limit_price"]
                parent["legs"] = [
                    {
                        **parent,
                        "id": "stop-" + parent["id"],
                        "client_order_id": "stop-child",
                        "type": "stop",
                        "stop_price": body["stop_loss"]["stop_price"],
                    }
                ]
            elif body["type"] == "stop":
                parent["stop_price"] = body["stop_price"]
            else:
                parent["limit_price"] = body["limit_price"]
            self.replacements[parent["id"]] = parent
            self.orders[parent["id"]] = self.view_order(parent)
            if self.protect_timeout:
                raise BrokerError("Replacement accepted but response lost", unknown=True)
            return parent
        order_id = path.rsplit("/", 1)[-1]
        for parent in self.replacements.values():
            for raw in [parent, *parent["legs"]]:
                if raw["id"] == order_id:
                    if method == "DELETE":
                        self.cancellations.append(path)
                        raw["status"] = "canceled"
                        self.orders[parent["id"]] = self.view_order(parent)
                        return {}
                    if method == "GET":
                        return raw
        return super().request(method, path, params=params, body=body, **kwargs)


@pytest.fixture
def protected(monkeypatch, signed_in):
    signed_in.put(
        "/api/v1/market-connections/alpaca",
        json={
            "environment": "paper",
            "credential_action": "replace",
            "key": "fixture-key",
            "secret": "fixture-secret",
            "expected_revision": 0,
        },
    )
    broker = ProtectedBroker()
    monkeypatch.setattr(broker_execution, "adapter", lambda *args, **kwargs: broker)
    enable(signed_in)
    _, opened = create(signed_in, stop_loss="140", take_profit="180")
    broker.protect()
    return broker, opened["id"]


def test_partial_close_restores_stop_and_target_only_for_attributed_remainder(signed_in, protected):
    broker, entry_id = protected
    _, response = close(signed_in, entry_id)
    assert response.json()["status"] == "filled", response.text
    replacement = broker.protect_calls[0]
    assert replacement["qty"] == "1" and replacement["order_class"] == "oco"
    assert replacement["stop_loss"]["stop_price"] == "140"
    position = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert position["protection_status"] == "covered" and position["remaining_quantity"] == "1"
    assert close(signed_in, entry_id)[1].json()["status"] == "filled"
    assert len(broker.protect_calls) == 1 and broker.positions()[0].quantity == "7"


def test_lost_replacement_response_reconciles_once_and_keeps_close_unresolved(signed_in, protected):
    broker, entry_id = protected
    broker.protect_timeout = True
    _, response = close(signed_in, entry_id)
    assert response.json()["status"] == "unknown"
    assert len(broker.protect_calls) == 1
    for _ in range(2):
        result = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
        assert result.json()["status"] == "filled", result.text
    assert len(broker.protect_calls) == 1 and len(broker.submissions) == 2


def test_rejected_replacement_never_completes_or_repeats(signed_in, protected):
    broker, entry_id = protected
    broker.protect_rejected = True
    _, response = close(signed_in, entry_id)
    assert response.json()["status"] == "unknown"
    result = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
    assert result.json()["status"] == "unknown"
    assert len(broker.protect_calls) == 1
    assert (
        signed_in.get("/api/v1/brokers/journal").json()["positions"][0]["protection_status"]
        == "unprotected"
    )


@pytest.mark.parametrize(
    "status, expected", [("held", "unprotected"), ("pending_cancel", "incomplete")]
)
def test_inactive_or_uncertain_stop_does_not_prove_coverage(signed_in, protected, status, expected):
    broker, entry_id = protected
    broker.children[0].update(type="stop", stop_price="140", status=status)
    response = signed_in.post(f"/api/v1/brokers/orders/{entry_id}/position")
    assert response.status_code == 200, response.text
    assert response.json()["protection_status"] == expected


def test_held_replacement_remains_unresolved_without_resubmission(signed_in, protected):
    broker, entry_id = protected
    broker.protect_held = True
    _, response = close(signed_in, entry_id)
    assert response.json()["status"] == "unknown", response.text
    repeat = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
    assert repeat.json()["status"] == "unknown" and len(broker.protect_calls) == 1
    assert (
        signed_in.get("/api/v1/brokers/journal").json()["positions"][0]["protection_status"]
        == "unprotected"
    )


def test_replacement_stop_fill_is_attributed_and_preserves_external_holdings(signed_in, protected):
    broker, entry_id = protected
    assert close(signed_in, entry_id)[1].json()["status"] == "filled"
    parent = next(iter(broker.replacements.values()))
    stop = parent["legs"][0]
    stop.update(status="filled", filled_qty="1", filled_avg_price="140")
    parent["status"] = "canceled"
    broker.orders[parent["id"]] = broker.view_order(parent)
    broker.fill(broker.view_order(stop))
    response = signed_in.post(f"/api/v1/brokers/orders/{entry_id}/position")
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"
    assert Decimal(response.json()["realized_gross_pnl"]) == 0
    assert broker.positions()[0].quantity == "7"


def test_terminal_failed_close_restores_full_surviving_quantity(signed_in, protected):
    broker, entry_id = protected
    broker.close_error = True
    _, response = close(signed_in, entry_id)
    closing = broker.orders[broker.submissions[-1]]
    broker.orders[broker.submissions[-1]] = closing.model_copy(update={"status": "rejected"})
    result = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
    assert result.json()["status"] == "rejected", result.text
    assert broker.protect_calls[0]["qty"] == "2"
    assert (
        signed_in.get("/api/v1/brokers/journal").json()["positions"][0]["protection_status"]
        == "covered"
    )


def test_confirmed_rejected_protection_can_be_repaired_with_one_new_request(signed_in, protected):
    broker, entry_id = protected
    broker.protect_rejected = True
    _, response = close(signed_in, entry_id)
    close_id = response.json()["id"]
    broker.protect_rejected = False
    body = {"request_id": str(uuid4()), "reason": "Repair confirmed rejected protection"}
    url = f"/api/v1/brokers/closes/{close_id}/repair-protection"
    result = signed_in.post(url, json=body)
    assert result.status_code == 200 and result.json()["status"] == "filled", result.text
    assert len(broker.protect_calls) == 2 and broker.protect_calls[-1]["qty"] == "1"
    # Completed repairs are already safely terminal and cannot issue another effect.
    assert signed_in.post(url, json=body).status_code == 409
    assert len(broker.protect_calls) == 2


def test_uncertain_protection_cannot_be_repaired_or_duplicated(signed_in, protected):
    broker, entry_id = protected
    broker.protect_timeout = True
    _, response = close(signed_in, entry_id)
    url = f"/api/v1/brokers/closes/{response.json()['id']}/repair-protection"
    result = signed_in.post(
        url, json={"request_id": str(uuid4()), "reason": "Attempt unsafe repeat"}
    )
    assert result.status_code == 409 and len(broker.protect_calls) == 1


def test_close_certified_not_submitted_restores_full_quantity(signed_in, protected, monkeypatch):
    broker, entry_id = protected
    submit = broker.submit

    def blocked(order):
        if order.position_effect == "reduce":
            raise BrokerError("Credentials denied before submission", not_submitted=True)
        return submit(order)

    monkeypatch.setattr(broker, "submit", blocked)
    _, response = close(signed_in, entry_id)
    result = signed_in.post(f"/api/v1/brokers/closes/{response.json()['id']}/reconcile")
    assert result.json()["status"] == "rejected", result.text
    assert broker.protect_calls[0]["qty"] == "2"
    assert len(broker.submissions) == 1
