from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from .schemas import Contract


class GovernanceEnqueue(Contract):
    request_id: UUID
    proposal_id: UUID
    profile: Literal["cloud", "local"]
    reason: str = Field(min_length=3, max_length=500)


class GovernanceResult(Contract):
    job_id: UUID
    context_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_id: UUID
    policy_id: UUID
    outcome: Literal["allow", "reject", "defer"]
    reason: str = Field(min_length=3, max_length=500, pattern=r".*\S.*")


class GovernanceJobView(Contract):
    id: str
    proposal_id: str
    profile: str
    model: str
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    decision_id: str | None
    error: str | None
    context_checksum: str
    usage: dict[str, int]


class GovernanceRouteView(Contract):
    id: Literal["cloud", "local"]
    configured: bool
    ready: bool
    model: str | None
    detail: str


class GovernanceStatusView(Contract):
    worker_ready: bool
    gateway_ready: bool
    routes: list[GovernanceRouteView]
    jobs: list[GovernanceJobView]


class GatewayHeartbeat(Contract):
    fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    capabilities: list[Literal["paper_governance", "broker_entry_review"]] = Field(
        default_factory=list, max_length=2
    )
