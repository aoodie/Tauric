"""Operational evidence never grants execution permission or includes error bodies."""

import json
import time
from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .broker_adapters import BrokerError
from .broker_models import BrokerCloseRequest, BrokerOrderIntent, BrokerReviewJob
from .database import engine
from .models import AnalysisJob, now
from .operations_models import WorkerLaneProgress

LANES = {
    "broker_execution": ("positions", "orders", "discovery", "advancement", "delivery"),
    "valuation": ("observation",),
    "forward_validation": ("evaluation",),
}


def category(error):
    if isinstance(error, (TimeoutError, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, SQLAlchemyError):
        return "storage"
    if isinstance(error, httpx.HTTPError):
        return "transport"
    if isinstance(error, BrokerError):
        return "broker_evidence"
    if isinstance(error, HTTPException):
        return "rate_limit" if error.status_code == 429 else "precondition"
    return "unexpected"


def _record(worker, lane, invocation_id, started, *, result=None, error=None, duration=None):
    with Session(engine()) as db:
        db.execute(
            insert(WorkerLaneProgress)
            .values(worker=worker, lane=lane, invocation_id=invocation_id, started_at=started)
            .on_conflict_do_nothing()
        )
        row = db.scalar(
            select(WorkerLaneProgress)
            .where(WorkerLaneProgress.worker == worker, WorkerLaneProgress.lane == lane)
            .with_for_update()
        )
        if duration is None:
            row.attempts += 1
            row.invocation_id, row.started_at = invocation_id, started
            row.finished_at = None
        else:
            stamp = now()
            if error is None:
                row.successes += 1
                row.work_completed += int(bool(result))
                row.last_success_at = stamp
                if result:
                    row.last_work_at = stamp
            else:
                row.failures += 1
                row.last_failure_at = stamp
            if row.invocation_id == invocation_id:
                row.finished_at, row.duration_ms = stamp, duration
                row.consecutive_failures = row.consecutive_failures + 1 if error else 0
                row.error_category = category(error) if error else None
        db.commit()


def record(*args, **kwargs):
    try:
        _record(*args, **kwargs)
    except Exception:
        # Metrics failure must never prevent protection, exits or reconciliation.
        print(json.dumps({"event": "progress_storage_unavailable", "worker": args[0], "lane": args[1]}), flush=True)


def run_lane(worker, lane, work):
    if lane not in LANES.get(worker, ()):
        raise ValueError("Unknown operating lane")
    invocation, started, clock = str(uuid4()), now(), time.monotonic()
    record(worker, lane, invocation, started)
    result, failure = False, None
    try:
        with Session(engine()) as db:
            result = bool(work(db))
    except Exception as error:
        failure = error
        print(json.dumps({
            "event": "worker_lane_failed", "worker": worker, "lane": lane,
            "invocation_id": invocation, "error_category": category(error),
        }), flush=True)
    record(worker, lane, invocation, started, result=result, error=failure,
           duration=max(0, int((time.monotonic() - clock) * 1000)))
    return result


def state(db, owner):
    stamp = now()
    saved = {(r.worker, r.lane): r for r in db.scalars(select(WorkerLaneProgress))}
    lanes = []
    for worker, names in LANES.items():
        for lane in names:
            row = saved.get((worker, lane))
            status = "unobserved"
            if row:
                if not timedelta(0) <= stamp - row.started_at <= timedelta(seconds=120):
                    status = "stale"
                elif row.consecutive_failures:
                    status = "failing"
                elif row.finished_at is None:
                    status = "running"
                else:
                    status = "healthy"
            lanes.append({"worker": worker, "lane": lane, "status": status, **({
                key: getattr(row, key) for key in (
                    "attempts", "successes", "failures", "work_completed", "consecutive_failures",
                    "invocation_id", "started_at", "finished_at", "last_success_at", "last_work_at",
                    "last_failure_at", "error_category", "duration_ms",
                )
            } if row else {})})

    def queued(model, states):
        count, earliest = db.execute(
            select(func.count(), func.min(model.created_at)).where(
                model.owner_id == owner.id, model.status.in_(states)
            )
        ).one()
        return {"count": count, "oldest_age_seconds": max(0, (stamp - earliest).total_seconds()) if earliest else None}

    return {
        "generated_at": stamp, "lanes": lanes,
        "native_queue": queued(AnalysisJob, ["queued"]),
        "review_queue": queued(BrokerReviewJob, ["queued"]),
        "unresolved_entries": queued(BrokerOrderIntent, ["prepared", "submitting", "pending", "partially_filled", "unknown", "cancelling"]),
        "unresolved_closes": queued(BrokerCloseRequest, ["prepared", "cancelling_protection", "submitting", "pending", "partially_filled", "unknown"]),
        "scope": "Worker progress and owned queues; idle success does not prove a connected trading cycle.",
    }
