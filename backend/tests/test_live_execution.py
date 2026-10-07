"""Live-domain authority tests use dummy credentials and offline evidence only."""

import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_broker_review import configured as configured
from test_broker_review import lifecycle as lifecycle
from test_broker_review import ready as ready
from test_broker_review import setup as setup
from test_broker_setup import finish as native_finish
from test_broker_setup import queue as native_queue

from tauric import broker_execution, broker_review, live_trading, paper
from tauric.broker_adapters import AlpacaLive, BrokerError, OandaLive
from tauric.broker_contracts import BrokerOrderRequest
from tauric.broker_models import BrokerReviewJob
from tauric.data_providers import AlpacaConnection, OandaConnection
from tauric.live_models import TradingReadinessRecord
from tauric.models import Owner


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
@pytest.mark.parametrize("missing", ["permission", "stop"])
def test_live_adapter_rejects_missing_entry_authority_before_network(provider, missing):
    requests = []
    transport = httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(500))
    config = (
        OandaConnection(environment="live", token="fixture-token", account_id="001-001-123-001")
        if provider == "oanda"
        else AlpacaConnection(environment="live", key="fixture-key", secret="fixture-secret")
    )
    with (OandaLive if provider == "oanda" else AlpacaLive)(config, transport=transport) as broker:
        if missing == "stop":
            broker.before_mutation = lambda: None
        order = BrokerOrderRequest(
            client_order_id="fixture_live_guard",
            symbol="EUR_USD" if provider == "oanda" else "AAPL",
            side="buy",
            quantity="1",
            stop_loss="1" if missing == "permission" else None,
        )
        with pytest.raises(BrokerError) as caught:
            broker.submit(order)
        assert caught.value.not_submitted and requests == []


def test_live_entry_requires_native_approval_reduced_review_and_current_valuation(
    signed_in, database, ready, monkeypatch
):
    initial, broker, stamp, tokens, _ = ready
    evidence = initial["request"]["broker_context"]["assessment_input"]
    stored = broker_execution.stored_connection
    monkeypatch.setattr(
        broker_execution,
        "stored_connection",
        lambda db, provider: (
            stored(db, provider)[0].model_copy(update={"environment": "live"}),
            *stored(db, provider)[1:],
        ),
    )
    broker.external = 0
    account = broker.account
    monkeypatch.setattr(
        broker, "account", lambda: account().model_copy(update={"environment": "live"})
    )
    monkeypatch.setattr(live_trading, "now", lambda: stamp)

    # Test-only registered observations; no production report is created or altered.
    def observed_time(_, __, target):
        target.created_at = stamp

    event.listen(TradingReadinessRecord, "before_insert", observed_time)
    try:
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            payload = {"request": {"provider": "alpaca"}, "currency": "USD"}
            protocol = TradingReadinessRecord(
                owner_id=owner.id,
                kind="forward_protocol",
                request_id=str(uuid4()),
                payload=payload,
                checksum=paper.checksum(payload),
                created_at=stamp,
            )
            db.add(protocol)
            db.flush()
            payload = {
                "provider": "alpaca",
                "status": "passed",
                "strategy_version_id": evidence["strategy"]["id"],
                "instrument_id": initial["instrument_id"],
                "execution_signature": live_trading.signature(),
                "protocol_checksum": protocol.checksum,
                "maximum_risk_percent": "1",
            }
            report = TradingReadinessRecord(
                owner_id=owner.id,
                kind="forward_report",
                parent_id=protocol.id,
                request_id=str(uuid4()),
                payload=payload,
                checksum=paper.checksum(payload),
                created_at=stamp,
            )
            db.add(report)
            db.commit()
            report_id = report.id
        activated = signed_in.put(
            "/api/v1/brokers/alpaca/policy",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "enabled": True,
                "live_acknowledged": True,
                "forward_report_id": report_id,
                "reason": "Offline test of live authority after synthetic gate evidence",
            },
        )
        assert activated.status_code == 200, activated.text
        assert (
            signed_in.post(
                "/api/v1/brokers/alpaca/orders",
                json={
                    "request_id": str(uuid4()),
                    "expected_connection_revision": 1,
                    "symbol": "AAPL",
                    "side": "buy",
                    "quantity": "1",
                    "stop_loss": "9",
                    "reason": "Attempt native bypass",
                },
            ).status_code
            == 409
        )
        body = {**evidence["request"], "request_id": str(uuid4()), "quantity": "20"}
        assert signed_in.post("/api/v1/brokers/setup-preview", json=body).status_code == 409
        snapshot = signed_in.post(
            "/api/v1/trading-readiness/valuation",
            json={"request_id": str(uuid4()), "reason": "Observe the offline live account"},
        )
        assert snapshot.status_code == 200, snapshot.text
        preview = signed_in.post("/api/v1/brokers/setup-preview", json=body)
        assert preview.status_code == 200 and preview.json()["entry_evidence_ready"], preview.text
        native = native_queue(signed_in, body)
        native_finish(database, native)
        queued = signed_in.post(
            "/api/v1/brokers/reviews",
            json={
                "request_id": str(uuid4()),
                "assessment_job_id": native["id"],
                "profile": "cloud",
                "submit_on_allow": False,
                "reason": "Offline mandatory native-to-live review",
            },
        )
        assert queued.status_code == 201, queued.text
        job_id = queued.json()["id"]
        with Session(database[0]) as db:
            claimed = broker_review.claim(db)
        context = signed_in.get(
            f"/internal/openclaw/broker-jobs/{job_id}/context",
            headers={"Authorization": "Bearer " + tokens["platform_token"]},
        )
        assert (
            context.status_code == 200
            and context.json()["context"]["authority"] == "read_only_live_broker_entry"
        )
        with Session(database[0]) as db:
            job = db.get(BrokerReviewJob, job_id)
            result = {
                **broker_review.result_binding(job),
                "outcome": "allow",
                "risk_scale": "0.5",
                "reason": "Reduce native approved risk by half",
            }
            assert broker_review.finish(db, *claimed, raw=json.dumps(result))
        response = signed_in.post(
            f"/api/v1/brokers/reviews/{job_id}/submit",
            json={"request_id": str(uuid4()), "reason": "Submit one offline governed live entry"},
        )
        assert response.status_code == 200 and response.json()["status"] == "filled", response.text
        assert response.json()["request"]["quantity"] == "10"
        assert response.json()["request"]["stop_loss"] == "9" and len(broker.submissions) == 1
        # A new live entry cannot race the first entry's unresolved protective coverage.
        assert (
            signed_in.post(
                "/api/v1/brokers/setup-preview", json={**body, "request_id": str(uuid4())}
            ).status_code
            == 409
        )
    finally:
        event.remove(TradingReadinessRecord, "before_insert", observed_time)
