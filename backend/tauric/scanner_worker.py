"""Opt-in deterministic scans. This worker never submits research or trades."""

import logging
import signal
import time
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import candidate_pipeline, scanner
from .database import engine
from .models import (
    Owner,
    ScannerCandidate,
    ScannerEvent,
    ScannerMatch,
    ScannerProfileVersion,
    ScannerRun,
)
from .scanner_contracts import ScannerParameters, ScannerRunRequest

STOP = False
logger = logging.getLogger(__name__)


def stop(*_):
    global STOP
    STOP = True


def due(parameters, last_run_at, stamp, watching=False):
    if not parameters.enabled or not parameters.automatic_enabled:
        return False
    seconds = parameters.watch_scan_seconds if watching else parameters.scan_seconds
    return last_run_at is None or stamp >= last_run_at + timedelta(seconds=seconds)


def process_one(db):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(scanner.LOCK))):
        return False
    seen = set()
    ready = []
    stamp = scanner.now()
    for profile in db.scalars(
        select(ScannerProfileVersion).order_by(ScannerProfileVersion.revision.desc())
    ):
        if profile.profile_id in seen:
            continue
        seen.add(profile.profile_id)
        p = ScannerParameters.model_validate(scanner.checked(profile).payload["parameters"])
        if not p.enabled or not p.automatic_enabled:
            continue
        last = db.scalar(
            select(ScannerRun)
            .where(ScannerRun.profile_version_id == profile.id)
            .order_by(ScannerRun.created_at.desc())
            .limit(1)
        )
        # A live candidate/watch match uses the shorter cadence, without position checks.
        watching = False
        for candidate_id in db.scalars(
            select(ScannerCandidate.id)
            .join(ScannerMatch)
            .where(ScannerMatch.profile_version_id == profile.id, ScannerMatch.expires_at > stamp)
        ):
            action = db.scalar(
                select(ScannerEvent)
                .where(
                    ScannerEvent.candidate_id == candidate_id,
                    ScannerEvent.payload["action"].as_string().in_(["watch", "ignore"]),
                )
                .order_by(ScannerEvent.created_at.desc(), ScannerEvent.id.desc())
                .limit(1)
            )
            if not action or scanner.checked(action).payload["action"] != "ignore":
                watching = True
                break
        if due(p, last.created_at if last else None, stamp, watching):
            seconds = p.watch_scan_seconds if watching else p.scan_seconds
            ready.append(
                (
                    last.created_at + timedelta(seconds=seconds) if last else profile.created_at,
                    profile,
                )
            )
    if ready:
        # Oldest due profile wins, so slow cycles cannot starve other saved universes.
        profile = min(ready, key=lambda item: (item[0], item[1].profile_id))[1]
        candidate_pipeline.run(
            db,
            db.get(Owner, profile.owner_id),
            ScannerRunRequest(
                request_id=uuid4(), profile_version_id=profile.id, trigger="scheduled"
            ),
        )
        return True
    return False


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logging.basicConfig(level=logging.INFO)
    logger.info("Scanner worker started. Saved opt-in profiles only; zero model calls.")
    while not STOP:
        try:
            with Session(engine()) as db:
                worked = process_one(db)
            if not worked:
                time.sleep(2)
        except Exception:
            # Exception text may include provider credentials. Record a bounded reason only.
            logger.error("SCANNER_CYCLE_FAILED: retrying saved profiles after reconnect")
            time.sleep(3)


if __name__ == "__main__":
    main()
