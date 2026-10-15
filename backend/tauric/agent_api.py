"""Visible owner controls and owner-bound Gateway tool endpoints."""

from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from . import agent_tasks, assistant, engineering, release_jobs
from .agent_contracts import (
    EngineeringInvocation,
    TaskControl,
    TaskCreate,
    TaskInvocation,
    TaskView,
)
from .agent_models import AgentTask, AgentTaskEvent, EngineeringJob
from .auth import CurrentOwner, Db
from .openclaw_api import service_owner
from .release_contracts import ReleaseInvocation

router = APIRouter(tags=["OpenClaw project work"])


@router.get("/api/v1/assistant/tasks", response_model=list[TaskView])
def tasks(db: Db, owner: CurrentOwner):
    return [
        agent_tasks.view(row, db)
        for row in db.scalars(
            select(AgentTask)
            .where(AgentTask.owner_id == owner.id)
            .order_by(AgentTask.created_at.desc())
            .limit(100)
        )
    ]


@router.post("/api/v1/assistant/tasks", status_code=201, response_model=TaskView)
def create(body: TaskCreate, db: Db, owner: CurrentOwner):
    return agent_tasks.create(db, owner, body)


@router.post("/api/v1/assistant/tasks/{task_id}/control", response_model=TaskView)
def control(task_id: UUID, body: TaskControl, db: Db, owner: CurrentOwner):
    result = agent_tasks.control(db, owner, task_id, body)
    for job in db.scalars(
        select(EngineeringJob).where(
            EngineeringJob.owner_id == owner.id, EngineeringJob.status == "queued"
        )
    ):
        engineering.observe(db, owner, job.id)
    db.commit()
    return result


@router.get("/api/v1/assistant/tasks/{task_id}")
def task(task_id: UUID, db: Db, owner: CurrentOwner):
    row = agent_tasks.owned(db, owner, task_id)
    history = list(
        db.scalars(
            select(AgentTaskEvent)
            .where(AgentTaskEvent.task_id == row.id)
            .order_by(AgentTaskEvent.sequence)
        )
    )
    return {
        "task": agent_tasks.view(row, db),
        "events": [
            {
                "kind": e.kind,
                "payload": e.payload,
                "checksum": e.checksum,
                "created_at": e.created_at,
            }
            for e in history
        ],
    }


@router.post("/internal/openclaw/task")
def task_tool(body: TaskInvocation, request: Request, db: Db):
    owner = service_owner(request, db)
    turn = assistant.authorize(db, owner, body)
    return agent_tasks.invoke(db, owner, turn, body)


@router.post("/internal/openclaw/engineering")
def engineering_tool(body: EngineeringInvocation, request: Request, db: Db):
    owner = service_owner(request, db)
    turn = assistant.authorize(db, owner, body)
    return engineering.enqueue(db, owner, turn, body)


@router.get("/api/v1/assistant/engineering/{job_id}")
def engineering_job(job_id: UUID, db: Db, owner: CurrentOwner):
    row = engineering.observe(db, owner, str(job_id))
    db.commit()
    return engineering.view(row)


@router.get("/api/v1/assistant/engineering/{job_id}/source")
def engineering_source(job_id: UUID, db: Db, owner: CurrentOwner):
    result = engineering.source(db, owner, job_id)
    db.commit()
    return result


@router.post("/internal/openclaw/release")
def release_tool(body: ReleaseInvocation, request: Request, db: Db):
    owner = service_owner(request, db)
    turn = assistant.authorize(db, owner, body)
    return release_jobs.enqueue(db, owner, turn, body)


@router.get("/api/v1/assistant/releases/{job_id}")
def release_job(job_id: UUID, db: Db, owner: CurrentOwner):
    return release_jobs.observe(db, owner, job_id)
