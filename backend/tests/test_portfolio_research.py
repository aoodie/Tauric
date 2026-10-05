"""Research-only schedules, durable evidence and shared admission on disposable PostgreSQL."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_paper import setup as setup
from test_portfolio_chat import HEADERS, active_turn, preferences
from test_portfolio_chat import portfolios_ready as portfolios_ready
from test_research import import_request
from test_scanner import configured
from test_scanner import scanner_feed as scanner_feed

from tauric import admission_budget, analysis
from tauric import portfolio_research as coordinator
from tauric.models import (
    AnalysisJob,
    Owner,
    TradingRecord,
)
from tauric.portfolio_research_contracts import PortfolioResearchParameters

pytest_plugins = ["test_chat"]
BTC = "binance:spot:BTCUSDT"


def policy(**changes):
    return {
        "request_id": str(uuid4()),
        "expected_revision": 0,
        "parameters": PortfolioResearchParameters(**{"universe": [BTC], **changes}).model_dump(
            mode="json"
        ),
        "reason": "Configure isolated research fixture",
    }


def save(client, scope, body):
    r = client.put(f"/api/v1/portfolios/{scope['id']}/research", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def run(client, scope):
    body = {"request_id": str(uuid4()), "reason": "Run offline portfolio fixture"}
    r = client.post(f"/api/v1/portfolios/{scope['id']}/research/runs", json=body)
    assert r.status_code == 202, r.text
    assert (
        client.post(f"/api/v1/portfolios/{scope['id']}/research/runs", json=body).json()["id"]
        == r.json()["id"]
    )
    return r.json()


def drain(database):
    for _ in range(40):
        with Session(database[0]) as db:
            if not coordinator.process_one(db):
                return
    raise AssertionError("Coordinator did not become idle")


def state(client, scope):
    return client.get(f"/api/v1/portfolios/{scope['id']}/research").json()


def budget(client, limit, revision=0):
    r = client.put(
        "/api/v1/research/admission-budget",
        json={
            "request_id": str(uuid4()),
            "expected_revision": revision,
            "daily_job_limit": limit,
            "reason": "Explicit offline allowance",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_policy_is_opt_in_cas_owned_validated_and_immutable(ready, database, portfolios_ready):
    a, b, *_ = portfolios_ready
    p = policy(asset_classes=["crypto_spot", "fx", "equity"])
    result = save(ready, a, p)
    assert result["next_daily_due"] is None
    assert result["parameters"]["enabled"] is False
    assert result["shared_budget"]["daily_job_limit"] is None
    assert {
        v["asset_class"] for v in result["asset_capabilities"] if v["priority"] == "next_together"
    } == {"fx", "equity", "etf"}
    assert ready.put(f"/api/v1/portfolios/{a['id']}/research", json=p).json()["revision"] == 1
    assert ready.put(f"/api/v1/portfolios/{b['id']}/research", json=p).status_code == 409
    assert ready.put(f"/api/v1/portfolios/{a['id']}/research", json=policy()).status_code == 409
    for changes in (
        {"timezone": "invalid/zone"},
        {"enabled": 1},
        {"universe": ["unregistered"]},
        {"opportunities": False, "strategies": False},
        {"max_native_jobs_per_day": 1},
    ):
        request = {**policy(), "expected_revision": 1}
        request["parameters"].update(changes)
        assert ready.put(f"/api/v1/portfolios/{a['id']}/research", json=request).status_code in (
            404,
            422,
        )
    assert ready.get(f"/api/v1/portfolios/{a['id']}/research/history").json()[0]["revision"] == 1
    for table in (
        "portfolio_research_policy_versions",
        "shared_admission_budget_versions",
        "portfolio_research_steps",
        "portfolio_research_links",
        "portfolio_research_digests",
    ):
        for command in (
            f"UPDATE {table} SET checksum='changed'",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with database[0].begin() as db, pytest.raises(DBAPIError):
                db.execute(text(command))


def test_daily_dst_and_missed_runs_coalesce_and_restart(
    ready, database, portfolios_ready, monkeypatch
):
    p = PortfolioResearchParameters(daily_time="01:30", timezone="Europe/London")
    assert coordinator.daily_due(datetime(2026, 3, 29).date(), p) == datetime(
        2026, 3, 29, 1, 0, tzinfo=UTC
    )
    assert coordinator.daily_due(datetime(2026, 10, 25).date(), p) == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )
    a, *_ = portfolios_ready
    save(ready, a, policy(enabled=True))
    with database[0].begin() as db:
        db.execute(
            text("UPDATE portfolio_research_schedules SET next_daily_due=:due"),
            {"due": datetime.now(UTC) - timedelta(days=4)},
        )
    drain(database)
    s = state(ready, a)
    assert len(s["runs"]) == 1 and s["runs"][0]["trigger"] == "daily"
    assert s["runs"][0]["digest"]["missed_run_recovery"] is True
    assert s["runs"][0]["digest"]["execution_authority"] is False
    assert s["runs"][0]["status"] == "partial"  # No configured feeds/hypotheses, honestly deferred.
    drain(database)
    assert len(state(ready, a)["runs"]) == 1
    assert datetime.fromisoformat(s["next_daily_due"]) > datetime.now(UTC)


def test_both_tracks_persist_real_replays_and_reuse_without_orders(
    ready, database, portfolios_ready, scanner_feed
):
    a, *_ = portfolios_ready
    profile, _ = configured(ready)
    dataset = ready.post("/api/v1/research/datasets/import", json=import_request()).json()
    experiment = {
        "dataset_id": dataset["id"],
        "hypothesis": "Compare bounded momentum windows after costs",
        "parameters": {"fast_window": 1, "slow_window": 3, "risk_percent": "1"},
        "explore_sma_neighbours": True,
    }
    save(ready, a, policy(scanner_profile_ids=[profile["id"]], experiments=[experiment]))
    with Session(database[0]) as db:
        before = db.scalar(select(func.count()).select_from(TradingRecord))
    run(ready, a)
    drain(database)
    s = state(ready, a)
    first = s["runs"][0]
    assert first["status"] == "partial"  # Fresh native research is disabled at a zero allowance.
    experiments = [v for v in first["steps"] if v.get("replay_id")]
    assert len(experiments) == 2 and all(v["qualification"] == "not_qualified" for v in experiments)
    assert any(v.get("shortlist") for v in first["steps"])
    assert all(v["dataset_checksum"] == dataset["checksum"] for v in experiments)
    assert first["native_jobs"] == []
    run(ready, a)
    drain(database)
    second = state(ready, a)["runs"][0]
    assert all(v["reused"] for v in second["steps"] if v.get("replay_id"))
    assert {v["replay_id"] for v in experiments} == {
        v["replay_id"] for v in second["steps"] if v.get("replay_id")
    }
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(TradingRecord)) == before
    assert (
        ready.put(
            f"/api/v1/portfolios/{a['id']}/research",
            json={
                **policy(experiments=[{**experiment, "parameters": {"risk_percent": "100"}}]),
                "expected_revision": 1,
            },
        ).status_code
        == 422
    )


def test_native_reuse_cross_portfolio_zero_budget_failed_job_and_shared_cap(
    ready, database, portfolios_ready, scanner_feed
):
    a, b, *_ = portfolios_ready
    profile, _ = configured(ready)
    budget(ready, 1)
    save(ready, a, policy(scanner_profile_ids=[profile["id"]], max_native_jobs_per_day=1))
    save(ready, b, policy(scanner_profile_ids=[profile["id"]]))
    with Session(database[0]) as db:
        analysis.heartbeat(db, "offline-fixture")
    run(ready, a)
    drain(database)
    first = state(ready, a)["runs"][0]
    assert first["status"] == "waiting_native"
    assert len(first["native_jobs"]) == 1 and first["native_jobs"][0]["charged"]
    assert ready.get("/api/v1/research/admission-budget").json()["used_today"] == 1
    run(ready, b)
    drain(database)
    second = state(ready, b)["runs"][0]
    assert second["status"] == "waiting_native"
    assert second["native_jobs"][0]["job"]["id"] == first["native_jobs"][0]["job"]["id"]
    assert second["native_jobs"][0]["charged"] is False
    _, identity = active_turn(ready, database, b)
    context = ready.post("/internal/openclaw/chat-read", headers=HEADERS, json=identity)
    assert context.status_code == 200, context.text
    assert context.json()["daily_research"]["runs"][0]["id"] == second["id"]
    assert context.json()["daily_research"]["runs"][0]["status"] == "waiting_native"
    job_read = ready.post(
        "/internal/openclaw/chat-read",
        headers=HEADERS,
        json={**identity, "job_id": first["native_jobs"][0]["job"]["id"]},
    )
    assert job_read.status_code == 200, job_read.text
    assert job_read.json()["job"]["status"] == "queued"
    with Session(database[0]) as db:
        job = db.get(AnalysisJob, first["native_jobs"][0]["job"]["id"])
        job.status = "running"
        db.commit()
        analysis.finish(db, job.id, error="Explicit offline failure; no paid retry")
    drain(database)
    run(ready, b)
    drain(database)
    last = state(ready, b)["runs"][0]
    assert last["status"] == "partial" and last["native_jobs"][0]["job"]["status"] == "failed"
    assert last["native_jobs"][0]["charged"] is False
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1
        assert admission_budget.used(db, db.scalar(select(Owner))) == 1
    r = ready.post(
        "/api/v1/analysis",
        json={
            "request_id": str(uuid4()),
            "instrument_id": "binance:spot:ETHUSDT",
            "profile": "local",
        },
    )
    assert r.status_code == 429, r.text


def test_policy_changes_cancel_waiting_and_block_native_before_launch(
    ready, database, portfolios_ready, scanner_feed
):
    a, *_ = portfolios_ready
    profile, _ = configured(ready)
    budget(ready, 2)
    save(ready, a, policy(scanner_profile_ids=[profile["id"]], max_native_jobs_per_day=1))
    with Session(database[0]) as db:
        analysis.heartbeat(db, "fixture")
    run(ready, a)
    drain(database)
    job_id = state(ready, a)["runs"][0]["native_jobs"][0]["job"]["id"]
    updated = preferences(a, 1, research_objective="consistency")
    assert ready.put(f"/api/v1/portfolios/{a['id']}", json=updated).status_code == 200
    with Session(database[0]) as db, pytest.raises(ValueError):
        coordinator.validate_admission(db, db.get(AnalysisJob, job_id))
    drain(database)
    assert state(ready, a)["runs"][0]["status"] == "cancelled"


def test_chat_scope_settings_receipts_run_and_events(ready, database, portfolios_ready):
    a, b, *_ = portfolios_ready
    _, identity = active_turn(ready, database, a)
    body = {
        **identity,
        "action": "research_settings",
        "expected_revision": 0,
        "research_parameters": policy()["parameters"],
        "reason": "Explicit portfolio research settings",
    }
    r = ready.post("/internal/openclaw/portfolio-action", headers=HEADERS, json=body)
    assert r.status_code == 200, r.text
    assert (
        ready.post("/internal/openclaw/portfolio-action", headers=HEADERS, json=body).json()["id"]
        == r.json()["id"]
    )
    assert state(ready, b)["revision"] == 0
    req = {**identity, "tool_call_id": "research-run", "reason": "Explicit owner research request"}
    r = ready.post("/internal/openclaw/portfolio-research", headers=HEADERS, json=req)
    assert r.status_code == 202, r.text
    assert r.json()["portfolio_id"] == a["id"] and r.json()["execution_authority"] is False
    assert (
        ready.post("/internal/openclaw/portfolio-research", headers=HEADERS, json=req).json()["id"]
        == r.json()["id"]
    )
    assert (
        ready.post(
            "/internal/openclaw/portfolio-research",
            headers=HEADERS,
            json={**req, "portfolio_id": b["id"]},
        ).status_code
        == 422
    )
    drain(database)
    evt = {"request_id": str(uuid4()), "reason": "Unapproved event", "event_key": "market-shock"}
    assert ready.post(f"/api/v1/portfolios/{a['id']}/research/runs", json=evt).status_code == 403
    save(ready, a, {**policy(enabled=True, event_keys=["market-shock"]), "expected_revision": 1})
    assert (
        ready.post(f"/api/v1/portfolios/{a['id']}/research/runs", json=evt).json()["trigger"]
        == "event"
    )


def test_waiting_portfolio_does_not_block_other_due_work_and_timeout_is_recorded(
    ready, database, portfolios_ready, scanner_feed, monkeypatch
):
    a, b, *_ = portfolios_ready
    profile, _ = configured(ready)
    budget(ready, 2)
    save(
        ready,
        a,
        policy(
            enabled=True,
            scanner_profile_ids=[profile["id"]],
            max_native_jobs_per_day=1,
            native_wait_minutes=5,
        ),
    )
    save(ready, b, policy(enabled=True, asset_classes=["fx", "equity"], universe=[]))
    with Session(database[0]) as db:
        analysis.heartbeat(db, "fixture")
    run(ready, a)
    drain(database)
    assert state(ready, a)["runs"][0]["status"] == "waiting_native"
    from tauric.models import now

    with database[0].begin() as db:
        db.execute(
            text("UPDATE portfolio_research_schedules SET next_daily_due=:due"),
            {"due": now() - timedelta(days=2)},
        )
    drain(database)
    assert state(ready, b)["runs"][0]["trigger"] == "daily"
    assert state(ready, b)["runs"][0]["status"] == "partial"
    assert len(state(ready, a)["runs"]) == 1
    future = now() + timedelta(minutes=6)
    monkeypatch.setattr(coordinator, "now", lambda: future)
    # One committed step finishes the timed-out active run before catching up the schedule.
    with Session(database[0]) as db:
        assert coordinator.process_one(db)
    result = state(ready, a)["runs"][0]
    assert result["status"] == "partial"
    assert any("wait deadline" in step.get("reason", "") for step in result["steps"])
    assert len(result["native_jobs"]) == 1
    with Session(database[0]) as db, pytest.raises(ValueError):
        coordinator.validate_admission(
            db, db.get(AnalysisJob, result["native_jobs"][0]["job"]["id"])
        )


def test_shared_budget_zero_blocks_all_new_admissions_and_concurrent_requests_have_one_winner(
    ready, database, portfolios_ready
):
    from concurrent.futures import ThreadPoolExecutor

    from fastapi import HTTPException

    from tauric.market_contracts import AnalysisRequest

    budget(ready, 0)
    with Session(database[0]) as db:
        analysis.heartbeat(db, "fixture")

    def request():
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            try:
                return analysis.enqueue(
                    db,
                    owner,
                    AnalysisRequest(request_id=uuid4(), instrument_id=BTC, profile="local"),
                ).id
            except HTTPException as error:
                db.rollback()
                return error.status_code

    assert request() == 429
    budget(ready, 1, revision=1)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: request(), range(3)))
    assert sum(isinstance(result, str) for result in results) == 1
    assert results.count(429) == 2
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1
