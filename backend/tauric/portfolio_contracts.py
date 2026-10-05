"""Portfolio mandates around existing account allocations; no trading permission."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .portfolio_research_contracts import PortfolioResearchParameters
from .schemas import Contract


class PortfolioPreferences(Contract):
    mandate: str = Field(min_length=3, max_length=2000)
    research_objective: Literal["net_return", "balanced", "consistency"]
    capital_mode: Literal["fixed", "managed"] = "fixed"
    chat_edit_preferences: bool = Field(default=False, strict=True)
    chat_control_deployments: bool = Field(default=False, strict=True)

    @field_validator("mandate")
    @classmethod
    def meaningful_mandate(cls, value):
        if len(value.strip()) < 3:
            raise ValueError("Describe the portfolio's mandate.")
        return value.strip()


class PortfolioSave(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    preferences: PortfolioPreferences
    allocation_ids: list[UUID] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("allocation_ids")
    @classmethod
    def distinct_allocations(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Choose distinct account allocations.")
        return sorted(values, key=str)


class PortfolioView(Contract):
    id: str
    name: str
    revision: int
    version_id: str | None
    preferences: PortfolioPreferences | None
    allocation_ids: list[str]
    accounts: list[dict]
    capabilities: dict[str, bool]


class PortfolioReceipt(Contract):
    id: str
    portfolio_id: str
    turn_id: str | None
    action: str
    result: dict
    checksum: str
    created_at: datetime


class PortfolioContext(Contract):
    portfolio: PortfolioView
    allocations: list[dict]
    deployments: list[dict]
    positions: list[dict]
    research_jobs: list[dict]
    receipts: list[PortfolioReceipt]
    as_of: datetime
    daily_research: dict | None = None
    research_digests: list[dict] = Field(default_factory=list)
    strategy_qualification: list[dict] = Field(default_factory=list)
    intelligence_dependency: Literal["tauric"] = "tauric"
    live_execution_enabled: Literal[False] = False
    performance_basis: Literal["attributed_paper_positions"] = "attributed_paper_positions"


class ChatIdentity(Contract):
    channel: Literal["webchat", "telegram", "discord"]
    agent_id: Literal["tauric-chat"]
    session_key: str = Field(min_length=1, max_length=400)
    sender_id: str = Field(default="", max_length=100)
    sender_is_owner: bool = Field(strict=True)
    account_id: str = Field(default="default", max_length=100)
    tool_call_id: str = Field(min_length=1, max_length=200)


class PortfolioChatAction(ChatIdentity):
    action: Literal["preferences", "pause", "resume", "research_settings"]
    expected_revision: int = Field(ge=0, strict=True)
    mandate: str | None = Field(default=None, min_length=3, max_length=2000)
    research_objective: Literal["net_return", "balanced", "consistency"] | None = None
    deployment_id: UUID | None = None
    research_parameters: PortfolioResearchParameters | None = None
    reason: str = Field(min_length=3, max_length=500)


class ChatContextRead(ChatIdentity):
    report_id: UUID | None = None
    job_id: UUID | None = None


class PortfolioResearchInvocation(ChatIdentity):
    reason: str = Field(min_length=3, max_length=500)
