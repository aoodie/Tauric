"""One fair, leased OpenClaw queue across paper and demo broker reviews."""

from sqlalchemy import func, select

from .broker_models import BrokerReviewJob
from .models import GovernanceJob, identifier

LOCK = 830603
LEASE_SECONDS = 45
MODELS = {"paper": GovernanceJob, "broker": BrokerReviewJob}


def active_count(db):
    return sum(
        db.scalar(
            select(func.count()).select_from(model).where(model.status.in_(["queued", "running"]))
        )
        for model in MODELS.values()
    )


def claim(db, kind, stamp):
    db.execute(select(func.pg_advisory_xact_lock(LOCK)))
    for model in MODELS.values():
        for job in db.scalars(select(model).where(model.status == "running").with_for_update()):
            if (
                not job.heartbeat_at
                or not 0 <= (stamp - job.heartbeat_at).total_seconds() < LEASE_SECONDS
            ):
                job.status, job.finished_at, job.error = (
                    "interrupted",
                    stamp,
                    "The review worker stopped responding. No automatic retry was made.",
                )
    db.flush()
    if any(
        db.scalar(select(model.id).where(model.status == "running").limit(1))
        for model in MODELS.values()
    ):
        db.commit()
        return None
    candidates = []
    for name, model in MODELS.items():
        job = db.scalar(
            select(model)
            .where(model.status == "queued")
            .order_by(model.created_at, model.id)
            .limit(1)
            .with_for_update()
        )
        if job:
            candidates.append((job.created_at, job.id, name, job))
    first = min(candidates, key=lambda value: value[:3]) if candidates else None
    job = first[3] if first and first[2] == kind else None
    if job:
        job.status, job.lease_id, job.started_at, job.heartbeat_at = (
            "running",
            identifier(),
            stamp,
            stamp,
        )
    db.commit()
    return (job.id, job.lease_id) if job else None


def renew(db, model, job_id, lease, stamp):
    job = db.scalar(select(model).where(model.id == job_id).with_for_update())
    if (
        not job
        or job.status != "running"
        or job.lease_id != lease
        or not job.heartbeat_at
        or not 0 <= (stamp - job.heartbeat_at).total_seconds() < LEASE_SECONDS
    ):
        return False
    job.heartbeat_at = stamp
    db.commit()
    return True
