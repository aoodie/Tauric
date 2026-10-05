"""Scheduled paper checks: isolation, atomic recovery and exit monitoring."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_paper import action
from test_paper import setup as setup
from test_strategies import deploy, pause
from test_strategy_rules import configured, filled, run
from test_strategy_rules import rule_feed as rule_feed

from tauric import market, strategy_execution, strategy_scheduler
from tauric.models import (
    Owner,
    StrategyRun,
    StrategySchedule,
    StrategyScheduleTick,
)


@pytest.fixture
def clock(monkeypatch, rule_feed):
    monkeypatch.setattr(strategy_scheduler, "now", lambda: rule_feed["now"])
    return rule_feed


def schedule(client, dep, *, revision=0, enabled=True, cadence=60):
    request = {
        **action(),
        "expected_revision": revision,
        "enabled": enabled,
        "cadence_seconds": cadence,
    }
    response = client.put(f"/api/v1/strategies/deployments/{dep['id']}/schedule", json=request)
    assert response.status_code == 200, response.text
    return response.json(), request


def process(database):
    with Session(database[0]) as db:
        return strategy_scheduler.process_one(db)


def test_schedule_auth_csrf_and_empty_default(client, signed_in, database):
    state = signed_in.get("/api/v1/paper").json()
    assert state["strategy_schedules"] == []
    assert not state["strategy_scheduler"]["ready"]
    assert not process(database)
    signed_in.headers.pop("X-CSRF-Token")
    assert (
        signed_in.put("/api/v1/strategies/deployments/missing/schedule", json={}).status_code == 403
    )
    signed_in.cookies.clear()
    assert (
        signed_in.put("/api/v1/strategies/deployments/missing/schedule", json={}).status_code == 401
    )
    assert (
        signed_in.get("/api/v1/strategies/deployments/missing/schedule/history").status_code == 401
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"cadence_seconds": 29},
        {"cadence_seconds": 3601},
        {"cadence_seconds": 60.5},
        {"cadence_seconds": 301},
        {"enabled": "true"},
        {"expected_revision": -1},
        {"unexpected": True},
        {"reason": "   "},
    ],
)
def test_schedule_inputs_bounded(signed_in, setup, clock, changes):
    dep, _ = configured(signed_in, setup)
    req = {**action(), "expected_revision": 0, "enabled": True, "cadence_seconds": 60, **changes}
    assert (
        signed_in.put(f"/api/v1/strategies/deployments/{dep['id']}/schedule", json=req).status_code
        == 422
    )


def test_manual_deployment_and_other_owner_rejected(signed_in, setup, clock, database):
    req = {**action(), "expected_revision": 0, "enabled": True, "cadence_seconds": 60}
    assert (
        signed_in.put(
            f"/api/v1/strategies/deployments/{setup[1]['id']}/schedule", json=req
        ).status_code
        == 422
    )
    dep, _ = configured(signed_in, setup)
    with Session(database[0]) as db:
        other = Owner(id=action()["request_id"], username="another-owner", password_hash="unused")
        from tauric.strategy_contracts import StrategyScheduleChange

        with pytest.raises(HTTPException) as error:
            strategy_scheduler.save(
                db, other, dep["id"], StrategyScheduleChange.model_validate(req)
            )
        assert error.value.status_code == 404
        with pytest.raises(HTTPException):
            strategy_scheduler.history(db, other, dep["id"])


def test_saved_schedule_conflicts_history_and_immutability(signed_in, setup, clock, database):
    dep, _ = configured(signed_in, setup)
    saved, req = schedule(signed_in, dep)
    path = f"/api/v1/strategies/deployments/{dep['id']}/schedule"
    assert signed_in.put(path, json=req).json() == saved
    assert signed_in.put(path, json={**req, "cadence_seconds": 30}).status_code == 409
    assert (
        signed_in.put(
            path, json={**action(), "expected_revision": 0, "enabled": False, "cadence_seconds": 60}
        ).status_code
        == 409
    )
    assert process(database)
    off, _ = schedule(signed_in, dep, revision=1, enabled=False)
    assert off["revision"] == 2 and off["next_due_at"] is None
    assert not process(database)
    history = signed_in.get(path + "/history").json()
    assert len(history["versions"]) == 2 and len(history["ticks"]) == 1
    for table in ("strategy_schedule_versions", "strategy_schedule_ticks"):
        for sql in (
            f"UPDATE {table} SET checksum='bad'",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with database[0].connect() as connection, pytest.raises(DBAPIError):
                connection.execute(text(sql))


def test_due_check_is_atomic_idempotent_and_makes_no_financial_records(
    signed_in, setup, clock, database
):
    dep, _ = configured(signed_in, setup)
    saved, _ = schedule(signed_in, dep)
    before = signed_in.get("/api/v1/paper").json()["records"]
    with Session(database[0]) as db:
        strategy_scheduler.heartbeat(db)
    assert process(database)
    assert not process(database)
    state = signed_in.get("/api/v1/paper").json()
    assert state["strategy_scheduler"]["ready"]
    signal = state["strategy_runs"][0]
    assert signal["action"] == "enter" and signal["payload"]["run_mode"] == "scheduled"
    assert signal["payload"]["request"]["schedule_context"]["version_id"] == saved["version_id"]
    assert state["records"] == before and not state["positions"]
    clock["now"] += timedelta(seconds=60)
    assert process(database)
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(StrategyScheduleTick)) == 2
    assert signed_in.get("/api/v1/paper").json()["records"] == before


def test_worker_crash_rolls_back_and_recovers_once(signed_in, setup, clock, database, monkeypatch):
    dep, _ = configured(signed_in, setup)
    saved, _ = schedule(signed_in, dep)
    real_run = strategy_execution.run

    def interrupted(*args, **kwargs):
        real_run(*args, **kwargs)
        raise SystemExit("Simulate worker termination before commit")

    monkeypatch.setattr(strategy_execution, "run", interrupted)
    with pytest.raises(SystemExit):
        process(database)
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(StrategyRun)) == 0
        assert db.scalar(select(func.count()).select_from(StrategyScheduleTick)) == 0
        assert db.get(StrategySchedule, dep["id"]).next_due_at.isoformat() == saved[
            "next_due_at"
        ].replace("Z", "+00:00")
    monkeypatch.setattr(strategy_execution, "run", real_run)
    clock["now"] += timedelta(hours=4)
    assert process(database)
    assert not process(database)
    state = signed_in.get("/api/v1/paper").json()
    assert len(state["strategy_runs"]) == 1
    assert state["strategy_runs"][0]["payload"]["bars"][-1]["close_time"] > saved["next_due_at"]


def test_competing_workers_only_save_one_due_check(signed_in, setup, clock, database):
    dep, _ = configured(signed_in, setup)
    schedule(signed_in, dep)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: process(database), range(4)))
    assert sum(results) == 1
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(StrategyRun)) == 1
        assert db.scalar(select(func.count()).select_from(StrategyScheduleTick)) == 1


def test_monitor_keeps_attributed_exit_visible_during_halt_and_pause(
    signed_in, setup, clock, database
):
    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)
    fill = filled(signed_in, dep, signal)
    schedule(signed_in, dep)
    assert pause(signed_in, dep, False).status_code == 200
    account = signed_in.get("/api/v1/paper").json()["accounts"][0]
    assert (
        signed_in.put(
            f"/api/v1/paper/accounts/{setup[0]['id']}/halt",
            json={
                "expected_revision": account["revision"],
                "halted": True,
                "reason": "Halt entries during monitoring",
            },
        ).status_code
        == 200
    )
    clock["now"] += timedelta(minutes=45)
    clock["mode"] = "stop"
    assert process(database)
    state = signed_in.get("/api/v1/paper").json()
    exit_signal = state["strategy_runs"][0]
    assert exit_signal["action"] == "exit" and exit_signal["payload"]["position_id"] == fill["id"]
    assert len(state["positions"]) == 1  # Monitor has no automatic close authority.
    assert state["strategy_schedules"][0]["last_action"] == "exit"


def test_paused_entry_is_blocked_without_provider_work(signed_in, setup, clock, database):
    dep, _ = configured(signed_in, setup)
    schedule(signed_in, dep)
    assert pause(signed_in, dep, False).status_code == 200
    calls = clock["calls"]
    assert process(database)
    state = signed_in.get("/api/v1/paper").json()
    assert state["strategy_runs"][0]["action"] == "blocked"
    assert clock["calls"] == calls


def test_feed_outage_and_runtime_failure_are_saved_and_recover(
    signed_in, setup, clock, database, monkeypatch
):
    dep, _ = configured(signed_in, setup)
    schedule(signed_in, dep)
    real_refresh = market.refresh

    def unavailable(*_args, **_kwargs):
        raise HTTPException(429, "Market provider rate limited")

    monkeypatch.setattr(market, "refresh", unavailable)
    assert process(database)
    state = signed_in.get("/api/v1/paper").json()
    assert state["strategy_runs"][0]["action"] == "blocked"
    assert state["strategy_runs"][0]["payload"]["snapshot_id"] is None
    monkeypatch.setattr(market, "refresh", real_refresh)
    clock["now"] += timedelta(seconds=60)
    real_run = strategy_execution.run

    def broken(*_args, **_kwargs):
        raise RuntimeError("Sensitive provider body must not escape")

    monkeypatch.setattr(strategy_execution, "run", broken)
    assert process(database)
    state = signed_in.get("/api/v1/paper").json()
    assert state["strategy_schedules"][0]["last_action"] == "failed"
    assert "Sensitive" not in state["strategy_schedules"][0]["last_error"]
    monkeypatch.setattr(strategy_execution, "run", real_run)
    clock["now"] += timedelta(seconds=60)
    assert process(database)
    assert signed_in.get("/api/v1/paper").json()["strategy_schedules"][0]["last_action"] == "enter"


def test_heartbeat_expiry_is_visible(signed_in, clock, database):
    with Session(database[0]) as db:
        strategy_scheduler.heartbeat(db)
    clock["now"] += timedelta(seconds=61)
    assert not signed_in.get("/api/v1/paper").json()["strategy_scheduler"]["ready"]


def test_monitor_count_is_bounded(signed_in, setup, clock):
    dep, version = configured(signed_in, setup)
    schedule(signed_in, dep)
    scope = signed_in.get("/api/v1/paper").json()["capital_scopes"][0]
    for _ in range(19):
        extra = deploy(signed_in, setup[3], version, scope).json()
        schedule(signed_in, extra)
    extra = deploy(signed_in, setup[3], version, scope).json()
    req = {**action(), "expected_revision": 0, "enabled": True, "cadence_seconds": 60}
    response = signed_in.put(f"/api/v1/strategies/deployments/{extra['id']}/schedule", json=req)
    assert response.status_code == 422 and "20" in response.json()["detail"]
    schedule(signed_in, dep, revision=1, enabled=False)
    schedule(signed_in, extra)


def test_database_rejects_cross_deployment_schedule_state(signed_in, setup, clock, database):
    dep, version = configured(signed_in, setup)
    schedule(signed_in, dep)
    assert process(database)
    scope = signed_in.get("/api/v1/paper").json()["capital_scopes"][0]
    extra = deploy(signed_in, setup[3], version, scope).json()
    other, _ = schedule(signed_in, extra)
    with database[0].connect() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text("UPDATE strategy_schedules SET version_id=:version WHERE deployment_id=:dep"),
            {"version": other["version_id"], "dep": dep["id"]},
        )
