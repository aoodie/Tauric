"""Progress reflects completed work, survives failures and never stores private errors."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from tauric import operations
from tauric.models import Owner, now
from tauric.operations_models import WorkerLaneProgress


def test_idle_is_distinct_from_work_and_progress_survives_a_worker_restart(database):
    assert not operations.run_lane("valuation", "observation", lambda db: False)
    assert operations.run_lane("valuation", "observation", lambda db: True)
    with Session(database[0]) as db:
        row = db.get(WorkerLaneProgress, ("valuation", "observation"))
        assert (row.attempts, row.successes, row.work_completed, row.failures) == (2, 2, 1, 0)
        assert row.last_work_at and row.last_success_at and row.finished_at
    assert not operations.run_lane("valuation", "observation", lambda db: False)
    with Session(database[0]) as db:
        row = db.get(WorkerLaneProgress, ("valuation", "observation"))
        assert (row.attempts, row.successes, row.work_completed) == (3, 3, 1)


def test_failure_rolls_back_work_redacts_details_and_does_not_stop_other_lanes(database, capsys):
    secret = "fixture-private-broker-token-never-record"

    def failed(db):
        owner = db.scalar(select(Owner))
        owner.username = "uncommitted-failed-change"
        db.flush()
        raise TimeoutError(secret)

    assert not operations.run_lane("broker_execution", "orders", failed)
    assert operations.run_lane("broker_execution", "positions", lambda db: True)
    with Session(database[0]) as db:
        assert db.scalar(select(Owner)).username == "test-owner"
        owner = db.scalar(select(Owner))
        state = operations.state(db, owner)
        failed_lane = next(item for item in state["lanes"] if item["lane"] == "orders")
        assert failed_lane["status"] == "failing" and failed_lane["failures"] == 1
        assert failed_lane["error_category"] == "timeout"
        assert secret not in str(state)
    assert secret not in capsys.readouterr().out
    assert operations.run_lane("broker_execution", "orders", lambda db: True)
    with Session(database[0]) as db:
        row = db.get(WorkerLaneProgress, ("broker_execution", "orders"))
        assert row.failures == 1 and row.consecutive_failures == 0 and row.error_category is None


def test_interrupted_and_stale_progress_is_not_reported_healthy(database):
    operations.record("forward_validation", "evaluation", "interrupted", now())
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        state = operations.state(db, owner)
        lane = next(item for item in state["lanes"] if item["worker"] == "forward_validation")
        assert lane["status"] == "running" and lane["successes"] == 0
        row = db.get(WorkerLaneProgress, ("forward_validation", "evaluation"))
        row.started_at = now() - timedelta(minutes=3)
        db.commit()
        lane = next(item for item in operations.state(db, owner)["lanes"] if item["worker"] == "forward_validation")
        assert lane["status"] == "stale"


def test_progress_storage_outage_does_not_block_protection_work(database, monkeypatch, capsys):
    def storage_failure(*args, **kwargs):
        raise RuntimeError("fixture-private-storage-url")

    monkeypatch.setattr(operations, "_record", storage_failure)
    assert operations.run_lane("broker_execution", "positions", lambda db: True)
    output = capsys.readouterr().out
    assert "progress_storage_unavailable" in output
    assert "fixture-private-storage-url" not in output


def test_operating_state_requires_owner_authentication(client, signed_in):
    assert signed_in.get("/api/v1/trading-readiness/operations").status_code == 200
    signed_in.delete("/api/v1/session")
    assert client.get("/api/v1/trading-readiness/operations").status_code == 401
