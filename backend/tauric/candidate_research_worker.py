"""Opt-in downstream admission of published setups; never model or trade execution."""

import logging
import signal
import time
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from . import candidate_research, scanner
from .database import engine
from .models import (
    Owner,
    ScannerAnalysis,
    ScannerCandidate,
    ScannerEvent,
    ScannerProfileVersion,
    WorkerStatus,
)
from .scanner_contracts import CandidateEscalate, PotentialCandidate, ScannerParameters

LOCK = 913570041
STOP = False
logger = logging.getLogger(__name__)


def stop(*_):
    global STOP
    STOP = True


def heartbeat(db):
    db.execute(
        insert(WorkerStatus)
        .values(
            id="candidate_research", heartbeat_at=scanner.now(), version="candidate-research-v1"
        )
        .on_conflict_do_update(
            index_elements=[WorkerStatus.id],
            set_={"heartbeat_at": scanner.now(), "version": "candidate-research-v1"},
        )
    )
    db.commit()


def request_id(owner, profile, candidate):
    return uuid5(
        NAMESPACE_URL,
        f"tauric:automatic-research:{owner.id}:{profile.profile_id}:{candidate.dedupe_key}",
    )


def defer_reason(failure):
    detail = str(failure.detail)
    if "daily" in detail.lower() and "allowance" in detail.lower():
        return "DAILY_ALLOWANCE"
    if detail.startswith("Candidate Gate: "):
        return detail.split(": ", 1)[1].split(".", 1)[0]
    return {
        429: "QUEUE_BUSY",
        503: "WORKER_UNAVAILABLE",
        422: "CONNECTION_OR_SCOPE_UNAVAILABLE",
    }.get(failure.status_code, "ADMISSION_CHANGED")


def process_one(db):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
        return False
    seen, profiles = set(), {}
    for row in db.scalars(
        select(ScannerProfileVersion).order_by(ScannerProfileVersion.revision.desc())
    ):
        if row.profile_id in seen:
            continue
        seen.add(row.profile_id)
        p = ScannerParameters.model_validate(scanner.checked(row).payload["parameters"])
        if p.enabled and p.automatic_research_enabled:
            profiles[row.id] = row
    if not profiles:
        return False
    stamp = scanner.now()
    receipt = aliased(ScannerEvent)
    done_or_waiting = exists(
        select(receipt.id).where(
            receipt.owner_id == ScannerEvent.owner_id,
            receipt.payload["details"]["publication_id"].as_string() == ScannerEvent.id,
            or_(
                receipt.payload["action"]
                .as_string()
                .in_(["automatic_research_admitted", "automatic_research_skipped"]),
                (receipt.payload["action"].as_string() == "automatic_research_deferred")
                & (receipt.payload["details"]["retry_at"].as_string() > stamp.isoformat()),
            ),
        )
    )
    publication = db.scalar(
        select(ScannerEvent)
        .where(
            ScannerEvent.payload["action"].as_string() == "published",
            ScannerEvent.payload["details"]["candidate"]["profile_version_id"]
            .as_string()
            .in_(profiles),
            ~done_or_waiting,
        )
        .order_by(ScannerEvent.created_at, ScannerEvent.id)
        .limit(1)
    )
    if publication is None:
        return False
    candidate = PotentialCandidate.model_validate(
        scanner.checked(publication).payload["details"]["candidate"]
    )
    profile = profiles[candidate.profile_version_id]
    owner = db.get(Owner, profile.owner_id)
    identifier = request_id(owner, profile, candidate)
    prior = db.scalar(
        select(ScannerAnalysis).where(
            ScannerAnalysis.owner_id == owner.id, ScannerAnalysis.request_id == str(identifier)
        )
    )
    details = {
        "publication_id": publication.id,
        "profile_version_id": profile.id,
        "execution_authority": False,
    }
    try:
        # Preserve the coordinator lock while rolling back a rejected admission's partial work.
        with db.begin_nested():
            if prior:
                job_id = scanner.checked(prior).job_id
            else:
                result = candidate_research.escalate(
                    db,
                    owner,
                    candidate.candidate_id,
                    CandidateEscalate(
                        request_id=identifier,
                        profile_version_id=profile.id,
                        reason="Automatically admit a published, gate-approved ranked setup to daily research.",
                    ),
                    commit=False,
                    automatic=True,
                )
                job_id = result.id
    except HTTPException as failure:
        reason = defer_reason(failure)
        row = db.get(ScannerCandidate, candidate.candidate_id)
        expired = not row or stamp >= row.created_at + timedelta(
            seconds=profile.payload["parameters"]["candidate_ttl_seconds"]
        )
        retry_at = (
            stamp.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            if reason == "DAILY_ALLOWANCE"
            else stamp + timedelta(seconds=30)
        )
        scanner.event(
            db,
            owner,
            candidate.candidate_id,
            "automatic_research_skipped" if expired else "automatic_research_deferred",
            "Automatic research admission: " + reason,
            {**details, "reason_code": reason, "retry_at": retry_at.isoformat()},
        )
    else:
        scanner.event(
            db,
            owner,
            candidate.candidate_id,
            "automatic_research_admitted",
            "Published setup admitted or existing daily research reused automatically.",
            {**details, "job_id": job_id},
        )
    db.commit()
    return True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO)
    logger.info("Automatic candidate research coordinator started. Saved opt-in profiles only.")
    while not STOP:
        try:
            with Session(engine()) as db:
                heartbeat(db)
            with Session(engine()) as db:
                worked = process_one(db)
            if not worked:
                time.sleep(2)
        except Exception:
            logger.error("CANDIDATE_RESEARCH_UNAVAILABLE: inspect saved admission state")
            time.sleep(3)


if __name__ == "__main__":
    main()
