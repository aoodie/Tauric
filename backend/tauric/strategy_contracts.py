"""Immutable owner strategy configuration; native analysis remains a separate graph."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .financial_contracts import Money
from .market_contracts import Interval
from .schemas import Contract


class BreakoutRules(Contract):
    engine: Literal["closed_bar_breakout_v1"]
    interval: Interval
    entry_lookback: int = Field(ge=2, le=100, strict=True)
    minimum_volume_ratio: Money
    exit_lookback: int = Field(ge=2, le=100, strict=True)
    take_profit_r: Money
    signal_ttl_seconds: int = Field(ge=60, le=3600, strict=True)

    @model_validator(mode="after")
    def bounds(self):
        if Decimal(self.minimum_volume_ratio) > 100 or not 1 <= Decimal(self.take_profit_r) <= 100:
            raise ValueError(
                "Volume ratio must be at most 100 and profit target between 1 and 100 R."
            )
        return self


class StrategyParameters(Contract):
    label: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    description: str = Field(min_length=3, max_length=8000, pattern=r".*\S.*")
    quantity_step: Money
    capital_limit: Money
    risk_percent: Money
    rules: BreakoutRules | None = None

    @model_validator(mode="after")
    def limits(self):
        if Decimal(self.risk_percent) > 100 or Decimal(self.quantity_step) >= Decimal("1e12"):
            raise ValueError(
                "Paper strategy risk must be at most 100% with a usable quantity step."
            )
        return self


class StrategyCreate(Contract):
    request_id: UUID
    name: str = Field(min_length=3, max_length=80, pattern=r".*\S.*")
    parameters: StrategyParameters
    reason: str = Field(min_length=3, max_length=500)


class StrategyRevise(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=1, strict=True)
    parameters: StrategyParameters
    reason: str = Field(min_length=3, max_length=500)


class StrategyVersionView(Contract):
    id: str
    strategy_id: str
    name: str
    revision: int
    parameters: StrategyParameters
    reason: str
    checksum: str
    created_at: datetime


class DeploymentControl(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)
    reason: str = Field(min_length=3, max_length=500)


class DeploymentControlView(Contract):
    revision: int
    enabled: bool
    reason: str
    created_at: datetime | None


class StrategyRunRequest(Contract):
    request_id: UUID
    deployment_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class StrategyRunView(Contract):
    id: str
    deployment_id: str
    strategy_version_id: str
    action: Literal["enter", "exit", "hold", "blocked"]
    payload: dict
    checksum: str
    created_at: datetime


class StrategyScheduleChange(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    enabled: bool = Field(strict=True)
    cadence_seconds: int = Field(ge=30, le=3600, strict=True)
    reason: str = Field(min_length=3, max_length=500, pattern=r".*\S.*")


class StrategyScheduleView(Contract):
    deployment_id: str
    version_id: str
    revision: int
    enabled: bool
    cadence_seconds: int
    reason: str
    next_due_at: datetime | None
    last_checked_at: datetime | None
    last_run_id: str | None
    last_action: str | None
    last_error: str | None


class StrategySchedulerStatus(Contract):
    ready: bool
    heartbeat_at: datetime | None


class StrategyScheduleEvidence(Contract):
    id: str
    payload: dict
    checksum: str
    created_at: datetime


class StrategyScheduleHistory(Contract):
    versions: list[StrategyScheduleEvidence]
    ticks: list[StrategyScheduleEvidence]
