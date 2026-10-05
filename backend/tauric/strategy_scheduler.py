"""Opt-in closed-candle paper monitoring; never proposals, reviews or fills.

Owner locks follow the existing rules/action lock order. One current evaluation,
its immutable check receipt and next due time commit together. PostgreSQL releases
locks after interruption, leaving the due check available for a fresh evaluation.
Missed intervals are coalesced, not replayed against historical candles.
"""

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError

from . import market, paper, services, strategy_execution
from .models import (
    Owner,
    StrategySchedule,
    StrategyScheduleTick,
    StrategyScheduleVersion,
    WorkerStatus,
    now,
)
from .strategy_contracts import (
    StrategyRunRequest,
    StrategyScheduleChange,
    StrategyScheduleHistory,
    StrategySchedulerStatus,
    StrategyScheduleView,
)

WORKER_ID = "strategy_scheduler"


def checked(row):
    if not row or paper.checksum(row.payload) != row.checksum:
        paper.fail("Schedule evidence integrity failed.")
    return row


def heartbeat(db):
    db.execute(
        insert(WorkerStatus)
        .values(id=WORKER_ID, heartbeat_at=now(), version="paper-monitor-v1")
        .on_conflict_do_update(
            index_elements=[WorkerStatus.id],
            set_={"heartbeat_at": now(), "version": "paper-monitor-v1"},
        )
    )
    db.commit()


def status(db):
    row = db.get(WorkerStatus, WORKER_ID)
    return StrategySchedulerStatus(
        ready=bool(row and 0 <= (now() - row.heartbeat_at).total_seconds() <= 60),
        heartbeat_at=row.heartbeat_at if row else None,
    )


def view(db, row):
    version = checked(db.get(StrategyScheduleVersion, row.version_id))
    tick = checked(db.get(StrategyScheduleTick, row.last_tick_id)) if row.last_tick_id else None
    return StrategyScheduleView(
        deployment_id=row.deployment_id,
        version_id=version.id,
        revision=version.revision,
        enabled=version.payload["enabled"],
        cadence_seconds=version.payload["cadence_seconds"],
        reason=version.payload["reason"],
        next_due_at=row.next_due_at,
        last_checked_at=tick.created_at if tick else None,
        last_run_id=tick.run_id if tick else None,
        last_action=tick.payload.get("action") if tick else None,
        last_error=tick.payload.get("error") if tick else None,
    )


def views(db, owner):
    return [
        view(db, row)
        for row in db.scalars(
            select(StrategySchedule)
            .where(StrategySchedule.owner_id == owner.id)
            .order_by(StrategySchedule.deployment_id)
        )
    ]


def save(db, owner, deployment_id, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    dep = paper.deployment(db, owner, deployment_id)
    _, rules = strategy_execution.rules_for(db, owner, dep)
    if body.cadence_seconds > min(
        market.INTERVAL_SECONDS[rules.interval], rules.signal_ttl_seconds
    ):
        paper.fail(
            "Check frequency must fit within the rule candle interval and signal validity.", 422
        )
    request = body.model_dump(mode="json")
    previous = db.scalar(
        select(StrategyScheduleVersion).where(
            StrategyScheduleVersion.owner_id == owner.id,
            StrategyScheduleVersion.request_id == str(body.request_id),
        )
    )
    if previous:
        if previous.deployment_id != dep.id or checked(previous).payload != request:
            paper.fail("This request identifier was already used for another schedule change.")
        return view(db, db.get(StrategySchedule, dep.id))
    row = db.get(StrategySchedule, dep.id)
    revision = checked(db.get(StrategyScheduleVersion, row.version_id)).revision if row else 0
    if body.expected_revision != revision:
        paper.fail("The schedule changed. Reload before saving.")
    # Bound active work per owner; cadence cannot create an unlimited work queue.
    if body.enabled and (not row or row.next_due_at is None):
        active = list(
            db.scalars(
                select(StrategySchedule).where(
                    StrategySchedule.owner_id == owner.id, StrategySchedule.next_due_at.is_not(None)
                )
            )
        )
        if len(active) >= 20:
            paper.fail("At most 20 paper monitors may be enabled at once.", 422)
    version = StrategyScheduleVersion(
        owner_id=owner.id,
        deployment_id=dep.id,
        revision=revision + 1,
        request_id=str(body.request_id),
        payload=request,
        checksum=paper.checksum(request),
    )
    db.add(version)
    db.flush()
    if row is None:
        row = StrategySchedule(deployment_id=dep.id, owner_id=owner.id, version_id=version.id)
        db.add(row)
    row.version_id = version.id
    row.next_due_at = now() if body.enabled else None
    row.last_tick_id = None
    services.audit(
        db,
        owner,
        "strategy.schedule_changed",
        "deployment",
        dep.id,
        body.reason,
        {
            "revision": version.revision,
            "enabled": body.enabled,
            "cadence_seconds": body.cadence_seconds,
        },
    )
    db.commit()
    return view(db, row)


def history(db, owner, deployment_id):
    dep = paper.deployment(db, owner, deployment_id)

    def item(row):
        checked(row)
        return {
            "id": row.id,
            "payload": row.payload,
            "checksum": row.checksum,
            "created_at": row.created_at.isoformat(),
        }

    return StrategyScheduleHistory(
        versions=[
            item(r)
            for r in db.scalars(
                select(StrategyScheduleVersion)
                .where(StrategyScheduleVersion.deployment_id == dep.id)
                .order_by(StrategyScheduleVersion.revision.desc())
                .limit(100)
            )
        ],
        ticks=[
            item(r)
            for r in db.scalars(
                select(StrategyScheduleTick)
                .where(StrategyScheduleTick.deployment_id == dep.id)
                .order_by(StrategyScheduleTick.created_at.desc())
                .limit(100)
            )
        ],
    )


def process_one(db):
    # Lock owner first, just as manual evaluation/settings do. Competing workers
    # skip locked owners; they cannot each capture a different schedule lock and
    # then deadlock waiting for the shared owner/account lock.
    owner_id = db.scalar(
        select(StrategySchedule.owner_id)
        .where(StrategySchedule.next_due_at <= now())
        .order_by(StrategySchedule.next_due_at)
        .limit(1)
    )
    if not owner_id:
        return False
    owner = db.scalar(select(Owner).where(Owner.id == owner_id).with_for_update(skip_locked=True))
    if not owner:
        return False
    row = db.scalar(
        select(StrategySchedule)
        .where(StrategySchedule.owner_id == owner.id, StrategySchedule.next_due_at <= now())
        .order_by(StrategySchedule.next_due_at, StrategySchedule.deployment_id)
        .with_for_update()
        .limit(1)
    )
    if not row:
        return False
    version = checked(db.get(StrategyScheduleVersion, row.version_id))
    settings = StrategyScheduleChange.model_validate(version.payload)
    if not settings.enabled:
        paper.fail("A disabled schedule cannot be evaluated.")
    due_at = row.next_due_at
    context = {"version_id": version.id, "revision": version.revision, "due_at": due_at.isoformat()}
    request = StrategyRunRequest(
        request_id=uuid5(NAMESPACE_URL, f"tauric/schedule/{version.id}/{due_at.isoformat()}"),
        deployment_id=row.deployment_id,
        reason="Scheduled paper rules and attributed exit monitoring",
    )
    result = None
    error = None
    try:
        with db.begin_nested():
            result = strategy_execution.run(
                db, owner, request, commit=False, schedule_context=context
            )
    except SQLAlchemyError:
        raise  # Storage failures leave the due check intact; reconnect later.
    except HTTPException as failure:
        error = str(failure.detail)
    except Exception:
        # Provider/runtime detail can include secrets; expose a stable error.
        error = "Scheduled evaluation failed. Inspect the deployment and evidence; the next check will use fresh data."
    payload = {
        **context,
        "deployment_id": row.deployment_id,
        "run_id": result.id if result else None,
        "action": result.action if result else "failed",
        "error": error,
        "mode": "paper",
        "execution_authority": False,
        "recovery": "Missed checks coalesced into one current evaluation; no historical catch-up.",
    }
    tick = StrategyScheduleTick(
        owner_id=owner.id,
        deployment_id=row.deployment_id,
        version_id=version.id,
        due_at=due_at,
        run_id=result.id if result else None,
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(tick)
    db.flush()
    row.last_tick_id = tick.id
    row.next_due_at = now() + timedelta(seconds=settings.cadence_seconds)
    db.commit()
    return True
