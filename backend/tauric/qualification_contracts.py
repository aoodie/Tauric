"""Frozen experiment protocols; model opinion cannot create trading authority."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .financial_contracts import Cost, Money
from .research_contracts import ReplayParameters
from .schemas import Contract


class QualificationThresholds(Contract):
    minimum_holdout_bars: int = Field(ge=5, le=5000, strict=True)
    minimum_closed_trades: int = Field(ge=1, le=10000, strict=True)
    minimum_net_return_percent: Cost
    maximum_drawdown_percent: Money

    @model_validator(mode="after")
    def bounded(self):
        if (
            Decimal(self.maximum_drawdown_percent) > 100
            or Decimal(self.minimum_net_return_percent) > 10000
        ):
            raise ValueError("Use drawdown at most 100% and return at most 10,000%.")
        return self


class CostStress(Contract):
    name: str = Field(min_length=3, max_length=80)
    fee_bps: Cost
    slippage_bps: Cost

    @model_validator(mode="after")
    def bounded(self):
        if max(Decimal(self.fee_bps), Decimal(self.slippage_bps)) >= 10000:
            raise ValueError("Stress costs must be below 10,000 basis points.")
        return self


class QualificationFreeze(Contract):
    request_id: UUID
    portfolio_id: UUID
    strategy_version_id: UUID
    dataset_id: UUID
    name: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=3, max_length=2000)
    training_bars: int = Field(ge=5, le=4995, strict=True)
    embargo_bars: int = Field(ge=0, le=500, strict=True)
    parameters: ReplayParameters
    thresholds: QualificationThresholds
    stresses: list[CostStress] = Field(min_length=1, max_length=6)
    intended_mode: Literal["research_comparison", "paper_closed_bar", "live_scalping"]
    assessment_validity_hours: int = Field(ge=1, le=168, strict=True)
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def exact(self):
        if (
            self.parameters.engine != "closed_bar_breakout_v1"
            or self.parameters.strategy_version_id != self.strategy_version_id
        ):
            raise ValueError("Qualification requires the exact saved executable strategy version.")
        if len({v.name for v in self.stresses}) != len(self.stresses):
            raise ValueError("Give each cost scenario a distinct name.")
        if any(
            Decimal(s.fee_bps) < Decimal(self.parameters.fee_bps)
            or Decimal(s.slippage_bps) < Decimal(self.parameters.slippage_bps)
            for s in self.stresses
        ):
            raise ValueError("Stress scenarios may not reduce either baseline cost.")
        if not any(
            Decimal(s.fee_bps) > Decimal(self.parameters.fee_bps)
            or Decimal(s.slippage_bps) > Decimal(self.parameters.slippage_bps)
            for s in self.stresses
        ):
            raise ValueError("At least one stress scenario must increase a baseline cost.")
        return self


class QualificationAction(Contract):
    request_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class QualificationAssessmentRequest(QualificationAction):
    profile: Literal["cloud", "local"]


class NativeStrategyVerdict(Contract):
    strategy_version_id: str = Field(min_length=1, max_length=36)
    evidence_checksum: str = Field(min_length=1, max_length=64)
    intended_mode: Literal["research_comparison", "paper_closed_bar", "live_scalping"]
    decision: Literal["suitable", "unsuitable", "incomplete"]
    rationale: str = Field(min_length=3, max_length=4000)
    limitations: list[Annotated[str, Field(min_length=1, max_length=2000)]] = Field(
        min_length=1, max_length=20
    )


class QualificationView(Contract):
    id: str
    portfolio_id: str
    strategy_version_id: str
    dataset_id: str
    name: str
    checksum: str
    created_at: datetime
    frozen: dict
    evaluation: dict | None
    assessment_jobs: list[dict]
    reports: list[dict]
    execution_authority: Literal[False] = False


class QualificationState(Contract):
    protocols: list[QualificationView]
    native_assessment_ready: bool
    scope: str
