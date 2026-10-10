"""Owner-bound broker tools reuse execution services and invocation receipts."""

from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_management import HEADERS, READ, action
from test_assistant_management import invocation as invocation
from test_chat import ready as ready

from tauric import assistant_brokers
from tauric.broker_contracts import BrokerCloseView
from tauric.models import Owner, now


def test_broker_close_dispatch_and_retry(ready, database, invocation, monkeypatch):
    intent_id = str(uuid4())
    calls = []

    def close(db, owner, identifier, body):
        calls.append((owner.id, identifier, body))
        return BrokerCloseView(
            id=str(body.request_id),
            entry_intent_id=identifier,
            status="requested",
            quantity=body.quantity,
            detail="Pending broker confirmation",
            created_at=now(),
        )

    monkeypatch.setattr(assistant_brokers.broker_positions, "request_close", close)
    response = action(
        ready, invocation, "close_broker_position", {"intent_id": intent_id, "quantity": "2"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["result"]["status"] == "requested"
    assert (
        action(
            ready, invocation, "close_broker_position", {"intent_id": intent_id, "quantity": "2"}
        ).json()
        == response.json()
    )
    assert len(calls) == 1
    with Session(database[0]) as db:
        assert calls[0][0] == db.scalar(select(Owner)).id
    assert calls[0][1] == intent_id
    assert calls[0][2].reason == "Owner-requested research upgrade"
    assert (
        action(
            ready, invocation, "close_broker_position", {"intent_id": intent_id, "quantity": "3"}
        ).status_code
        == 409
    )


def test_broker_rejection_is_not_a_success_receipt(ready, invocation, monkeypatch):
    def reject(*args):
        raise HTTPException(409, "Live account qualification is missing")

    monkeypatch.setattr(assistant_brokers.broker_positions, "request_close", reject)
    response = action(
        ready, invocation, "close_broker_position", {"intent_id": str(uuid4()), "quantity": "2"}
    )
    assert response.status_code == 409
    assert "qualification" in response.json()["detail"]
    assert ready.get("/api/v1/assistant").json()["receipts"] == []


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
def test_broker_policy_schema_is_discoverable(ready, invocation, provider):
    response = ready.post(
        READ,
        headers=HEADERS,
        json={**invocation, "resource": "schemas", "operation": "save_broker_policy"},
    )
    assert response.status_code == 200, response.text
    schema = response.json()["parameters_schemas"]["save_broker_policy"]
    assert (
        provider in schema["$defs"]["BrokerProvider"]["enum"]
        if "$defs" in schema
        else provider in schema["properties"]["provider"]["enum"]
    )
    assert "request_id" not in schema["properties"]
    assert "live_acknowledged" in schema["properties"]


def test_broker_management_rejects_untrusted_owner(ready, invocation):
    response = action(
        ready,
        {**invocation, "sender_is_owner": False},
        "close_broker_position",
        {"intent_id": str(uuid4()), "quantity": "2"},
    )
    assert response.status_code == 403


def test_broker_read_is_owner_bound(ready, invocation, monkeypatch):
    calls = []

    def read(db, owner, resource, resource_id):
        calls.append((owner.id, resource, resource_id))
        return {"records": []}

    monkeypatch.setattr(assistant_brokers, "read", read)
    response = ready.post(READ, headers=HEADERS, json={**invocation, "resource": "broker_reviews"})
    assert response.status_code == 200, response.text
    assert calls[0][1] == "broker_reviews"
    assert (
        ready.post(
            READ,
            headers=HEADERS,
            json={**invocation, "sender_is_owner": False, "resource": "broker_reviews"},
        ).status_code
        == 403
    )
    assert len(calls) == 1


def test_project_tool_cannot_change_financial_policy(ready, invocation):
    response = action(
        ready,
        invocation,
        "save_broker_policy",
        {
            "provider": "oanda",
            "expected_revision": 0,
            "enabled": True,
        },
    )
    assert response.status_code == 403, response.text
    assert "financial" in response.text.lower()
