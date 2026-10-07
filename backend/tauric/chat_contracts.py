from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .assistant_contracts import AssistantReceipt
from .market_contracts import AnalysisView
from .portfolio_contracts import PortfolioReceipt
from .schemas import Contract


class ChannelConfig(Contract):
    enabled: bool = False
    owner_id: str = Field(default="", max_length=25)
    token: str = Field(default="", max_length=500)

    @field_validator("owner_id")
    @classmethod
    def numeric_owner(cls, value):
        if value and (
            not value.isascii()
            or not value.isdigit()
            or int(value) <= 0
            or str(int(value)) != value
        ):
            raise ValueError("Use your numeric user ID")
        return value


class ChatConfig(Contract):
    profile: Literal["cloud", "local"] = "local"
    daily_research_allowance: int = Field(default=3, ge=0, le=50, strict=True)
    telegram: ChannelConfig = Field(default_factory=ChannelConfig)
    discord: ChannelConfig = Field(default_factory=ChannelConfig)


class ChannelChange(Contract):
    enabled: bool
    owner_id: str = Field(max_length=25)
    token_action: Literal["keep", "replace", "clear"] = "keep"
    token: str = Field(default="", max_length=500)


class ChatConfigChange(Contract):
    expected_revision: int = Field(ge=0)
    profile: Literal["cloud", "local"]
    daily_research_allowance: int = Field(default=3, ge=0, le=50, strict=True)
    telegram: ChannelChange
    discord: ChannelChange


class ChannelView(Contract):
    enabled: bool
    owner_id: str
    has_token: bool
    detail: str


class ChatConfigView(Contract):
    revision: int
    profile: Literal["cloud", "local"]
    daily_research_allowance: int
    research_used_today: int
    telegram: ChannelView
    discord: ChannelView
    gateway_ready: bool
    worker_ready: bool
    route_ready: bool
    storage_ready: bool


class ChatSend(Contract):
    request_id: UUID
    conversation_id: UUID
    portfolio_id: UUID | None = None
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Enter a message")
        return value


class ChatTurnView(Contract):
    id: str
    conversation_id: str
    portfolio_id: str | None = None
    user_text: str
    assistant_text: str | None
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    usage: dict[str, int]
    research_jobs: list[AnalysisView] = Field(default_factory=list)
    action_receipts: list[PortfolioReceipt] = Field(default_factory=list)
    project_receipts: list[AssistantReceipt] = Field(default_factory=list)


class ResearchInvocation(Contract):
    instrument_id: str = Field(min_length=3, max_length=100)
    channel: Literal["webchat", "telegram", "discord"]
    agent_id: Literal["tauric-chat"]
    session_key: str = Field(min_length=1, max_length=400)
    sender_id: str = Field(default="", max_length=100)
    sender_is_owner: bool = Field(strict=True)
    account_id: str = Field(default="default", max_length=100)
    tool_call_id: str = Field(min_length=1, max_length=200)


class ConversationView(Contract):
    id: str
    portfolio_id: str | None = None
    title: str
    updated_at: datetime
