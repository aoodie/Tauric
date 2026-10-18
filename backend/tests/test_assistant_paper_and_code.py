"""Assistant changes have durable receipts and cannot substitute evidence or orders."""

import difflib
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_management import action
from test_assistant_management import invocation as invocation
from test_chat import ready as ready
from test_paper import setup as setup
from test_strategy_rules import configured
from test_strategy_rules import rule_feed as rule_feed

from tauric import analysis, autonomous, governance
from tauric.models import AnalysisArtifact, AnalysisJob, Owner, ScannerAnalysis, TradingRecord, now


def test_applied_code_workspace_is_idempotent_and_does_not_touch_source(ready, invocation):
    path = "backend/tauric/code_workspace.py"
    source = Path(__file__).resolve().parents[2] / path
    original = source.read_text()
    changed = original.replace(
        "Apply exact-context code edits", "Stage exact-context code edits", 1
    )
    params = {
        "title": "Owner code change",
        "hypothesis": "Clarify the workspace purpose",
        "evidence": ["Current source checksum"],
        "counter_evidence": ["Runtime changes need separate release validation"],
        "test_plan": ["Run code workspace tests"],
        "decision": "test",
        "files": [{"path": path, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
        "changes": ["Clarify the module description"],
        "patch": "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                changed.splitlines(keepends=True),
                fromfile="a/" + path,
                tofile="b/" + path,
            )
        ),
    }
    first = action(ready, invocation, "stage_upgrade", params)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "review_required"
    result = first.json()["result"]
    assert result["workspace_files"][path]["content"] == changed
    assert result["runtime_code_changed"] is False and result["tests_executed"] is False
    assert source.read_text() == original
    assert action(ready, invocation, "stage_upgrade", params).json()["id"] == first.json()["id"]


@pytest.mark.parametrize("stage_from_candidate", [False, True])
def test_paper_evaluation_needs_matching_backtest_and_keeps_orders_governed(
    ready, setup, rule_feed, invocation, database, stage_from_candidate
):
    dep, saved = configured(ready, setup)
    feed = ready.post("/api/v1/market/binance:spot:BTCUSDT/refresh", json={"interval": "15m"})
    assert feed.status_code == 200, feed.text

    def call(key, operation, params):
        result = action(ready, {**invocation, "tool_call_id": key}, operation, params)
        assert result.status_code == 200, result.text
        return result.json()["result"]

    dataset = call(
        "capture-fixture",
        "capture_dataset",
        {"name": "Closed bar evidence", "snapshot_id": feed.json()["snapshot_id"]},
    )
    replay = call(
        "backtest-fixture",
        "run_backtest",
        {
            "name": "Exact strategy test",
            "hypothesis": "Test the deployed rules with recorded costs",
            "dataset_id": dataset["id"],
            "parameters": {"engine": "closed_bar_breakout_v1", "strategy_version_id": saved["id"]},
        },
    )
    params = {"deployment_id": dep["id"], "backtest_id": replay["id"]}
    evaluated = call("evaluate-fixture", "evaluate_paper_strategy", params)
    assert evaluated["mode"] == "paper" and evaluated["orders_submitted"] is False
    assert evaluated["strategy_version_id"] == saved["id"]
    baseline = call(
        "baseline-fixture",
        "run_backtest",
        {
            "name": "Different baseline",
            "hypothesis": "Test a different engine",
            "dataset_id": dataset["id"],
            "parameters": {"fast_window": 1, "slow_window": 2},
        },
    )
    rejected = action(
        ready,
        {**invocation, "tool_call_id": "mismatch-fixture"},
        "evaluate_paper_strategy",
        {"deployment_id": dep["id"], "backtest_id": baseline["id"]},
    )
    assert rejected.status_code == 422 and "exact deployed strategy" in rejected.json()["detail"]

    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        settings = autonomous.settings_snapshot(db, owner, "local")
        native_request = {
            "mode": "current_daily_research",
            "trade_date": now().date().isoformat(),
            "upstream_revision": analysis.UPSTREAM_REVISION,
            "profile_fingerprint": settings["profile_fingerprint"],
            "settings": settings["analysis_settings"],
        }
        native_job = AnalysisJob(
            owner_id=owner.id,
            request_id=str(uuid4()),
            instrument_id=dep["instrument_id"],
            profile="local",
            status="succeeded",
            request=native_request,
            progress=[],
            finished_at=now(),
        )
        db.add(native_job)
        db.flush()
        native_payload = {
            "request": native_request,
            "native_rating": "BUY",
            "execution_authority": False,
            "governance": None,
            "reports": {k: "Synthetic complete native evidence" for k in autonomous.REPORTS},
        }
        original = AnalysisArtifact(
            owner_id=owner.id,
            job_id=native_job.id,
            payload=native_payload,
            checksum=hashlib.sha256(
                json.dumps(native_payload, sort_keys=True, allow_nan=False).encode()
            ).hexdigest(),
        )
        db.add(original)
        db.flush()
        artifact_id = original.id
        native_job_id = native_job.id
        original_checksum = original.checksum
        db.commit()
        governance.heartbeat(db)
    proposal = call(
        "propose-fixture",
        "propose_paper_strategy",
        {
            **params,
            "strategy_run_id": evaluated["id"],
            "native_artifact_id": artifact_id,
            "stop_price": evaluated["payload"]["stop_price"],
        },
    )
    review = call(
        "review-fixture",
        "request_paper_review",
        {"proposal_id": proposal["id"], "backtest_id": replay["id"], "profile": "local"},
    )
    assert review["status"] == "queued"
    with Session(database[0]) as db:
        claimed = governance.claim(db)
    assert claimed[0] == review["id"]
    context = ready.get(
        "/internal/openclaw/jobs/" + review["id"] + "/context",
        headers={"Authorization": "Bearer fixture-private-" + "x" * 50},
    )
    assert context.status_code == 200, context.text
    ctx = context.json()
    result = {
        "job_id": review["id"],
        "context_checksum": ctx["context_checksum"],
        "proposal_id": proposal["id"],
        "policy_id": ctx["context"]["policy_id"],
        "outcome": "allow",
        "reason": "Synthetic independent governance fixture",
    }
    with Session(database[0]) as db:
        assert governance.finish(db, *claimed, raw=json.dumps(result))
    completed = ready.get("/api/v1/governance/jobs/" + review["id"]).json()
    if stage_from_candidate:
        from test_scanner import body, candidate

        from tauric import paper

        profile_response = ready.put("/api/v1/scanners/profiles", json=body(saved))
        assert profile_response.status_code == 200, profile_response.text
        profile = profile_response.json()
        found = candidate(ready, profile)
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            payload = {"candidate_id": found["id"], "job_id": native_job_id}
            db.add(
                ScannerAnalysis(
                    owner_id=owner.id,
                    candidate_id=found["id"],
                    profile_version_id=profile["id"],
                    strategy_version_id=saved["id"],
                    job_id=native_job_id,
                    request_id=str(uuid4()),
                    research_key="fixture:" + found["id"],
                    charged=False,
                    payload=payload,
                    checksum=paper.checksum(payload),
                )
            )
            db.commit()
        endpoint = f"/api/v1/scanners/candidates/{found['id']}/paper-plan"
        readiness = ready.get(f"/api/v1/scanners/candidates/{found['id']}/paper-readiness")
        assert readiness.status_code == 200 and readiness.json()["ready"], readiness.text
        request = {
            "request_id": str(uuid4()),
            "decision_id": completed["decision_id"],
            "backtest_id": replay["id"],
            "reason": "Stage only the governed candidate paper plan",
        }
        mismatch = ready.post(endpoint, json={**request, "backtest_id": baseline["id"]})
        assert mismatch.status_code == 422
        staged = ready.post(endpoint, json=request)
        assert staged.status_code == 200, staged.text
        plan = staged.json()
        assert ready.post(endpoint, json=request).json()["id"] == plan["id"]
        assert (
            ready.post(endpoint, json={**request, "backtest_id": baseline["id"]}).status_code == 409
        )
        with Session(database[0]) as db:
            assert not list(db.scalars(select(TradingRecord).where(TradingRecord.kind == "fill")))
    else:
        plan = call(
            "plan-fixture",
            "plan_paper_entry",
            {"decision_id": completed["decision_id"], "backtest_id": replay["id"]},
        )
    filled = call(
        "fill-fixture", "execute_paper_plan", {"plan_id": plan["id"], "backtest_id": replay["id"]}
    )
    assert filled["kind"] == "fill" and filled["payload"]["side"] == "buy"
    closed = call("close-fixture", "close_paper_position", {"position_id": filled["id"]})
    assert closed["kind"] == "fill" and closed["payload"]["side"] == "sell"
    with Session(database[0]) as db:
        assert db.get(AnalysisArtifact, artifact_id).checksum == original_checksum
        fills = list(db.scalars(select(TradingRecord).where(TradingRecord.kind == "fill")))
        assert len(fills) == 2
