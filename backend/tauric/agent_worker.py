"""Wake persisted project work; do not continuously call a model."""

import logging
import signal
import time

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import agent_tasks, engineering
from .agent_models import EngineeringJob
from .database import engine
from .models import Owner, WorkerStatus, now

STOP = False
LOG = logging.getLogger(__name__)


def stop(*_):
    global STOP
    STOP = True


def step(db):
    for row in db.scalars(
        select(EngineeringJob)
        .where(EngineeringJob.status == "queued")
        .order_by(EngineeringJob.created_at)
        .limit(10)
    ):
        owner = db.get(Owner, row.owner_id)
        try:
            engineering.observe(db, owner, row.id)
        except HTTPException as error:
            task = agent_tasks.owned(db, owner, row.task_id)
            task.status, task.error, task.next_check_at = "requires_owner", str(error.detail), None
            row.status = "failed"
            agent_tasks.event(
                db, task, "engineering_evidence_failed", {"job_id": row.id, "error": task.error}
            )
    db.commit()
    agent_tasks.tick(db)
    status = db.get(WorkerStatus, "agent_tasks")
    if status:
        status.heartbeat_at = now()
    else:
        db.add(WorkerStatus(id="agent_tasks", heartbeat_at=now(), version="agent-tasks-v1"))
    db.commit()


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not STOP:
        try:
            with Session(engine()) as db:
                step(db)
        except Exception:
            LOG.exception("Project supervision failed; original request identities retained")
            time.sleep(5)
        time.sleep(2)


if __name__ == "__main__":
    main()
