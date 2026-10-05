"""Explicit paper-only execution policies and inspectable workflow projections."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .schemas import Contract


class AutonomyPolicyChange(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)
    auto_entries: bool = Field(strict=True)
    auto_exits: bool = Field(strict=True)
    profile: Literal["cloud", "local"]
    daily_research_limit: int = Field(ge=0, le=10, strict=True)
    daily_review_limit: int = Field(ge=0, le=50, strict=True)
    daily_entry_limit: int = Field(ge=1, le=20, strict=True)
    report_max_age_seconds: int = Field(ge=300, le=86400, strict=True)
    workflow_timeout_seconds: int = Field(ge=300, le=1800, strict=True)
    reason: str = Field(min_length=3, max_length=500, pattern=r".*\S.*")

    @model_validator(mode="after")
    def enabled_actions(self):
        if self.enabled and not (self.auto_entries or self.auto_exits):
            raise ValueError("Enable an entry or exit action for autonomous paper execution.")
        return self


class AutonomyPolicyView(Contract):
    id: str
    deployment_id: str
    revision: int
    settings: AutonomyPolicyChange
    created_at: datetime
    usage_today: dict[str, int]


class AutonomyWorkflowView(Contract):
    id: str
    deployment_id: str
    policy_version_id: str
    kind: Literal["entry", "exit"]
    status: str
    initial_signal_id: str
    analysis_job_id: str | None
    native_artifact_id: str | None
    review_job_id: str | None
    proposal_id: str | None
    decision_id: str | None
    plan_id: str | None
    entry_fill_id: str | None
    close_fill_id: str | None
    reason: str
    created_at: datetime


class AutonomyEventView(Contract):
    id: str
    workflow_id: str
    kind: str
    payload: dict
    checksum: str
    created_at: datetime


class AutonomyHistory(Contract):
    policies: list[AutonomyPolicyView]
    events: list[AutonomyEventView]


class AutonomyState(Contract):
    worker_ready: bool
    policies: list[AutonomyPolicyView]
    workflows: list[AutonomyWorkflowView]
