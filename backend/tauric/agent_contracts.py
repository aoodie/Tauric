"""Owner-authorized project work never grants financial authority."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field

from .portfolio_contracts import ChatIdentity
from .schemas import Contract

Scope = Literal["research", "engineering", "operations"]


class Criterion(Contract):
    description: str = Field(min_length=3, max_length=500)
    proof: Literal[
        "research_report",
        "source_evidence",
        "job_success",
        "command_success",
        "tests_passed",
        "child_complete",
        "deployment_verified",
    ]


class TaskCreate(Contract):
    request_id: UUID
    objective: str = Field(min_length=3, max_length=4000)
    criteria: list[Criterion] = Field(min_length=1, max_length=12)
    scope: list[Scope] = Field(default=["research", "engineering", "operations"], min_length=1)
    parent_id: UUID | None = None
    dependencies: list[UUID] = Field(default_factory=list, max_length=12)
    max_wakes: int = Field(default=12, ge=1, le=40, strict=True)
    max_attempts: int = Field(default=3, ge=1, le=5, strict=True)
    deadline_hours: int = Field(default=24, ge=1, le=168, strict=True)
    conversation_id: UUID | None = None


class TaskControl(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal["pause", "resume", "cancel"]
    reason: str = Field(min_length=3, max_length=500)


class JobReference(Contract):
    kind: Literal[
        "analysis",
        "quant",
        "discovery",
        "open_research",
        "engineering",
        "assistant_receipt",
        "task",
        "release",
    ]
    id: UUID


class TaskInvocation(ChatIdentity):
    action: Literal["create", "read", "wait", "complete", "needs_owner"]
    task_id: UUID | None = None
    effect_key: str | None = Field(
        default=None, min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$"
    )
    parameters: dict = Field(default_factory=dict, max_length=20)


class TaskWait(Contract):
    job: JobReference
    reason: str = Field(min_length=3, max_length=500)


class TaskComplete(Contract):
    evidence: list[JobReference] = Field(min_length=1, max_length=12)
    summary: str = Field(min_length=3, max_length=4000)


class EngineeringInvocation(ChatIdentity):
    task_id: UUID
    effect_key: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")
    script: str = Field(min_length=1, max_length=30000)
    timeout_seconds: int = Field(default=120, ge=1, le=600, strict=True)
    purpose: Literal["inspect", "develop", "test"] = "develop"


class TaskView(Contract):
    id: str
    parent_id: str | None
    root_id: str
    objective: str
    criteria: list[Criterion]
    dependencies: list[str]
    scope: list[Scope]
    status: str
    revision: int
    attempts: int
    max_attempts: int
    max_wakes: int
    deadline: AwareDatetime
    next_check_at: AwareDatetime | None
    waiting: dict
    result: dict
    error: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    executor: str
    trace_id: str
    active_turn_id: str | None
    lease_id: str | None
    heartbeat_at: AwareDatetime | None
