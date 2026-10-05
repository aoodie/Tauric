"""Broker setup capture and immutable native evidence in disposable PostgreSQL.

The stock calendar, breakout and quotes are explicit offline fixtures. These
tests neither call a model nor submit an order to any connected broker.
"""

import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_broker_positions import lifecycle as lifecycle_fixture
from test_market_analysis import configured as configured_fixture
from test_stock_execution import feed
from test_strategy_replay import RULES, register

from tauric import (
    analysis,
    broker_setup,
    market,
    native_broker_setup,
    stock_execution,
)
from tauric.broker_adapters import DemoBroker
from tauric.broker_contracts import BrokerInstrumentView, BrokerQuote
from tauric.models import AnalysisJob, MarketSnapshot
from tauric.native_runner import artifact

lifecycle = lifecycle_fixture
configured = configured_fixture


@pytest.fixture
def setup(signed_in, lifecycle, configured, database, monkeypatch):
    value, stamp = feed()
    rules = {**RULES, "interval": "15m"}
    saved = register(signed_in, rules=rules)
    data = {
        "candles": [bar.model_dump(mode="json") for bar in value.candles],
        "source": value.source,
        "symbol": value.instrument.provider_symbol,
        "volume_type": value.instrument.volume_type,
        "availability": "observed_at_receipt",
        "data_context": value.data_context.model_dump(mode="json"),
        "quality": value.quality.model_dump(mode="json"),
    }
    checksum = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    with Session(database[0]) as db:
        snapshot = MarketSnapshot(
            id=str(uuid4()),
            instrument_id=value.instrument.id,
            interval=value.interval,
            received_at=stamp,
            provider_time=stamp,
            checksum=checksum,
            data=data,
        )
        db.add(snapshot)
        db.commit()
        value.snapshot_id, value.checksum = snapshot.id, checksum
    monkeypatch.setattr(market, "refresh", lambda *args, **kwargs: value)
    for module in (analysis, broker_setup, native_broker_setup, stock_execution):
        monkeypatch.setattr(module, "now", lambda: stamp)
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    lifecycle.instrument = lambda name: BrokerInstrumentView(
        symbol=name, tradable=True, quantity_step="1", minimum_quantity="1", price_step="0.01"
    )
    lifecycle.quote = lambda name: BrokerQuote(
        symbol=name, bid="12", ask="12.01", observed_at=stamp, tradable=True, feed="iex"
    )
    lifecycle.validate = lambda order: DemoBroker.validate(lifecycle, order)
    body = {
        "request_id": str(uuid4()),
        "strategy_version_id": saved["id"],
        "instrument_id": value.instrument.id,
        "provider": "alpaca",
        "quantity": "200",
        "profile": "cloud",
        "reason": "Explicit offline setup verification, no broker orders",
    }
    return body, lifecycle, stamp, value


def queue(client, body):
    response = client.post("/api/v1/brokers/setup-assessments", json=body)
    assert response.status_code == 202, response.text
    return response.json()


def finish(database, job, **assessment_changes):
    with Session(database[0]) as db:
        active = db.get(AnalysisJob, analysis.claim(db))
        assert active.id == job["id"]
        evidence, binding = native_broker_setup.verify(active.request["broker_context"])
        payload = artifact(
            {"final_trade_decision": "Original synthetic daily BUY report"},
            "BUY",
            active.request,
            active.request["settings"],
            "0.5.2",
        )
        payload["broker_setup_assessment"] = {
            **binding,
            "decision": "approve",
            "rationale": "Synthetic exact entry review",
            "limitations": ["Synthetic review; no real model call or broker fill."],
            "adapter_version": native_broker_setup.ADAPTER_VERSION,
            "source": native_broker_setup.SOURCE,
            "execution_authority": False,
            "model_invocations": 1,
            **assessment_changes,
        }
        assert analysis.finish(db, active.id, payload=payload)
    return payload


def test_capture_real_rule_signal_exact_sizing_and_no_order(signed_in, setup, database):
    body, broker, _, _ = setup
    response = signed_in.post("/api/v1/brokers/setup-preview", json=body)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["entry_evidence_ready"] and preview["signal_action"] == "enter"
    planned = preview["planned_order"]
    assert (planned["quantity"], planned["stop_loss"], planned["take_profit"]) == (
        "200",
        "9",
        "18.03",
    )
    context = preview["context"]
    evidence, _ = native_broker_setup.verify(context)
    assert (
        evidence["policy"]["no_owner_limits"]
        and evidence["existing_positions"][0]["quantity"] == "7"
    )
    # Requested exposure exceeds the paper capital_limit; it is not a broker cap.
    assert evidence["strategy"]["parameters"]["capital_limit"] == "1000"
    job = queue(signed_in, body)
    duplicate = queue(signed_in, body)
    assert duplicate["id"] == job["id"] and job["request"]["mode"] == "broker_entry_assessment"
    assert broker.submissions == [] and broker.cancellations == []
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1
        broker_setup.validate_admission(db, db.get(AnalysisJob, job["id"]))
    pending = signed_in.get(f"/api/v1/brokers/setup-assessments/{job['id']}").json()
    assert pending["assessment"] is None and not pending["evidence_current"]


@pytest.mark.parametrize(
    "failure",
    [
        "closed",
        "old_quote",
        "future_quote",
        "wait_signal",
        "quantity",
        "provider",
        "version",
        "account",
    ],
)
def test_invalid_entry_does_not_queue_or_trade(signed_in, setup, database, failure):
    body, broker, stamp, value = setup
    expected = 422
    if failure in {"closed", "old_quote", "future_quote"}:
        broker.quote = lambda name: BrokerQuote(
            symbol=name,
            bid="12",
            ask="12.01",
            tradable=failure != "closed",
            observed_at=stamp
            + timedelta(
                seconds=-90 if failure == "old_quote" else 90 if failure == "future_quote" else 0
            ),
        )
    elif failure == "wait_signal":
        value.candles[-1].close = "10"
    elif failure == "quantity":
        body["quantity"] = "0.5"
    elif failure == "provider":
        body["provider"] = "oanda"
    elif failure == "version":
        body["strategy_version_id"] = str(uuid4())
        expected = 404
    else:
        broker.current_fingerprint = "b" * 64
        expected = 409
    response = signed_in.post("/api/v1/brokers/setup-assessments", json=body)
    assert response.status_code == expected, response.text
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0
    assert broker.submissions == [] and broker.cancellations == []


def test_request_id_reuse_cannot_change_context_or_duplicate_research(signed_in, setup):
    body, broker, _, _ = setup
    job = queue(signed_in, body)
    changed = signed_in.post("/api/v1/brokers/setup-assessments", json={**body, "quantity": "201"})
    assert changed.status_code == 409
    generic = signed_in.post(
        "/api/v1/analysis",
        json={
            "request_id": body["request_id"],
            "instrument_id": body["instrument_id"],
            "profile": body["profile"],
            "snapshot_id": job["request"]["broker_context"]["assessment_input"]["snapshot_id"],
        },
    )
    assert generic.status_code == 409 and broker.submissions == []


@pytest.mark.parametrize("change", ["expired", "account", "policy", "models"])
def test_changed_admission_stops_before_native_work(
    signed_in, setup, database, configured, monkeypatch, change
):
    body, broker, stamp, _ = setup
    job = queue(signed_in, body)
    if change == "expired":
        monkeypatch.setattr(broker_setup, "now", lambda: stamp + timedelta(minutes=6))
    elif change == "account":
        broker.current_fingerprint = "b" * 64
    elif change == "models":
        configured.write_text(
            '{"cloud":{"provider":"openai","quick_model":"other","deep_model":"other","api_key":"changed-secret"}}'
        )
    else:
        assert (
            signed_in.put(
                "/api/v1/brokers/alpaca/policy",
                json={
                    "request_id": str(uuid4()),
                    "expected_revision": 1,
                    "enabled": False,
                    "reason": "Pause the fixture demo policy",
                },
            ).status_code
            == 200
        )
    with Session(database[0]) as db:
        with pytest.raises(HTTPException):
            broker_setup.validate_admission(db, db.get(AnalysisJob, job["id"]))
    assert broker.submissions == []


def test_successful_report_is_separate_and_expired_evidence_remains_visible(
    signed_in, setup, database, monkeypatch
):
    body, broker, stamp, _ = setup
    job = queue(signed_in, body)
    payload = finish(database, job)
    response = signed_in.get(f"/api/v1/brokers/setup-assessments/{job['id']}")
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["assessment"]["decision"] == "approve" and report["evidence_current"]
    assert report["execution_authority"] is False and report["job"]["status"] == "succeeded"
    raw = signed_in.get(f"/api/v1/analysis-artifacts/{report['native_artifact_id']}").json()
    assert raw["payload"] == payload
    saved = signed_in.get("/api/v1/brokers/setup-assessments").json()
    assert [row["job"]["id"] for row in saved] == [job["id"]]
    monkeypatch.setattr(broker_setup, "now", lambda: stamp + timedelta(minutes=6))
    stale = signed_in.get(f"/api/v1/brokers/setup-assessments/{job['id']}").json()
    assert not stale["evidence_current"] and stale["assessment"]["decision"] == "approve"
    assert broker.submissions == []


def test_worker_expired_setup_never_starts_or_retries_model(
    signed_in, setup, database, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from tauric import worker

    body, broker, stamp, _ = setup
    job = queue(signed_in, body)
    with Session(database[0]) as db:
        assert analysis.claim(db) == job["id"]
    monkeypatch.setattr(broker_setup, "now", lambda: stamp + timedelta(minutes=6))
    monkeypatch.setattr(
        worker, "runtime_config", lambda: SimpleNamespace(runtime_directory=str(tmp_path))
    )
    monkeypatch.setattr(
        worker.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("Expired setup started a native model process"),
    )
    worker.process(job["id"], "0.5.2")
    failed = signed_in.get(f"/api/v1/brokers/setup-assessments/{job['id']}").json()
    assert failed["job"]["status"] == "failed" and failed["assessment"] is None
    assert "No model request" in failed["job"]["error"]
    assert queue(signed_in, body)["id"] == job["id"]
    assert broker.submissions == []


def test_broker_research_context_cannot_mix_with_other_research(signed_in, setup, database):
    from tauric.market_contracts import AnalysisRequest
    from tauric.models import Owner

    body, _, _, _ = setup
    job = queue(signed_in, body)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        for key in (
            "qualification_context",
            "autonomous_context",
            "scanner_context",
            "chat_context",
            "portfolio_research_context",
        ):
            with pytest.raises(HTTPException, match="own research context"):
                analysis.enqueue(
                    db,
                    owner,
                    AnalysisRequest(
                        request_id=uuid4(), instrument_id=body["instrument_id"], profile="cloud"
                    ),
                    broker_context=job["request"]["broker_context"],
                    **{key: {"fixture": True}},
                )


def test_setup_endpoints_require_owner_session_and_csrf(client):
    assert client.get("/api/v1/brokers/setup-assessments").status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert client.post("/api/v1/brokers/setup-preview", json={}).status_code == 403
    assert client.post("/api/v1/brokers/setup-assessments", json={}).status_code == 403


@pytest.mark.parametrize(
    "changes", [{"quantity": "201"}, {"execution_authority": True}, {"setup_id": str(uuid4())}]
)
def test_wrong_result_binding_is_rejected(signed_in, setup, database, changes):
    body, broker, _, _ = setup
    job = queue(signed_in, body)
    finish(database, job, **changes)
    assert signed_in.get(f"/api/v1/brokers/setup-assessments/{job['id']}").status_code == 409
    assert broker.submissions == []
