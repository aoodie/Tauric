"""Activity reflects actual jobs, isolates owners and never forwards private payloads."""

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_management import action
from test_assistant_management import invocation as invocation
from test_assistant_management import ready as ready
from test_multifactor_scanner import multifactor_market as multifactor_market

from tauric import analysis, assistant_activity, paper
from tauric.models import AnalysisArtifact, AnalysisJob, AssistantRecord, Owner, now


def record(db, owner, operation, result, stamp=None, stage="completed", request_id=None):
    request_id = request_id or str(uuid4())
    if stage == "completed":
        record(db, owner, operation, {}, stamp, "requested", request_id)
    payload = {
        "inputs": {"parameters": {"token": "private-credential-fixture"}},
        "result": result,
        "channel": "webchat",
    }
    row = AssistantRecord(
        owner_id=owner.id,
        request_id=request_id,
        stage=stage,
        operation=operation,
        title="Recorded action",
        payload=payload,
        checksum=paper.checksum(payload),
        created_at=stamp or now(),
    )
    db.add(row)
    db.flush()
    return row


def test_opportunity_brief_uses_saved_high_scores_and_reconciles_same_invocation(
    ready, invocation, multifactor_market
):
    from test_independent_scanners import standalone
    from test_scanner import scan

    profile, _ = standalone(
        ready,
        universe=multifactor_market[:2],
        scoring_version="multifactor_v1",
        minimum_setup_score=0,
        minimum_volume_ratio="1.5",
        lookback=20,
        max_analyses_per_day=0,
    )
    scan(ready, profile)
    response = action(ready, invocation, "propose_high_conviction_candidates", {"limit": 1})
    assert response.status_code == 200, response.text
    brief = response.json()
    result = brief["result"]
    assert len(result["opportunities"]) == 1 and result["total_high_score"] == 2
    assert result["execution_authority"] is False
    assert (
        action(ready, invocation, "propose_high_conviction_candidates", {"limit": 1}).json()["id"]
        == brief["id"]
    )
    feed = ready.get("/api/v1/assistant/activities").json()
    item = next(i for i in feed["items"] if i["resource_id"] == brief["id"])
    assert item["actor"] == "openclaw" and item["category"] == "proposal"
    assert len(item["symbols"]) == 1 and len(item["details"]["opportunities"]) == 1


def test_activity_requires_owner_and_bounded_query(client, signed_in):
    client.cookies.clear()
    assert client.get("/api/v1/assistant/activities").status_code == 401
    login = client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert login.status_code == 200
    assert client.get("/api/v1/assistant/activities?limit=101").status_code == 422
    assert client.get("/api/v1/assistant/activities?cursor=bad-cursor").status_code == 422


def test_pagination_ties_owner_isolation_and_private_projection(signed_in, database):
    stamp = now() - timedelta(minutes=2)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        for index in range(7):
            record(
                db,
                owner,
                "save_research_note",
                {"id": str(index), "token": "private-secret", "raw_error": "private-secret"},
                stamp,
            )
        db.commit()
        # Production deliberately permits only one owner; an unrelated identity
        # must still be unable to read that owner's records through the projection.
        unrelated = assistant_activity.stream(db, SimpleNamespace(id=str(uuid4())))
        assert not unrelated.items and not unrelated.current_work
    identities, cursor = [], None
    for _ in range(5):
        response = signed_in.get(
            "/api/v1/assistant/activities",
            params={"limit": 2, **({"cursor": cursor} if cursor else {})},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        assert "private-secret" not in response.text
        assert "private-credential-fixture" not in response.text
        identities.extend(v["id"] for v in page["items"])
        assert all(
            v["actor"] == "openclaw" and v["verification"] == "checksum_verified"
            for v in page["items"]
        )
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(identities) == 7 and len(set(identities)) == 7


def test_request_receipt_follows_actual_research_outcome(signed_in, database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        job = AnalysisJob(
            owner_id=owner.id,
            request_id=str(uuid4()),
            instrument_id="binance:spot:BTCUSDT",
            profile="local",
            request={},
            status="queued",
        )
        db.add(job)
        db.flush()
        saved = record(db, owner, "research_candidate", {"id": job.id, "status": "queued"})
        saved_id, job_id = saved.id, job.id
        db.commit()
    response = signed_in.get("/api/v1/assistant/activities").json()
    receipt = next(v for v in response["items"] if v["id"] == "assistant:" + saved_id)
    assert receipt["status"] == "queued"
    assert any(v["id"] == receipt["id"] for v in response["current_work"])
    with Session(database[1]) as db:
        job = db.get(AnalysisJob, job_id)
        job.status = "running"
        job.heartbeat_at = now()
        db.commit()
    response = signed_in.get("/api/v1/assistant/activities").json()
    assert (
        next(v for v in response["items"] if v["id"] == "assistant:" + saved_id)["status"]
        == "running"
    )
    with Session(database[1]) as db:
        # Use the real native writer: its checksum format differs from receipts.
        payload = {"reports": {"fixture": "Synthetic research result — café"}}
        assert analysis.finish(db, job_id, payload)
        artifact = db.scalar(select(AnalysisArtifact).where(AnalysisArtifact.job_id == job_id))
        assert artifact.checksum != paper.checksum(payload)
    response = signed_in.get("/api/v1/assistant/activities").json()
    assert (
        next(v for v in response["items"] if v["id"] == "assistant:" + saved_id)["status"]
        == "completed"
    )
    observed = signed_in.post(
        "/api/v1/assistant/activities/observe",
        json={"identities": ["assistant:" + saved_id, "research:" + job_id]},
    )
    assert observed.status_code == 200, observed.text
    assert all(v["status"] == "completed" for v in observed.json())
    assert (
        signed_in.post(
            "/api/v1/assistant/activities/observe",
            json={"identities": ["research:" + str(uuid4())]},
        ).json()
        == []
    )
    assert (
        signed_in.post(
            "/api/v1/assistant/activities/observe", json={"identities": ["unknown:bad"]}
        ).status_code
        == 422
    )
    assert (
        signed_in.post(
            "/api/v1/assistant/activities/observe",
            json={"identities": ["research:" + job_id] * 101},
        ).status_code
        == 422
    )


def test_completed_native_job_without_artifact_remains_blocked(signed_in, database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        job = AnalysisJob(
            owner_id=owner.id,
            request_id=str(uuid4()),
            instrument_id="binance:spot:BTCUSDT",
            profile="local",
            request={},
            status="queued",
        )
        db.add(job)
        db.flush()
        saved = record(db, owner, "research_candidate", {"id": job.id, "status": "queued"})
        identity, job_id = saved.id, job.id
        db.commit()
        job.status, job.finished_at = "succeeded", now()
        db.commit()
    response = signed_in.get("/api/v1/assistant/activities")
    assert response.status_code == 200, response.text
    feed = response.json()
    assert (
        next(row for row in feed["items"] if row["id"] == "assistant:" + identity)["status"]
        == "blocked"
    )
    assert any(
        row["id"] == "research:" + job_id and row["status"] == "blocked"
        for row in feed["current_work"]
    )


def test_unresolved_intent_is_not_completed_or_failed(signed_in, database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        row = record(
            db, owner, "create_strategy", {}, now() - timedelta(minutes=4), stage="requested"
        )
        identity = row.id
        db.commit()
    page = signed_in.get("/api/v1/assistant/activities").json()
    assert (
        next(v for v in page["items"] if v["id"] == "assistant:" + identity)["status"]
        == "reconciling"
    )


def test_corrupt_receipt_cannot_be_projected():
    with pytest.raises(HTTPException, match="integrity"):
        assistant_activity.verified(SimpleNamespace(payload={"result": {}}, checksum="f" * 64))


@pytest.mark.parametrize("compact_checksum", [True, False])
def test_native_artifact_rejects_receipt_format_and_corrupt_checksum(compact_checksum):
    payload = {"reports": {"fixture": "Native checksum format"}}
    artifact = AnalysisArtifact(
        payload=payload, checksum=paper.checksum(payload) if compact_checksum else "f" * 64
    )
    with pytest.raises(HTTPException, match="integrity"):
        assistant_activity.verified(artifact)
