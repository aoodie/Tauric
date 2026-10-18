"""A release consumes existing signed test evidence, never submitted commands."""

from uuid import UUID

from pydantic import Field

from .portfolio_contracts import ChatIdentity


class ReleaseInvocation(ChatIdentity):
    task_id: UUID
    test_job_id: UUID
    effect_key: str = Field(min_length=3, max_length=80, pattern=r"^[a-zA-Z0-9_.:-]+$")
