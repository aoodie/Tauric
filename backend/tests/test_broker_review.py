"""Bound demo permissions and one-shot delivery, using offline broker/model facts."""

import asyncio
import json
import secrets
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_broker_setup import configured as configured
from test_broker_setup import finish as native_finish
from test_broker_setup import lifecycle as lifecycle
from test_broker_setup import queue as native_queue
from test_broker_setup import setup as setup

from tauric import broker_execution, broker_review, governance, governance_worker, openclaw_api
from tauric.broker_models import BrokerOrderIntent, BrokerReviewDecision, BrokerReviewJob
from tauric.config import runtime_config
from tauric.models import GatewayStatus, Owner


@pytest.fixture
def ready(signed_in, setup, database, monkeypatch, tmp_path):
    body, broker, stamp, value = setup
    secret = tmp_path / "private.json"
    tokens = {key: secrets.token_urlsafe(48) for key in ("platform_token", "gateway_token")}
    secret.write_text(json.dumps(tokens))
    monkeypatch.setenv("TAURIC_OPENCLAW_PRIVATE_FILE", str(secret))
    runtime_config.cache_clear()
    for module in (broker_review, governance, openclaw_api, broker_execution):
        monkeypatch.setattr(module, "now", lambda: stamp)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        db.add(
            GatewayStatus(
                id="openclaw",
                fingerprint=governance.routing(db, owner)["fingerprint"],
                heartbeat_at=stamp,
                capabilities=["paper_governance", "broker_entry_review"],
            )
        )
        governance.heartbeat(db)
        broker_execution.heartbeat(db)
    native = native_queue(signed_in, body)
    native_finish(database, native)
    submit = broker.submit

    def guarded(order):
        if broker.before_mutation:
            broker.before_mutation()
        return submit(order)

    monkeypatch.setattr(broker, "submit", guarded)
    return native, broker, stamp, tokens, value


def queue(client, ready, submit=True):
    body = {
        "request_id": str(uuid4()),
        "assessment_job_id": ready[0]["id"],
        "profile": "cloud",
        "submit_on_allow": submit,
        "reason": "Offline bound review test",
    }
    response = client.post("/api/v1/brokers/reviews", json=body)
    assert response.status_code == 201, response.text
    return body, response.json()


def complete(client, database, ready, job, outcome="allow", read=True, changes=None, raw=None):
    with Session(database[0]) as db:
        claimed = broker_review.claim(db)
    assert claimed[0] == job["id"]
    if read:
        response = client.get(
            f"/internal/openclaw/broker-jobs/{job['id']}/context",
            headers={"Authorization": "Bearer " + ready[3]["platform_token"]},
        )
        assert response.status_code == 200, response.text
        assert response.json()["context"]["authority"] == "read_only_demo_broker_entry"
    with Session(database[0]) as db:
        active = db.get(BrokerReviewJob, job["id"])
        data = {
            **broker_review.result_binding(active),
            "outcome": outcome,
            "reason": "Offline exact review",
            **(changes or {}),
        }
        assert broker_review.finish(
            db,
            *claimed,
            raw=raw if raw is not None else json.dumps(data),
            usage={"total_tokens": 12, "api_key": "never persist", "prompt_tokens": True},
        )
        assert not broker_review.finish(db, *claimed, raw=json.dumps(data))
    return next(
        row for row in client.get("/api/v1/brokers/reviews").json() if row["id"] == job["id"]
    )


def submit(client, job):
    return client.post(
        f"/api/v1/brokers/reviews/{job['id']}/submit",
        json={"request_id": str(uuid4()), "reason": "Consume exact demo permission"},
    )


def test_allow_delivers_one_exact_order_and_preserves_external_holdings(signed_in, ready, database):
    body, job = queue(signed_in, ready)
    assert signed_in.post("/api/v1/brokers/reviews", json=body).json()["id"] == job["id"]
    assert (
        signed_in.post(
            "/api/v1/brokers/reviews", json={**body, "submit_on_allow": False}
        ).status_code
        == 409
    )
    finished = complete(signed_in, database, ready, job)
    assert finished["status"] == "succeeded" and finished["decision"]["outcome"] == "allow"
    assert finished["usage"] == {"total_tokens": 12} and ready[1].submissions == []
    # A restart between review finalization and delivery is recovered from durable rows.
    with Session(database[0]) as db:
        assert broker_execution.process_one(db)
    intent = signed_in.get("/api/v1/brokers").json()["orders"][0]
    assert intent["status"] == "filled"
    assert intent["request"]["quantity"] == "200" and intent["request"]["stop_loss"] == "9"
    assert intent["request"]["take_profit"] == "18.03"
    assert submit(signed_in, job).json()["id"] == intent["id"]
    assert submit(signed_in, job).json()["id"] == intent["id"]
    with Session(database[0]) as db:
        assert not broker_review.deliver_one(db)
        assert db.scalar(select(func.count()).select_from(BrokerOrderIntent)) == 1
        stored = db.get(BrokerOrderIntent, intent["id"])
        assert stored.review_decision_id == finished["decision"]["id"]
        assert stored.payload["assessment_job_id"] == ready[0]["id"]
    assert len(ready[1].submissions) == 1
    position = signed_in.post(f"/api/v1/brokers/orders/{intent['id']}/position").json()
    assert position["entry_quantity"] == "200" and position["broker_holding_quantity"] == "207"
    closed = signed_in.post(
        f"/api/v1/brokers/orders/{intent['id']}/close",
        json={
            "request_id": str(uuid4()),
            "quantity": "200",
            "reason": "Close only this reviewed entry",
        },
    )
    assert closed.status_code == 200 and closed.json()["status"] == "filled", closed.text
    assert ready[1].positions()[0].quantity == "7"
    assert (
        signed_in.get("/api/v1/brokers/journal").json()["positions"][0]["realized_gross_pnl"]
        == "2000"
    )
    duplicate = signed_in.post("/api/v1/brokers/reviews", json={**body, "request_id": str(uuid4())})
    assert duplicate.status_code == 409


@pytest.mark.parametrize("outcome", ["reject", "defer"])
def test_non_allow_never_delivers(signed_in, ready, database, outcome):
    _, job = queue(signed_in, ready)
    assert complete(signed_in, database, ready, job, outcome)["decision"]["outcome"] == outcome
    with Session(database[0]) as db:
        assert not broker_review.deliver_one(db)
    assert submit(signed_in, job).status_code == 422 and ready[1].submissions == []


@pytest.mark.parametrize(
    "bad", ["quantity", "provider", "context", "missing_read", "duplicate", "oversize"]
)
def test_invalid_or_unread_review_cannot_issue_permission(signed_in, ready, database, bad):
    _, job = queue(signed_in, ready)
    changes = (
        {"quantity": "201"}
        if bad == "quantity"
        else {"provider": "oanda"}
        if bad == "provider"
        else {"context_checksum": "b" * 64}
        if bad == "context"
        else None
    )
    raw = (
        '{"outcome":"allow","outcome":"reject"}'
        if bad == "duplicate"
        else "x" * 10001
        if bad == "oversize"
        else None
    )
    result = complete(
        signed_in, database, ready, job, read=bad != "missing_read", changes=changes, raw=raw
    )
    assert result["status"] == "failed" and result["decision"] is None
    with Session(database[0]) as db:
        assert broker_review.claim(db) is None
        assert db.scalar(select(func.count()).select_from(BrokerReviewDecision)) == 0
    assert submit(signed_in, job).status_code == 409 and ready[1].submissions == []


@pytest.mark.parametrize(
    "change", ["policy", "expired", "quote", "holdings", "cash", "connection", "models"]
)
def test_changed_execution_context_is_durably_rejected_without_order(
    signed_in, ready, database, monkeypatch, configured, change
):
    _, job = queue(signed_in, ready)
    complete(signed_in, database, ready, job)
    broker, stamp = ready[1:3]
    if change == "policy":
        response = signed_in.put(
            "/api/v1/brokers/alpaca/policy",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "enabled": False,
                "reason": "Pause before approved delivery",
            },
        )
        assert response.status_code == 200, response.text
    elif change == "expired":
        monkeypatch.setattr(broker_review, "now", lambda: stamp + timedelta(minutes=6))
    elif change == "quote":
        quote = broker.quote
        monkeypatch.setattr(
            broker,
            "quote",
            lambda name: quote(name).model_copy(
                update={"observed_at": stamp - timedelta(seconds=90)}
            ),
        )
    elif change == "holdings":
        broker.external += 1
    elif change == "cash":
        account = broker.account
        monkeypatch.setattr(
            broker, "account", lambda: account().model_copy(update={"cash": "99999"})
        )
    elif change == "connection":
        broker.current_fingerprint = "b" * 64
    else:
        configured.write_text(
            '{"cloud":{"provider":"openai","quick_model":"changed","deep_model":"changed","api_key":"private"}}'
        )
    response = submit(signed_in, job)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "rejected" and broker.submissions == []
    with Session(database[0]) as db:
        assert not broker_review.deliver_one(db)
    assert submit(signed_in, job).json()["id"] == response.json()["id"]


def test_gateway_capability_required_and_private_context_scoped(signed_in, ready, database):
    native = ready[0]
    assert (
        signed_in.get(f"/internal/openclaw/broker-jobs/{native['id']}/context").status_code == 401
    )
    with Session(database[0]) as db:
        db.get(GatewayStatus, "openclaw").capabilities = ["paper_governance"]
        db.commit()
    response = signed_in.post(
        "/api/v1/brokers/reviews",
        json={
            "request_id": str(uuid4()),
            "assessment_job_id": native["id"],
            "profile": "cloud",
            "reason": "Missing tool must block review",
        },
    )
    assert response.status_code == 503 and ready[1].submissions == []


def test_terminal_reviews_and_order_lineage_are_immutable(signed_in, ready, database):
    _, job = queue(signed_in, ready, submit=False)
    complete(signed_in, database, ready, job)
    response = submit(signed_in, job)
    assert response.status_code == 200, response.text
    with Session(database[0]) as db:
        for statement in [
            "UPDATE broker_review_jobs SET context='{}'",
            "UPDATE broker_review_jobs SET status='queued'",
            "DELETE FROM broker_review_jobs",
            "TRUNCATE broker_review_jobs CASCADE",
            "UPDATE broker_review_decisions SET payload='{}'",
            "DELETE FROM broker_review_decisions",
            "UPDATE broker_order_intents SET review_decision_id=NULL",
        ]:
            with pytest.raises(DBAPIError), db.begin_nested():
                db.execute(text(statement))
    assert len(ready[1].submissions) == 1


def test_expired_worker_lease_does_not_retry_model_or_trade(
    signed_in, ready, database, monkeypatch
):
    _, job = queue(signed_in, ready)
    with Session(database[0]) as db:
        claimed = broker_review.claim(db)
        assert claimed[0] == job["id"]
    monkeypatch.setattr(broker_review, "now", lambda: ready[2] + timedelta(seconds=46))
    with Session(database[0]) as db:
        assert not broker_review.renew(db, *claimed)
        assert broker_review.claim(db) is None
        assert db.get(BrokerReviewJob, job["id"]).status == "interrupted"
    assert ready[1].submissions == []


def test_worker_uses_isolated_agent_and_bound_context(signed_in, ready, database, monkeypatch):
    _, job = queue(signed_in, ready)
    with Session(database[0]) as db:
        claimed = broker_review.claim(db)
    calls = []

    async def gateway(body, timeout):
        calls.append(body)
        assert body["model"] == "openclaw:broker-governor-cloud"
        binding = json.loads(body["messages"][1]["content"])
        assert binding["quantity"] == "200" and "response_schema" in binding
        context = signed_in.get(
            f"/internal/openclaw/broker-jobs/{job['id']}/context",
            headers={"Authorization": "Bearer " + ready[3]["platform_token"]},
        )
        assert context.status_code == 200
        binding.pop("response_schema")
        return json.dumps({**binding, "outcome": "allow", "reason": "Offline agent response"}), {
            "total_tokens": 9
        }

    monkeypatch.setattr(governance_worker, "gateway_call", gateway)
    asyncio.run(governance_worker.process(*claimed, mode="broker"))
    assert len(calls) == 1
    result = signed_in.get("/api/v1/brokers/reviews").json()[0]
    assert result["status"] == "succeeded" and result["usage"] == {"total_tokens": 9}
    assert ready[1].submissions == []


@pytest.mark.parametrize("missing", [False, True])
def test_unknown_submission_never_repeats_even_if_broker_cannot_find_it(
    signed_in, ready, database, monkeypatch, missing
):
    from tauric.broker_adapters import BrokerError

    _, job = queue(signed_in, ready)
    complete(signed_in, database, ready, job)
    real_submit = ready[1].submit

    def lost(order):
        real_submit(order)
        raise BrokerError("Offline lost broker response", unknown=True)

    monkeypatch.setattr(ready[1], "submit", lost)
    response = submit(signed_in, job)
    assert response.status_code == 200 and response.json()["status"] == "unknown", response.text
    intent = response.json()
    if missing:
        ready[1].orders.clear()
    for _ in range(2):
        assert submit(signed_in, job).json()["id"] == intent["id"]
        observed = signed_in.post(f"/api/v1/brokers/orders/{intent['id']}/reconcile")
        assert observed.json()["status"] == ("unknown" if missing else "filled")
        with Session(database[0]) as db:
            assert not broker_review.deliver_one(db)
    assert len(ready[1].submissions) == 1


def test_sql_rejects_a_changed_bound_decision(signed_in, ready, database):
    from copy import deepcopy

    from tauric.native_broker_setup import checksum

    _, job = queue(signed_in, ready)
    with Session(database[0]) as db:
        broker_review.claim(db)
        active = db.get(BrokerReviewJob, job["id"])
        evidence = active.context["setup_evidence"]
        payload = {
            "authority": "demo_broker_entry",
            "source": "openclaw",
            "outcome": "allow",
            "approved_order": active.context["approved_order"],
            "job_id": active.id,
            "context_checksum": active.checksum,
            "expires_at": active.context["expires_at"],
            "account_fingerprint": evidence["account"]["account_fingerprint"],
            "connection_revision": evidence["connection_revision"],
        }
        for key, value in [
            ("context_checksum", "f" * 64),
            ("approved_order", {**payload["approved_order"], "quantity": "201"}),
            ("account_fingerprint", "f" * 64),
            ("source", "owner"),
            ("outcome", "unknown"),
        ]:
            invalid = deepcopy(payload)
            invalid[key] = value
            with pytest.raises(DBAPIError), db.begin_nested():
                db.add(
                    BrokerReviewDecision(
                        id=str(uuid4()),
                        owner_id=active.owner_id,
                        job_id=active.id,
                        assessment_job_id=active.assessment_job_id,
                        native_artifact_id=active.context["native_original"]["id"],
                        policy_version_id=evidence["policy_id"],
                        provider="alpaca",
                        payload=invalid,
                        checksum=checksum(invalid),
                    )
                )
                db.flush()
    assert ready[1].submissions == []


def test_paper_worker_cannot_claim_broker_review_and_only_one_review_runs(
    signed_in, ready, database, setup
):
    from tauric import review_queue

    _, first = queue(signed_in, ready)
    body = {**setup[0], "request_id": str(uuid4())}
    native = native_queue(signed_in, body)
    native_finish(database, native)
    second_ready = (native, *ready[1:])
    _, second = queue(signed_in, second_ready)
    with Session(database[0]) as db:
        assert review_queue.active_count(db) == 2
        assert governance.claim(db) is None
        claimed = broker_review.claim(db)
        assert claimed[0] == first["id"]
        assert broker_review.claim(db) is None
        assert governance.claim(db) is None
        assert db.get(BrokerReviewJob, second["id"]).status == "queued"
    assert ready[1].submissions == []


def test_owner_cannot_read_or_consume_another_owners_review(signed_in, ready, database):
    from fastapi import HTTPException

    _, job = queue(signed_in, ready, submit=False)
    result = complete(signed_in, database, ready, job)
    foreign = Owner(id=str(uuid4()), username="foreign-owner", password_hash="invalid")
    with Session(database[0]) as db:
        for reader, identifier in (
            (broker_review.checked, job["id"]),
            (broker_review.decision, result["decision"]["id"]),
        ):
            with pytest.raises(HTTPException) as failure:
                reader(db, foreign, identifier)
            assert failure.value.status_code == 404
    assert ready[1].submissions == []


def test_competing_review_deliveries_consume_one_permission_once(signed_in, ready, database):
    from concurrent.futures import ThreadPoolExecutor

    from tauric.broker_contracts import BrokerReviewedEntrySubmit

    _, job = queue(signed_in, ready)
    reviewed = complete(signed_in, database, ready, job)

    def consume():
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            return broker_review.prepare_entry(
                db,
                owner,
                reviewed["decision"]["id"],
                BrokerReviewedEntrySubmit(
                    request_id=uuid4(), reason="Competing recovery of this one entry"
                ),
            ).id

    with ThreadPoolExecutor(max_workers=3) as pool:
        values = list(pool.map(lambda _: consume(), range(3)))
    assert len(set(values)) == 1 and len(ready[1].submissions) == 1
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(BrokerOrderIntent)) == 1


def fx_ready(signed_in, database, configured, monkeypatch, tmp_path):
    import forex_supplement_fixture
    import test_native_fx
    from test_broker_setup_fx import fx_setup

    from tauric import (
        analysis,
        broker_setup,
        forex_macro,
        forex_sources,
        market,
        native_broker_setup,
        native_fx,
    )
    from tauric.models import AnalysisJob

    original_refresh = market.refresh
    body, broker, stamp, intraday = fx_setup(signed_in, database, monkeypatch)
    monkeypatch.setattr(native_fx, "now", lambda: stamp)
    monkeypatch.setattr(forex_macro, "now", lambda: stamp)
    monkeypatch.setattr(forex_sources, "now", lambda: stamp)
    monkeypatch.setattr(forex_supplement_fixture, "now", lambda: stamp)
    monkeypatch.setattr(test_native_fx, "now", lambda: stamp)
    monkeypatch.setattr(market, "now", lambda: stamp)
    monkeypatch.setattr(
        market.PROVIDERS["oanda_v20"],
        "collect",
        lambda instrument, interval, **kwargs: test_native_fx.daily_fixture(instrument, stamp),
    )
    monkeypatch.setattr(
        market,
        "refresh",
        lambda db, instrument_id, interval, **kwargs: (
            intraday
            if interval == "1m"
            else original_refresh(db, instrument_id, interval, **kwargs)
        ),
    )
    monkeypatch.setattr(forex_macro, "collect", test_native_fx.policy_fixture)
    monkeypatch.setattr(
        forex_sources, "collect", lambda db, spec, **kwargs: forex_supplement_fixture.fixture(spec)
    )
    secret = tmp_path / "private-fx.json"
    tokens = {key: secrets.token_urlsafe(48) for key in ("platform_token", "gateway_token")}
    secret.write_text(json.dumps(tokens))
    monkeypatch.setenv("TAURIC_OPENCLAW_PRIVATE_FILE", str(secret))
    runtime_config.cache_clear()
    for module in (
        analysis,
        broker_setup,
        native_broker_setup,
        broker_review,
        governance,
        openclaw_api,
        broker_execution,
    ):
        monkeypatch.setattr(module, "now", lambda: stamp)
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
        owner = db.scalar(select(Owner))
        db.add(
            GatewayStatus(
                id="openclaw",
                fingerprint=governance.routing(db, owner)["fingerprint"],
                heartbeat_at=stamp,
                capabilities=["broker_entry_review"],
            )
        )
        governance.heartbeat(db)
        broker_execution.heartbeat(db)
    native = native_queue(signed_in, {**body, "profile": "cloud"})
    with Session(database[0]) as db:
        active = db.get(AnalysisJob, analysis.claim(db))
        from test_native_broker_setup import Model

        from tauric.native_runner import artifact

        payload = artifact(
            {"final_trade_decision": "Original synthetic FX REVIEW report"},
            "REVIEW",
            active.request,
            active.request["settings"],
            "0.5.2",
        )
        payload["runtime"] = "Tauric Forex daily extension / TradingAgents LLM clients"
        payload["adapter_version"] = native_fx.ADAPTER_VERSION
        payload["forex_evidence"] = native_fx.evidence(
            db, active.request["instrument"], active.request["forex_context"]
        )
        payload["broker_setup_assessment"] = native_broker_setup.assess(
            Model(), active.request["broker_context"], payload["reports"], "REVIEW"
        )
        assert analysis.finish(db, active.id, payload=payload)
    return native, broker, stamp, tokens, intraday


def test_oanda_review_preserves_native_fx_rating_units_and_home_currency(
    signed_in, database, configured, monkeypatch, tmp_path
):
    from tauric.broker_contracts import BrokerOrderView
    from tauric.models import AnalysisArtifact

    native, broker, stamp, tokens, _ = fx_ready(
        signed_in, database, configured, monkeypatch, tmp_path
    )
    local_ready = (native, broker, stamp, tokens, None)
    _, job = queue(signed_in, local_ready)
    finished = complete(signed_in, database, local_ready, job)
    assert finished["status"] == "succeeded"
    planned = finished["decision"]["approved_order"]
    assert (
        planned["quantity"],
        planned["stop_loss"],
        planned["take_profit"],
        planned["time_in_force"],
    ) == ("2500", "1.0899", "1.15065", "fok")

    def pending(order):
        broker.before_mutation()
        broker.submissions.append(order.client_order_id)
        return BrokerOrderView(
            provider="oanda",
            broker_order_id="offline-fx-42",
            client_order_id=order.client_order_id,
            symbol="EUR_USD",
            side="buy",
            requested_quantity=order.quantity,
            filled_quantity="0",
            average_fill_price=None,
            status="pending",
            broker_status="PENDING",
            observed_at=stamp,
        )

    monkeypatch.setattr(broker, "submit", pending)
    intent = submit(signed_in, job)
    assert intent.status_code == 200 and intent.json()["status"] == "pending", intent.text
    assert len(broker.submissions) == 1 and broker.positions()[0].quantity == "3"
    with Session(database[0]) as db:
        review = db.get(BrokerReviewJob, job["id"])
        original = db.get(AnalysisArtifact, review.context["native_original"]["id"])
        assert original.payload["native_rating"] == "REVIEW"
        assert review.context["fresh_broker_evidence"]["account"]["currency"] == "GBP"
