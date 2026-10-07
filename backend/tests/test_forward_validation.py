"""Controlled clocks/fills exercise promotion criteria in a disposable database.

These synthetic observations never certify an owner's real trading strategy.
"""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_broker_review import complete, queue, submit
from test_broker_review import configured as configured
from test_broker_review import lifecycle as lifecycle
from test_broker_review import ready as ready
from test_broker_review import setup as setup

from tauric import live_trading, paper, strategies, valuation
from tauric.broker_models import BrokerOrderIntent
from tauric.live_contracts import ForwardRegister
from tauric.live_models import TradingReadinessRecord
from tauric.models import Owner


def test_server_derived_forward_pass_and_later_cash_transfer_revokes_promotion(
    signed_in, ready, database, monkeypatch
):
    native, broker, stamp, *_ = ready
    begin, ending = stamp - timedelta(minutes=1), stamp + timedelta(days=1)
    broker.external = 0
    request = ForwardRegister(
        request_id=uuid4(),
        reason="Synthetic prospective promotion criteria",
        provider="alpaca",
        strategy_version_id=native["request"]["broker_context"]["assessment_input"]["strategy"][
            "id"
        ],
        instrument_id=native["instrument_id"],
        minimum_days=1,
        minimum_closed_trades=1,
    )
    original_submit = broker.submit
    import test_broker_positions

    monkeypatch.setattr(test_broker_positions, "now", lambda: stamp)

    def filled(order):
        result = original_submit(order).model_copy(
            update={"average_fill_price": "12.01" if order.side == "buy" else "15"}
        )
        broker.orders[result.client_order_id] = result
        broker.fill(result)
        return result

    monkeypatch.setattr(broker, "submit", filled)

    def intent_time(_, __, target):
        target.created_at = stamp

    def observation_time(_, __, target):
        if target.created_at is None:
            target.created_at = live_trading.now()

    event.listen(BrokerOrderIntent, "before_insert", intent_time)
    event.listen(TradingReadinessRecord, "before_insert", observation_time)
    try:
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            strategy = strategies.version(db, owner, request.strategy_version_id)
            baseline = valuation.broker_account(db, owner, "alpaca")
            # Research qualification is synthetic here; actual registration and
            # non-qualified refusal are covered by test_live_readiness.
            payload = {
                "request": request.model_dump(mode="json"),
                "strategy_checksum": strategy.checksum,
                "execution_signature": live_trading.signature(),
                "connection_revision": 1,
                "account_fingerprint": baseline["account_fingerprint"],
                "account_key": baseline["key"],
                "currency": "USD",
                "starting_equity": "10000",
                "transaction_checkpoint": None,
                "research_report_ids": ["synthetic-research-fixture"],
            }
            protocol = TradingReadinessRecord(
                owner_id=owner.id,
                kind="forward_protocol",
                request_id=str(request.request_id),
                payload=payload,
                checksum=paper.checksum(payload),
                created_at=begin,
            )
            db.add(protocol)
            db.commit()
            protocol_id, owner_id = protocol.id, owner.id
        _, job = queue(signed_in, ready, submit=False)
        complete(signed_in, database, ready, job, changes={"risk_scale": "0.1"})
        result = submit(signed_in, job)
        assert result.status_code == 200 and result.json()["status"] == "filled", result.text
        entry_id = result.json()["id"]
        closed = signed_in.post(
            f"/api/v1/brokers/orders/{entry_id}/close",
            json={
                "request_id": str(uuid4()),
                "quantity": "20",
                "reason": "Close synthetic governed forward trade",
            },
        )
        assert closed.status_code == 200 and closed.json()["status"] == "filled", closed.text
        assert broker.positions() == []
        account = broker.account
        monkeypatch.setattr(
            broker, "account", lambda: account().model_copy(update={"equity": "10059.80"})
        )
        original_request = broker.request
        adjustments = []

        def history(method, path, **kwargs):
            if method == "GET" and path == "/v2/orders":
                return []
            if (
                method == "GET"
                and path == "/v2/account/activities"
                and "order_id" not in (kwargs.get("params") or {})
            ):
                return [
                    row for values in broker.activities.values() for row in values
                ] + adjustments
            return original_request(method, path, **kwargs)

        monkeypatch.setattr(broker, "request", history)
        with Session(database[0]) as db:
            time = begin + timedelta(minutes=5)
            while time < ending:
                payload = {
                    "accounts": [{"key": baseline["key"], "complete": True, "equity": "10000"}]
                }
                db.add(
                    TradingReadinessRecord(
                        owner_id=owner_id,
                        kind="valuation",
                        request_id=str(uuid4()),
                        payload=payload,
                        checksum=paper.checksum(payload),
                        created_at=time,
                    )
                )
                time += timedelta(minutes=5)
            db.commit()
        monkeypatch.setattr(live_trading, "now", lambda: ending)
        url = f"/api/v1/trading-readiness/forward/{protocol_id}/evaluate"
        response = signed_in.post(
            url, json={"request_id": str(uuid4()), "reason": "Evaluate controlled forward evidence"}
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["payload"]["status"] == "passed", report["payload"]["criteria"]
        assert report["payload"]["result"]["closed_trades"] == 1
        assert Decimal(report["payload"]["maximum_risk_percent"]) == Decimal("0.602")
        with Session(database[0]) as db:
            owner = db.get(Owner, owner_id)
            assert (
                live_trading.eligible_report(db, owner, report["id"], "alpaca").id == report["id"]
            )
        adjustments.append(
            {"id": "unrelated-deposit", "activity_type": "TRANS", "net_amount": "500"}
        )
        monkeypatch.setattr(live_trading, "now", lambda: ending + timedelta(seconds=1))
        invalid = signed_in.post(
            url, json={"request_id": str(uuid4()), "reason": "Detect subsequent cash transfer"}
        ).json()
        assert invalid["payload"]["status"] == "incomplete"
        assert (
            next(
                c
                for c in invalid["payload"]["criteria"]
                if c["name"] == "dedicated_reconciled_account"
            )["status"]
            == "incomplete"
        )
        with Session(database[0]) as db:
            with pytest.raises(HTTPException):
                live_trading.eligible_report(db, db.get(Owner, owner_id), report["id"], "alpaca")
        assert len(broker.submissions) == 2
    finally:
        event.remove(BrokerOrderIntent, "before_insert", intent_time)
        event.remove(TradingReadinessRecord, "before_insert", observation_time)
