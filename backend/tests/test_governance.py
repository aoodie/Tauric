"""Isolated model fixtures test governance mechanics; no real AI or broker calls."""

import hashlib
import importlib.util
import json
import secrets
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_paper import INSTRUMENT, P, action
from test_paper import setup as setup

from tauric import governance
from tauric.config import runtime_config
from tauric.governance_contracts import GovernanceEnqueue
from tauric.models import (
    AnalysisArtifact,
    AnalysisJob,
    GatewayStatus,
    GovernanceJob,
    Owner,
    now,
)


@pytest.fixture
def ready(setup, database, monkeypatch, tmp_path):
    secret = tmp_path / "private.json"
    tokens = {k: secrets.token_urlsafe(48) for k in ("platform_token", "gateway_token")}
    secret.write_text(json.dumps(tokens))
    profile = tmp_path / "profiles.json"
    profiles = {
        k: dict(
            provider="openai_compatible",
            base_url="http://127.0.0.1:9/v1",
            quick_model="fixture-quick",
            deep_model="fixture-deep",
            api_key="fixture-private-key",
        )
        for k in ("cloud", "local")
    }
    profile.write_text(json.dumps(profiles))
    monkeypatch.setenv("TAURIC_OPENCLAW_PRIVATE_FILE", str(secret))
    monkeypatch.setenv("TAURIC_MODEL_PROFILES_FILE", str(profile))
    runtime_config.cache_clear()
    original = {
        "native_rating": "HOLD",
        "reports": {"final_trade_decision": "Isolated test fixture; daily USD context only."},
        "execution_authority": False,
        "governance": None,
    }
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        job = AnalysisJob(
            owner_id=owner.id,
            request_id=str(uuid4()),
            instrument_id=INSTRUMENT,
            profile="cloud",
            status="succeeded",
            request={"trade_date": now().date().isoformat()},
            progress=[],
        )
        db.add(job)
        db.flush()
        artifact = AnalysisArtifact(
            job_id=job.id,
            owner_id=owner.id,
            payload=original,
            checksum=hashlib.sha256(
                json.dumps(original, sort_keys=True, allow_nan=False).encode()
            ).hexdigest(),
        )
        db.add(artifact)
        db.flush()
        artifact_id = artifact.id
        db.add(
            GatewayStatus(
                id="openclaw",
                fingerprint=governance.routing(db, owner)["fingerprint"],
                heartbeat_at=now(),
            )
        )
        governance.heartbeat(db)
    return setup, tokens, artifact_id, original, profile, profiles


def queue(client, ready, route="cloud", original=True):
    prop = client.post(
        "/api/v1/paper/proposals",
        json={
            **action(),
            "deployment_id": ready[0][1]["id"],
            "stop_price": "90",
            "native_artifact_id": ready[2] if original else None,
        },
    )
    assert prop.status_code == 201, prop.text
    request = {**action(), "proposal_id": prop.json()["id"], "profile": route}
    queued = client.post("/api/v1/governance/jobs", json=request)
    return prop.json(), request, queued


def run_context(client, database, ready, job):
    with Session(database[0]) as db:
        claimed = governance.claim(db)
    assert claimed[0] == job["id"]
    context = client.get(
        f"/internal/openclaw/jobs/{job['id']}/context",
        headers={"Authorization": "Bearer " + ready[1]["platform_token"]},
    )
    assert context.status_code == 200, context.text
    return claimed, context.json()


def result(context, outcome="allow"):
    return dict(
        job_id=context["job_id"],
        context_checksum=context["context_checksum"],
        proposal_id=context["context"]["proposal"]["id"],
        policy_id=context["context"]["policy_id"],
        outcome=outcome,
        reason="Fixture assessment of explicit paper proposal only.",
    )


@pytest.mark.parametrize(
    "route,outcome",
    [("cloud", "allow"), ("local", "allow"), ("cloud", "reject"), ("local", "defer")],
)
def test_bound_review_to_plan_ledger_preserves_original(signed_in, database, ready, route, outcome):
    prop, req, response = queue(signed_in, ready, route)
    assert response.status_code == 201, response.text
    job = response.json()
    assert signed_in.post("/api/v1/governance/jobs", json=req).json()["id"] == job["id"]
    claimed, ctx = run_context(signed_in, database, ready, job)
    assert signed_in.get("/api/v1/paper").json()["openclaw_ready"] is True
    assert ctx["context"]["positions"] == []
    assert ctx["context"]["market_observation"]["observed_price"] == "100"
    with Session(database[0]) as db:
        assert governance.finish(
            db, *claimed, raw=json.dumps(result(ctx, outcome)), usage={"total_tokens": 15}
        )
        assert not governance.finish(db, *claimed, raw=json.dumps(result(ctx, outcome)))
    status = signed_in.get("/api/v1/governance").json()["jobs"][0]
    assert status["status"] == "succeeded"
    decision = signed_in.get(f"/api/v1/resources/trading_record/{status['decision_id']}").json()[
        "data"
    ]
    assert decision["payload"]["source"] == "openclaw"
    assert decision["payload"]["usage"] == {"total_tokens": 15}
    assert decision["payload"]["native_artifact_id"] == ready[2]
    plan = signed_in.post(f"/api/v1/paper/decisions/{status['decision_id']}/plan", json=action())
    assert plan.status_code == (201 if outcome == "allow" else 409), plan.text
    if outcome == "allow":
        fill_request = action()
        fill = signed_in.post(f"/api/v1/paper/plans/{plan.json()['id']}/execute", json=fill_request)
        assert fill.status_code == 201, fill.text
        assert fill.json()["payload"]["decision_id"] == status["decision_id"]
        assert (
            signed_in.post(
                f"/api/v1/paper/plans/{plan.json()['id']}/execute", json=fill_request
            ).json()["id"]
            == fill.json()["id"]
        )
        close_request = action()
        close = signed_in.post(
            f"/api/v1/paper/positions/{fill.json()['id']}/close", json=close_request
        )
        assert close.status_code == 201, close.text
        assert (
            signed_in.post(
                f"/api/v1/paper/positions/{fill.json()['id']}/close", json=close_request
            ).json()["id"]
            == close.json()["id"]
        )
        state = signed_in.get("/api/v1/paper").json()
        spec = importlib.util.spec_from_file_location(
            "real_workflow_verification",
            Path(__file__).resolve().parents[2] / "scripts/real_governance_verification.py",
        )
        verifier = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(verifier)
        inputs = (
            ready[0][0]["id"],
            prop,
            decision,
            plan.json(),
            fill.json(),
            close.json(),
            ready[2],
        )
        assert verifier.verify_journal(state, *inputs)["fill_count"] == 2
        changed = deepcopy(state)
        changed["accounts"][0]["cash"] = "1000000"
        with pytest.raises(AssertionError):
            verifier.verify_journal(changed, *inputs)
        changed = deepcopy(close.json())
        changed["payload"]["native_artifact_id"] = str(uuid4())
        changed["checksum"] = hashlib.sha256(
            json.dumps(changed["payload"], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        wrong_lineage_state = deepcopy(state)
        wrong_lineage_state["records"] = [
            changed if v["id"] == changed["id"] else v for v in wrong_lineage_state["records"]
        ]
        with pytest.raises(AssertionError):
            verifier.verify_journal(wrong_lineage_state, *inputs[:-2], changed, ready[2])
    report = signed_in.get(f"/api/v1/analysis-artifacts/{ready[2]}").json()
    assert report["payload"] == ready[3]
    public = signed_in.get(f"/api/v1/resources/governance_job/{job['id']}")
    assert public.status_code == 200 and "fixture-private-key" not in public.text
    assert ready[1]["gateway_token"] not in public.text


def test_no_report_or_unconfigured_gateway_cannot_queue(signed_in, ready, database):
    _, _, response = queue(signed_in, ready, original=False)
    assert response.status_code == 422
    with Session(database[0]) as db:
        db.get(GatewayStatus, "openclaw").heartbeat_at = now() - timedelta(seconds=31)
        db.commit()
    _, _, response = queue(signed_in, ready)
    assert response.status_code == 503


@pytest.mark.parametrize(
    "failure",
    [
        "malformed",
        "duplicate-field",
        "extra-order",
        "wrong-job",
        "no-tool",
        "policy",
        "key",
        "late",
    ],
)
def test_invalid_or_changed_context_never_grants_permission(signed_in, database, ready, failure):
    _, _, response = queue(signed_in, ready)
    job = response.json()
    claimed, ctx = run_context(signed_in, database, ready, job)
    payload = result(ctx)
    if failure == "extra-order":
        payload["quantity"] = "10"
    if failure == "wrong-job":
        payload["job_id"] = str(uuid4())
    if failure == "policy":
        acct = ready[0][0]
        assert (
            signed_in.put(
                f"/api/v1/paper/accounts/{acct['id']}/policy",
                json={
                    "expected_revision": 1,
                    "policy": {**P, "risk_percent": "0.5"},
                    "reason": "Fixture policy change",
                },
            ).status_code
            == 200
        )
    if failure == "key":
        values = ready[5]
        values["cloud"]["api_key"] = "rotated-fixture-key"
        ready[4].write_text(json.dumps(values))
    with Session(database[0]) as db:
        row = db.get(GovernanceJob, job["id"])
        if failure == "no-tool":
            row.context_read_at = None
            db.commit()
        if failure == "late":
            row.started_at = now() - timedelta(seconds=181)
            db.commit()
        serialized = json.dumps(payload)
        if failure == "duplicate-field":
            serialized = '{"outcome":"reject",' + serialized[1:]
        governance.finish(db, *claimed, raw="not json" if failure == "malformed" else serialized)
    assert signed_in.get(f"/api/v1/governance/jobs/{job['id']}").json()["status"] == "failed"
    assert not [
        r for r in signed_in.get("/api/v1/paper").json()["records"] if r["kind"] == "governance"
    ]


def test_queue_bindings_owner_race_and_restart(signed_in, database, ready):
    prop, request, response = queue(signed_in, ready)
    assert response.status_code == 201
    assert (
        signed_in.post("/api/v1/governance/jobs", json={**request, "profile": "local"}).status_code
        == 409
    )
    assert (
        signed_in.post(
            f"/api/v1/paper/proposals/{prop['id']}/review", json={**action(), "outcome": "allow"}
        ).status_code
        == 409
    )
    claimed, ctx = run_context(signed_in, database, ready, response.json())
    with Session(database[0]) as db:
        assert not governance.renew(db, claimed[0], str(uuid4()))
        row = db.get(GovernanceJob, claimed[0])
        row.heartbeat_at = now() - timedelta(seconds=46)
        db.commit()
        assert not governance.finish(db, *claimed, raw=json.dumps(result(ctx)))
        assert governance.claim(db) is None
        assert db.get(GovernanceJob, claimed[0]).status == "interrupted"
    retry = signed_in.post("/api/v1/governance/jobs", json={**request, "request_id": str(uuid4())})
    assert retry.status_code == 201
    with Session(database[0]) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE governance_jobs SET context='{}'"))
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("DELETE FROM governance_jobs"))


def test_concurrent_duplicate_requests_queue_once(signed_in, database, ready):
    prop = signed_in.post(
        "/api/v1/paper/proposals",
        json={
            **action(),
            "deployment_id": ready[0][1]["id"],
            "stop_price": "90",
            "native_artifact_id": ready[2],
        },
    ).json()
    request = GovernanceEnqueue.model_validate(
        {**action(), "proposal_id": prop["id"], "profile": "cloud"}
    )

    def send(_):
        with Session(database[0]) as db:
            return governance.enqueue(db, db.scalar(select(Owner)), request).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(send, range(2)))
    assert len(set(ids)) == 1


def test_private_runtime_only_exports_secrets_to_service(signed_in, ready):
    assert signed_in.get("/internal/openclaw/runtime-config").status_code == 401
    private = signed_in.get(
        "/internal/openclaw/runtime-config",
        headers={"Authorization": "Bearer " + ready[1]["platform_token"]},
    )
    assert (
        private.status_code == 200
        and private.json()["routes"]["cloud"]["api_key"] == "fixture-private-key"
    )
    assert "fixture-private-key" not in signed_in.get("/api/v1/governance").text
    assert (
        signed_in.post("/internal/openclaw/heartbeat", json={"fingerprint": "0" * 64}).status_code
        == 401
    )
    assert (
        signed_in.post(
            "/api/v1/governance/jobs",
            json={**action(), "proposal_id": str(uuid4()), "profile": "cloud", "outcome": "allow"},
        ).status_code
        == 422
    )


def test_shared_capital_is_captured_and_change_blocks_model_permission(signed_in, database, ready):
    from test_capital import allocation, change, strategy

    acct, _, _, dep_req = ready[0]
    root, _ = allocation(signed_in, acct, budget="5000")
    child, _ = allocation(signed_in, acct, "Governance pot", "2000", root)
    dep = strategy(signed_in, dep_req, child, root)
    prepared = ((acct, dep, ready[0][2], dep_req), *ready[1:])
    _, _, response = queue(signed_in, prepared)
    assert response.status_code == 201, response.text
    claimed, ctx = run_context(signed_in, database, prepared, response.json())
    assert len(ctx["context"]["capital_allocations"]) == 2
    assert ctx["context"]["deployment"]["capital_scope_id"] == child["id"]
    assert change(signed_in, child, budget="1500").status_code == 200
    with Session(database[0]) as db:
        assert governance.finish(db, *claimed, raw=json.dumps(result(ctx)))
    job = signed_in.get("/api/v1/governance").json()["jobs"][0]
    assert job["status"] == "failed" and job["decision_id"] is None
    assert "Capital allocations changed" in job["error"]


def test_paused_deployment_cannot_receive_model_permission(signed_in, database, ready):
    _, _, response = queue(signed_in, ready)
    assert response.status_code == 201
    claimed, context = run_context(signed_in, database, ready, response.json())
    dep = ready[0][1]
    paused = signed_in.put(
        f"/api/v1/paper/deployments/{dep['id']}/control",
        json={
            "expected_revision": 0,
            "enabled": False,
            "reason": "Pause while a model review is running",
        },
    )
    assert paused.status_code == 200
    with Session(database[0]) as db:
        assert governance.finish(db, *claimed, raw=json.dumps(result(context)))
    job = signed_in.get("/api/v1/governance").json()["jobs"][0]
    assert job["status"] == "failed" and job["decision_id"] is None
    assert "paused" in job["error"]
