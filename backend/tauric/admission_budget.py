"""Optional owner-wide admission cap, shared by native research and governance."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from . import paper, services
from .broker_models import BrokerReviewJob
from .models import AnalysisJob, GovernanceJob, SharedAdmissionBudgetVersion, now
from .portfolio_research_contracts import SharedAdmissionBudgetView

LOCK = 913570070


def head(db, owner):
    row = db.scalar(
        select(SharedAdmissionBudgetVersion)
        .where(SharedAdmissionBudgetVersion.owner_id == owner.id)
        .order_by(SharedAdmissionBudgetVersion.revision.desc())
        .limit(1)
    )
    if row and paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Shared admission budget integrity failed.")
    return row


def used(db, owner):
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return sum(
        db.scalar(
            select(func.count())
            .select_from(model)
            .where(
                model.owner_id == owner.id,
                model.created_at >= start,
                model.created_at < start + timedelta(days=1),
            )
        )
        for model in (AnalysisJob, GovernanceJob, BrokerReviewJob)
    )


def view(db, owner):
    row = head(db, owner)
    return SharedAdmissionBudgetView(
        revision=row.revision if row else 0,
        daily_job_limit=row.payload["daily_job_limit"] if row else None,
        used_today=used(db, owner),
    )


def lock(db):
    # Never wait while holding another queue/account/owner lock.
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
        raise HTTPException(429, "Shared AI admission is busy. No new job was admitted; try again.")


def check(db, owner):
    lock(db)
    row = head(db, owner)
    if not row:
        return  # Preserve existing limits, while serializing with first-time cap configuration.
    if used(db, owner) >= row.payload["daily_job_limit"]:
        raise HTTPException(
            429, "The shared daily AI job allowance is exhausted. Existing jobs remain readable."
        )


def save(db, owner, body):
    lock(db)
    old = db.scalar(
        select(SharedAdmissionBudgetVersion).where(
            SharedAdmissionBudgetVersion.owner_id == owner.id,
            SharedAdmissionBudgetVersion.request_id == str(body.request_id),
        )
    )
    payload = body.model_dump(mode="json")
    if old:
        if old.payload != payload or paper.checksum(old.payload) != old.checksum:
            raise HTTPException(
                409, "This request identifier belongs to a different budget change."
            )
        return view(db, owner)
    current = head(db, owner)
    if body.expected_revision != (current.revision if current else 0):
        raise HTTPException(409, "The shared admission budget changed. Reload before saving.")
    row = SharedAdmissionBudgetVersion(
        owner_id=owner.id,
        revision=body.expected_revision + 1,
        request_id=str(body.request_id),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "research.shared_budget_changed",
        "owner",
        owner.id,
        body.reason,
        {"revision": row.revision, "daily_job_limit": body.daily_job_limit, "monetary_cap": False},
    )
    db.commit()
    return view(db, owner)
