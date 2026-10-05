"""Unattended paper mechanics on isolated PostgreSQL and explicit AI fixtures."""

import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_governance import ready as ready
from test_governance import result, run_context
from test_paper import action
from test_paper import setup as setup
from test_strategies import pause
from test_strategy_rules import configured
from test_strategy_rules import rule_feed as rule_feed
from test_strategy_scheduler import process as monitor
from test_strategy_scheduler import schedule

from tauric import analysis, autonomous, governance, openclaw_api, strategy_scheduler
from tauric.models import (
    AnalysisArtifact,
    AnalysisJob,
    AutonomyEvent,
    GatewayStatus,
    GovernanceJob,
    Owner,
    TradingRecord,
)


@pytest.fixture
def clock(monkeypatch, rule_feed):
    # These tests advance within one simulated day. A wall-clock start near UTC
    # midnight accidentally resets daily limits and invalidates daily job reuse.
    stamp = rule_feed["now"].replace(hour=12, minute=0, second=0, microsecond=0)
    rule_feed["now"] = stamp.replace(
        minute=stamp.minute // 15 * 15, second=0, microsecond=0
    ) + timedelta(minutes=17)
    for module in (analysis, autonomous, governance, openclaw_api, strategy_scheduler):
        monkeypatch.setattr(module, "now", lambda: rule_feed["now"])
    return rule_feed


@pytest.fixture
def connections(ready, clock, database):
    with Session(database[0]) as db:
        analysis.heartbeat(db, "fixture-0.5.2")
        gateway = db.get(GatewayStatus, "openclaw")
        gateway.heartbeat_at = clock["now"]
        governance.heartbeat(db)
    return ready


def policy(client, dep, *, revision=0, **changes):
    req = {
        **action(),
        "expected_revision": revision,
        "enabled": True,
        "auto_entries": True,
        "auto_exits": True,
        "profile": "cloud",
        "daily_research_limit": 1,
        "daily_review_limit": 3,
        "daily_entry_limit": 1,
        "report_max_age_seconds": 3600,
        "workflow_timeout_seconds": 900,
        **changes,
    }
    response = client.put(f"/api/v1/strategies/deployments/{dep['id']}/autonomy", json=req)
    assert response.status_code == 200, response.text
    return response.json(), req


def process(database):
    with Session(database[0]) as db:
        return autonomous.process_one(db)


def attempts(client):
    return client.get("/api/v1/paper").json()["autonomy"]["workflows"]


def admitted(client, setup, database, **changes):
    dep, _ = configured(client, setup)
    schedule(client, dep)
    policy(client, dep, **changes)
    assert monitor(database)
    assert process(database)
    return dep, attempts(client)[0]


def research(database, workflow, *, change=None, error=None):
    with Session(database[0]) as db:
        job_id = analysis.claim(db)
        assert job_id == workflow["analysis_job_id"]
        job = db.get(AnalysisJob, job_id)
        payload = {
            "reports": {
                k: "Isolated native research fixture. Daily USD context only."
                for k in autonomous.REPORTS
            },
            "request": deepcopy(job.request),
            "native_rating": "BUY",
            "execution_authority": False,
            "governance": None,
        }
        if change:
            change(payload)
        assert analysis.finish(db, job_id, payload if not error else None, error)
        return payload


def waiting_review(client, database, clock, workflow):
    research(database, workflow)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    workflow = attempts(client)[0]
    assert workflow["status"] == "waiting_review", workflow["reason"]
    return workflow


def finish_review(client, database, connections, workflow, outcome="allow"):
    with Session(database[0]) as db:
        job = governance.view(db.get(GovernanceJob, workflow["review_job_id"])).model_dump(
            mode="json"
        )
    claim, ctx = run_context(client, database, connections, job)
    with Session(database[0]) as db:
        assert governance.finish(db, *claim, raw=json.dumps(result(ctx, outcome)))
        assert not governance.finish(db, *claim, raw=json.dumps(result(ctx, outcome)))


def test_default_off_auth_and_monitor_required(signed_in, setup, database, connections):
    state = signed_in.get("/api/v1/paper").json()
    assert state["autonomy"] == {"worker_ready": False, "policies": [], "workflows": []}
    assert not process(database)
    dep, _ = configured(signed_in, setup)
    req = {
        **action(),
        "expected_revision": 0,
        "enabled": False,
        "auto_entries": True,
        "auto_exits": True,
        "profile": "cloud",
        "daily_research_limit": 1,
        "daily_review_limit": 3,
        "daily_entry_limit": 1,
        "report_max_age_seconds": 3600,
        "workflow_timeout_seconds": 900,
    }
    path = f"/api/v1/strategies/deployments/{dep['id']}/autonomy"
    assert signed_in.put(path, json={**req, "enabled": True}).status_code == 422
    assert signed_in.put(path, json=req).status_code == 200
    signed_in.headers.pop("X-CSRF-Token")
    assert signed_in.put(path, json=req).status_code == 403
    signed_in.cookies.clear()
    assert signed_in.put(path, json=req).status_code == 401
    assert signed_in.get(path + "/history").status_code == 401


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": "true"},
        {"auto_exits": 1},
        {"daily_research_limit": 11},
        {"daily_research_limit": -1},
        {"daily_review_limit": 51},
        {"daily_entry_limit": 0},
        {"daily_entry_limit": 1.5},
        {"report_max_age_seconds": 86401},
        {"workflow_timeout_seconds": 1801},
        {"enabled": True, "auto_entries": False, "auto_exits": False},
        {"profile": "fallback"},
        {"unexpected": True},
        {"reason": "   "},
    ],
)
def test_policy_bounds(signed_in, setup, connections, change):
    dep, _ = configured(signed_in, setup)
    schedule(signed_in, dep)
    _, req = policy(signed_in, dep, enabled=False)
    assert (
        signed_in.put(
            f"/api/v1/strategies/deployments/{dep['id']}/autonomy",
            json={**req, **action(), "expected_revision": 1, **change},
        ).status_code
        == 422
    )


def test_unattended_complete_trade_journal_and_restarts(
    signed_in, setup, database, clock, connections
):
    dep, workflow = admitted(signed_in, setup, database)
    assert workflow["status"] == "waiting_research"
    assert not process(database)  # Same saved signal cannot request another job.
    original = research(database, workflow)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    workflow = attempts(signed_in)[0]
    assert workflow["status"] == "waiting_review", workflow["reason"]
    finish_review(signed_in, database, connections, workflow)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    opened = attempts(signed_in)[0]
    assert opened["status"] == "opened", opened["reason"]
    state = signed_in.get("/api/v1/paper").json()
    entry = next(r for r in state["records"] if r["id"] == opened["entry_fill_id"])
    assert entry["payload"]["autonomous_context"]["workflow_id"] == opened["id"]
    assert len(state["positions"]) == 1 and state["accounts"][0]["reconciled"]
    assert state["autonomy"]["policies"][0]["usage_today"] == {
        "research": 1,
        "reviews": 1,
        "entries": 1,
    }
    # New sessions simulate API/worker restart without retaining any in-memory state.
    assert not process(database)
    clock["now"] += timedelta(minutes=45)
    clock["mode"] = "stop"
    assert monitor(database)
    assert process(database)
    assert process(database)  # Link the original opened workflow to its close.
    state = signed_in.get("/api/v1/paper").json()
    fills = [r for r in state["records"] if r["kind"] == "fill"]
    assert len(fills) == 2 and not state["positions"]
    exit_fill = next(r for r in fills if r["payload"]["side"] == "sell")
    assert exit_fill["payload"]["position_id"] == entry["id"]
    assert exit_fill["payload"]["exit_source"] == "autonomous_rule_close"
    pnl = (
        Decimal(entry["payload"]["quantity"])
        * (Decimal(exit_fill["payload"]["price"]) - Decimal(entry["payload"]["price"]))
        - Decimal(entry["payload"]["fee"])
        - Decimal(exit_fill["payload"]["fee"])
    )
    assert Decimal(state["accounts"][0]["realized_pnl"]) == pnl
    assert Decimal(state["accounts"][0]["cash"]) == Decimal("10000") + pnl
    assert all(w["status"] == "closed" for w in state["autonomy"]["workflows"])
    with Session(database[0]) as db:
        assert db.get(AnalysisArtifact, opened["native_artifact_id"]).payload == original
        assert (
            db.scalar(select(func.count()).select_from(AnalysisJob)) == 2
        )  # Generic ready fixture + one actual admission.
    assert not process(database)


@pytest.mark.parametrize("outcome", ["reject", "defer"])
def test_denied_review_never_fills_or_retries(
    signed_in, setup, database, clock, connections, outcome
):
    _, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    finish_review(signed_in, database, connections, workflow, outcome)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    assert not process(database)
    assert not signed_in.get("/api/v1/paper").json()["positions"]


@pytest.mark.parametrize("change", ["disable", "schedule", "pause", "halt", "routing", "expiry"])
def test_changed_authority_blocks_pending_entry(
    signed_in, setup, database, clock, connections, change
):
    dep, workflow = admitted(signed_in, setup, database)
    if change == "disable":
        policy(signed_in, dep, revision=1, enabled=False)
    elif change == "schedule":
        schedule(signed_in, dep, revision=1, enabled=False)
    elif change == "pause":
        pause(signed_in, dep)
    elif change == "halt":
        state = signed_in.get("/api/v1/paper").json()
        response = signed_in.put(
            f"/api/v1/paper/accounts/{dep['account_id']}/halt",
            json={
                "reason": "Halt disposable account",
                "expected_revision": state["accounts"][0]["revision"],
                "halted": True,
            },
        )
        assert response.status_code == 200, response.text
    elif change == "routing":
        profiles = deepcopy(connections[5])
        profiles["cloud"]["quick_model"] = "changed"
        connections[4].write_text(json.dumps(profiles))
    else:
        clock["now"] += timedelta(minutes=20)
    clock["now"] += timedelta(seconds=5)
    with Session(database[0]) as db, pytest.raises(HTTPException):
        autonomous.validate_admission(db, db.get(AnalysisJob, workflow["analysis_job_id"]))
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    assert not signed_in.get("/api/v1/paper").json()["positions"]


@pytest.mark.parametrize(
    "mutation", ["missing_report", "request", "authority", "governance", "hold", "sell"]
)
def test_original_evidence_rejected_without_review(
    signed_in, setup, database, clock, connections, mutation
):
    _, workflow = admitted(signed_in, setup, database)

    def change(payload):
        if mutation == "missing_report":
            payload["reports"].pop("risk_debate")
        elif mutation == "request":
            payload["request"]["trade_date"] = "2000-01-01"
        elif mutation == "authority":
            payload["execution_authority"] = True
        elif mutation == "governance":
            payload["governance"] = "allow"
        else:
            payload["native_rating"] = "HOLD" if mutation == "hold" else "SELL"

    research(database, workflow, change=change)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    with Session(database[0]) as db:
        assert not db.scalar(select(GovernanceJob.id))


@pytest.mark.parametrize("budget", ["daily_research_limit", "daily_review_limit"])
def test_daily_limits_and_research_reuse_across_policy_versions(
    signed_in, setup, database, clock, connections, budget
):
    dep, workflow = admitted(signed_in, setup, database, **{budget: 0})
    if budget == "daily_review_limit":
        research(database, workflow)
        clock["now"] += timedelta(seconds=5)
        assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    policy(signed_in, dep, revision=1, **{budget: 0})
    clock["now"] += timedelta(minutes=15)
    with Session(database[0]) as db:
        db.get(GatewayStatus, "openclaw").heartbeat_at = clock["now"]
        governance.heartbeat(db)
        analysis.heartbeat(db, "fixture")
    assert monitor(database) and process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    usage = signed_in.get("/api/v1/paper").json()["autonomy"]["policies"][0]["usage_today"]
    assert usage["reviews"] == 0
    assert usage["research"] == (1 if budget == "daily_review_limit" else 0)


def test_failed_daily_native_is_not_retried_on_next_signal(
    signed_in, setup, database, clock, connections
):
    dep, workflow = admitted(signed_in, setup, database)
    research(database, workflow, error="Fixture provider failed")
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    clock["now"] += timedelta(minutes=15)
    assert monitor(database) and process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 2


def test_fill_rollback_after_crash_then_exactly_once_recovery(
    signed_in, setup, database, clock, connections, monkeypatch
):
    _, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    finish_review(signed_in, database, connections, workflow)
    clock["now"] += timedelta(seconds=5)
    real = autonomous.event

    def crash(db, workflow, kind, **detail):
        real(db, workflow, kind, **detail)
        if kind == "entry_filled":
            raise SystemExit("Fixture worker stopped before commit")

    monkeypatch.setattr(autonomous, "event", crash)
    with pytest.raises(SystemExit):
        process(database)
    assert attempts(signed_in)[0]["status"] == "waiting_review"
    assert not signed_in.get("/api/v1/paper").json()["positions"]
    monkeypatch.setattr(autonomous, "event", real)
    with ThreadPoolExecutor(max_workers=3) as pool:
        completed = list(pool.map(lambda _: process(database), range(3)))
    assert sum(completed) == 1
    assert attempts(signed_in)[0]["status"] == "opened"
    assert len(signed_in.get("/api/v1/paper").json()["positions"]) == 1


def test_database_history_immutable_and_workflow_cannot_be_rebound(
    signed_in, setup, database, clock, connections
):
    dep, workflow = admitted(signed_in, setup, database)
    _, req = policy(signed_in, dep, revision=1, enabled=False)
    path = f"/api/v1/strategies/deployments/{dep['id']}/autonomy"
    assert signed_in.put(path, json=req).status_code == 200
    assert signed_in.put(path, json={**req, "reason": "different"}).status_code == 409
    for table in ("autonomy_policy_versions", "autonomy_events"):
        for sql in (
            f"UPDATE {table} SET checksum='bad'",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with database[0].connect() as connection, pytest.raises(DBAPIError):
                connection.execute(text(sql))
    with database[0].connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("UPDATE autonomy_workflows SET payload='{}'"))
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    with database[0].connect() as connection, pytest.raises(DBAPIError):
        connection.execute(text("UPDATE autonomy_workflows SET status='waiting_research'"))
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AutonomyEvent)) >= 3
        other = Owner(id=action()["request_id"], username="other-owner", password_hash="unused")
        with pytest.raises(HTTPException):
            autonomous.history(db, other, dep["id"])


def test_interrupted_native_and_review_leases_are_not_retried(
    signed_in, setup, database, clock, connections
):
    _, workflow = admitted(signed_in, setup, database)
    with Session(database[0]) as db:
        assert analysis.claim(db) == workflow["analysis_job_id"]
    clock["now"] += timedelta(seconds=46)
    with Session(database[0]) as db:
        assert analysis.claim(db) is None
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    assert not process(database)


def test_interrupted_review_has_no_fill(signed_in, setup, database, clock, connections):
    _, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    with Session(database[0]) as db:
        assert governance.claim(db)[0] == workflow["review_job_id"]
    clock["now"] += timedelta(seconds=46)
    with Session(database[0]) as db:
        assert governance.claim(db) is None
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    assert not signed_in.get("/api/v1/paper").json()["positions"]


def test_explicit_bound_plan_is_adopted_and_revocation_blocks_execution(
    signed_in, setup, database, clock, connections
):
    dep, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    finish_review(signed_in, database, connections, workflow)
    with Session(database[0]) as db:
        decision_id = db.get(GovernanceJob, workflow["review_job_id"]).decision_id
    planned = signed_in.post(f"/api/v1/paper/decisions/{decision_id}/plan", json=action())
    assert planned.status_code == 201, planned.text
    policy(signed_in, dep, revision=1, enabled=False)
    fill = signed_in.post(f"/api/v1/paper/plans/{planned.json()['id']}/execute", json=action())
    assert fill.status_code == 409
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"


def test_existing_bound_fill_and_owner_close_are_reconciled_after_disable(
    signed_in, setup, database, clock, connections
):
    dep, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    finish_review(signed_in, database, connections, workflow)
    with Session(database[0]) as db:
        decision_id = db.get(GovernanceJob, workflow["review_job_id"]).decision_id
    planned = signed_in.post(f"/api/v1/paper/decisions/{decision_id}/plan", json=action())
    assert planned.status_code == 201, planned.text
    fill = signed_in.post(f"/api/v1/paper/plans/{planned.json()['id']}/execute", json=action())
    assert fill.status_code == 201, fill.text
    policy(signed_in, dep, revision=1, enabled=False)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "opened"
    closed = signed_in.post(f"/api/v1/paper/positions/{fill.json()['id']}/close", json=action())
    assert closed.status_code == 201, closed.text
    clock["now"] += timedelta(seconds=30)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "closed"
    assert not signed_in.get("/api/v1/paper").json()["positions"]


def test_storage_failure_rolls_back_stage_and_reconnect_resumes(
    signed_in, setup, database, clock, connections, monkeypatch
):
    _, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    finish_review(signed_in, database, connections, workflow)
    clock["now"] += timedelta(seconds=5)
    real = autonomous.paper.execute

    def fail_storage(db, *args, **kwargs):
        real(db, *args, **kwargs)
        db.execute(text("SELECT * FROM nonexistent_autonomy_storage_fixture"))

    monkeypatch.setattr(autonomous.paper, "execute", fail_storage)
    with pytest.raises(DBAPIError):
        process(database)
    assert attempts(signed_in)[0]["status"] == "waiting_review"
    assert not signed_in.get("/api/v1/paper").json()["positions"]
    monkeypatch.setattr(autonomous.paper, "execute", real)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "opened"


def test_provider_outage_during_approved_entry_blocks_fill(
    signed_in, setup, database, clock, connections, monkeypatch
):
    _, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    finish_review(signed_in, database, connections, workflow)
    clock["now"] += timedelta(seconds=31)

    def unavailable(*args, **kwargs):
        raise HTTPException(503, "Fixture provider unavailable")

    monkeypatch.setattr(autonomous.market.PROVIDERS["binance_spot_public"], "collect", unavailable)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    assert not signed_in.get("/api/v1/paper").json()["positions"]


def test_exit_only_policy_closes_during_account_halt_without_models(
    signed_in, setup, database, clock
):
    from test_strategy_rules import filled, run

    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)
    # Original setup quote lies before this test clock; refresh the real fixture collector.
    with Session(database[0]) as db:
        autonomous.market.refresh(db, dep["instrument_id"], "1m")
    entry = filled(signed_in, dep, signal)
    schedule(signed_in, dep)
    policy(signed_in, dep, auto_entries=False, daily_research_limit=0, daily_review_limit=0)
    state = signed_in.get("/api/v1/paper").json()
    halted = signed_in.put(
        f"/api/v1/paper/accounts/{dep['account_id']}/halt",
        json={
            "reason": "Halt new entries",
            "halted": True,
            "expected_revision": state["accounts"][0]["revision"],
        },
    )
    assert halted.status_code == 200, halted.text
    assert pause(signed_in, dep).status_code == 200
    clock["mode"] = "stop"
    clock["now"] += timedelta(minutes=45)
    assert monitor(database) and process(database)
    state = signed_in.get("/api/v1/paper").json()
    assert not state["positions"] and state["accounts"][0]["halted"]
    assert state["autonomy"]["policies"][0]["usage_today"] == {
        "research": 0,
        "reviews": 0,
        "entries": 0,
    }
    assert state["autonomy"]["workflows"][0]["close_fill_id"]
    with Session(database[0]) as db:
        assert not db.scalar(select(AnalysisJob.id))
        assert not db.scalar(select(GovernanceJob.id))
        fill = db.get(TradingRecord, state["autonomy"]["workflows"][0]["close_fill_id"])
        assert fill.payload["position_id"] == entry["id"]


def test_consumed_signal_requests_no_native_call(signed_in, setup, database, clock, connections):
    from test_strategy_rules import propose

    dep, _ = configured(signed_in, setup)
    schedule(signed_in, dep)
    assert monitor(database)
    signal = signed_in.get("/api/v1/paper").json()["strategy_runs"][0]
    with Session(database[0]) as db:
        autonomous.market.refresh(db, dep["instrument_id"], "1m")
    assert propose(signed_in, dep, signal)[0].status_code == 201
    policy(signed_in, dep)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    assert (
        signed_in.get("/api/v1/paper").json()["autonomy"]["policies"][0]["usage_today"]["research"]
        == 0
    )


def opened_entry(client, setup, database, clock, connections):
    dep, workflow = admitted(client, setup, database)
    workflow = waiting_review(client, database, clock, workflow)
    finish_review(client, database, connections, workflow)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    workflow = attempts(client)[0]
    assert workflow["status"] == "opened", workflow["reason"]
    return dep, workflow


def test_exit_crash_before_and_after_commit_recovers_one_attributed_fill(
    signed_in, setup, database, clock, connections, monkeypatch
):
    _, workflow = opened_entry(signed_in, setup, database, clock, connections)
    clock["now"] += timedelta(minutes=45)
    clock["mode"] = "stop"
    assert monitor(database)
    real_event = autonomous.event

    def crash(db, work, kind, **detail):
        real_event(db, work, kind, **detail)
        if kind == "exit_filled":
            raise SystemExit("Interrupted before exit transaction commit")

    monkeypatch.setattr(autonomous, "event", crash)
    with pytest.raises(SystemExit):
        process(database)
    assert len(signed_in.get("/api/v1/paper").json()["positions"]) == 1
    assert len(attempts(signed_in)) == 1
    monkeypatch.setattr(autonomous, "event", real_event)
    assert process(database)
    # Saved commit is authoritative even if the process dies before acknowledging it.
    assert process(database)
    assert not process(database)
    state = signed_in.get("/api/v1/paper").json()
    exits = [r for r in state["records"] if r["kind"] == "fill" and r["payload"]["side"] == "sell"]
    assert len(exits) == 1 and exits[0]["payload"]["position_id"] == workflow["entry_fill_id"]
    assert not state["positions"] and state["accounts"][0]["reconciled"]


@pytest.mark.parametrize("unrelated_strategy_assessment", [False, True])
def test_pause_resume_requires_fresh_signal_and_reuses_preserved_buy_context(
    signed_in, setup, database, clock, connections, unrelated_strategy_assessment
):
    dep, workflow = admitted(signed_in, setup, database)
    workflow = waiting_review(signed_in, database, clock, workflow)
    assert pause(signed_in, dep).status_code == 200
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "blocked"
    with Session(database[0]) as db:
        claim = governance.claim(db)
        assert claim[0] == workflow["review_job_id"]
        assert governance.finish(db, *claim, error="Paused context rejected before model dispatch")
    assert pause(signed_in, dep, enabled=True, revision=1).status_code == 200
    assert not process(database)  # Resume does not revive the consumed candle/old workflow.
    clock["now"] += timedelta(minutes=15)
    with Session(database[0]) as db:
        if unrelated_strategy_assessment:
            daily = db.get(AnalysisJob, workflow["analysis_job_id"])
            request = {**deepcopy(daily.request), "mode": "strategy_qualification"}
            request.pop("autonomous_context", None)
            db.add(
                AnalysisJob(
                    owner_id=daily.owner_id,
                    request_id=str(action()["request_id"]),
                    instrument_id=daily.instrument_id,
                    profile=daily.profile,
                    status="failed",
                    request=request,
                    progress=[],
                    created_at=clock["now"],
                )
            )
        db.get(GatewayStatus, "openclaw").heartbeat_at = clock["now"]
        governance.heartbeat(db)
        analysis.heartbeat(db, "fixture")
    assert monitor(database) and process(database)
    next_workflow = attempts(signed_in)[0]
    assert next_workflow["status"] == "waiting_review", next_workflow["reason"]
    assert next_workflow["analysis_job_id"] == workflow["analysis_job_id"]
    finish_review(signed_in, database, connections, next_workflow)
    clock["now"] += timedelta(seconds=5)
    assert process(database)
    assert attempts(signed_in)[0]["status"] == "opened"
    usage = signed_in.get("/api/v1/paper").json()["autonomy"]["policies"][0]["usage_today"]
    assert usage == {"research": 1, "reviews": 2, "entries": 1}


def test_daily_entry_count_survives_policy_revision_and_position_close(
    signed_in, setup, database, clock, connections
):
    dep, _ = opened_entry(signed_in, setup, database, clock, connections)
    clock["now"] += timedelta(minutes=30)
    clock["mode"] = "stop"
    assert monitor(database) and process(database) and process(database)
    policy(signed_in, dep, revision=1, daily_entry_limit=1)
    clock["now"] += timedelta(minutes=15)
    clock["mode"] = "enter"
    assert monitor(database) and process(database)
    workflow = attempts(signed_in)[0]
    assert workflow["status"] == "blocked"
    assert "entry limit" in workflow["reason"]
    state = signed_in.get("/api/v1/paper").json()
    assert not state["positions"]
    assert state["autonomy"]["policies"][0]["usage_today"]["entries"] == 1


def test_automatic_close_retry_returns_committed_record_after_disable(
    signed_in, setup, database, clock, connections
):
    dep, _ = opened_entry(signed_in, setup, database, clock, connections)
    clock["now"] += timedelta(minutes=30)
    clock["mode"] = "stop"
    assert monitor(database) and process(database)
    policy(signed_in, dep, revision=1, enabled=False)
    state = signed_in.get("/api/v1/paper").json()
    closed = next(
        r for r in state["records"] if r["kind"] == "fill" and r["payload"]["side"] == "sell"
    )
    request = closed["payload"]["request"]
    from tauric.models import StrategyRun
    from tauric.trading_contracts import PaperAction

    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        repeated = autonomous.paper.close(
            db,
            owner,
            request["position_id"],
            PaperAction(request_id=request["request_id"], reason=request["reason"]),
            strategy_run=db.get(StrategyRun, request["strategy_run_id"]),
            autonomous_context=request["autonomous_context"],
        )
        assert repeated.id == closed["id"]
    assert not signed_in.get("/api/v1/paper").json()["positions"]
