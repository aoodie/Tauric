"""Chat orchestration cannot promote a backtest or a non-BUY native report into approval."""

import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from tauric import analysis, assistant, autonomous
from tauric.assistant_contracts import PaperStrategyProposal
from tauric.models import AnalysisArtifact, AnalysisJob, now


@pytest.fixture
def evidence(monkeypatch):
    owner = SimpleNamespace(id="owner")
    dep = SimpleNamespace(instrument_id="instrument")
    settings = {"profile_fingerprint": "fixture", "analysis_settings": {"max_debate_rounds": 1}}
    request = {
        "mode": "current_daily_research",
        "trade_date": now().date().isoformat(),
        "upstream_revision": analysis.UPSTREAM_REVISION,
        "profile_fingerprint": settings["profile_fingerprint"],
        "settings": settings["analysis_settings"],
    }
    job = SimpleNamespace(
        id="job",
        owner_id=owner.id,
        instrument_id=dep.instrument_id,
        status="succeeded",
        request=request,
        profile="local",
        finished_at=now(),
    )
    payload = {
        "request": request,
        "native_rating": "BUY",
        "execution_authority": False,
        "governance": None,
        "reports": {k: "Complete fixture report" for k in autonomous.REPORTS},
    }
    artifact = SimpleNamespace(
        id="artifact",
        owner_id=owner.id,
        job_id=job.id,
        payload=payload,
        checksum=hashlib.sha256(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
    )
    db = SimpleNamespace(
        get=lambda cls, key: (
            artifact if cls is AnalysisArtifact else job if cls is AnalysisJob else None
        )
    )
    monkeypatch.setattr(autonomous, "settings_snapshot", lambda *args: settings)
    return db, owner, dep, artifact, job, settings


def test_complete_buy_evidence_remains_read_only(evidence):
    db, owner, dep, artifact, *_ = evidence
    assert assistant.native_paper_evidence(db, owner, dep, artifact.id) is artifact
    assert (
        artifact.payload["execution_authority"] is False and artifact.payload["governance"] is None
    )


@pytest.mark.parametrize(
    "failure",
    [
        "HOLD",
        "SELL",
        "REVIEW",
        "qualification",
        "stale",
        "wrong_instrument",
        "changed_configuration",
        "tampered",
        "missing_reports",
    ],
)
def test_native_boundary_rejects_nonapproval_and_changed_evidence(evidence, failure):
    db, owner, dep, artifact, job, settings = evidence
    if failure in {"HOLD", "SELL", "REVIEW"}:
        artifact.payload["native_rating"] = failure
    elif failure == "qualification":
        job.request["mode"] = "strategy_qualification"
    elif failure == "stale":
        job.finished_at = now() - timedelta(days=2)
    elif failure == "wrong_instrument":
        job.instrument_id = "other"
    elif failure == "changed_configuration":
        settings["profile_fingerprint"] = "changed"
    elif failure == "tampered":
        artifact.checksum = "0" * 64
    elif failure == "missing_reports":
        artifact.payload["reports"] = {}
    if failure != "tampered":
        artifact.checksum = hashlib.sha256(
            json.dumps(artifact.payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
    with pytest.raises(HTTPException) as rejected:
        assistant.native_paper_evidence(db, owner, dep, artifact.id)
    assert rejected.value.status_code == 409


def test_entry_contract_cannot_accept_model_claimed_approval():
    with pytest.raises(ValidationError):
        PaperStrategyProposal.model_validate(
            {
                "request_id": "12548e76-5c3c-4053-bd30-4d189520832e",
                "deployment_id": "12548e76-5c3c-4053-bd30-4d189520832e",
                "stop_price": "90",
                "native_artifact_id": "12548e76-5c3c-4053-bd30-4d189520832e",
                "strategy_run_id": "12548e76-5c3c-4053-bd30-4d189520832e",
                "backtest_id": "12548e76-5c3c-4053-bd30-4d189520832e",
                "reason": "Owner paper request",
                "native_approved": True,
            }
        )
