"""Paper-only contracts. Money and quantity cross the boundary as decimal strings."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .autonomy_contracts import AutonomyState
from .capital_contracts import CapitalView
from .financial_contracts import Cost, Money
from .research_contracts import AssetCosts
from .schemas import Contract
from .strategy_contracts import (
    StrategyRunView,
    StrategySchedulerStatus,
    StrategyScheduleView,
    StrategyVersionView,
)


class PaperPolicy(Contract):
    risk_percent: Money
    max_total_risk_percent: Money
    max_exposure_percent: Money
    max_positions: int = Field(ge=1, le=1000, strict=True)
    fee_bps: Cost
    slippage_bps: Cost
    quote_max_age_seconds: int = Field(ge=1, le=60, strict=True)
    decision_ttl_seconds: int = Field(ge=1, le=3600, strict=True)

    @model_validator(mode="after")
    def limits(self):
        if (
            max(
                Decimal(self.risk_percent),
                Decimal(self.max_exposure_percent),
                Decimal(self.max_total_risk_percent),
            )
            > 100
        ):
            raise ValueError("Unlevered paper risk and exposure cannot exceed 100%.")
        if max(Decimal(self.fee_bps), Decimal(self.slippage_bps)) >= 10000:
            raise ValueError("Costs must be below 10,000 basis points.")
        return self


class PaperAccountCreate(Contract):
    request_id: UUID
    name: str = Field(min_length=3, max_length=80)
    currency: Literal["USDT", "USD", "JPY"]
    starting_balance: Money
    policy: PaperPolicy
    reason: str = Field(min_length=3, max_length=500)


class PaperPolicyChange(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    policy: PaperPolicy
    reason: str = Field(min_length=3, max_length=500)


class PaperHalt(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    halted: bool = Field(strict=True)
    reason: str = Field(min_length=3, max_length=500)


class DeploymentCreate(Contract):
    request_id: UUID
    account_id: UUID
    instrument_id: str = Field(max_length=120)
    portfolio: str = Field(min_length=3, max_length=80)
    capital_pot: str = Field(min_length=3, max_length=80)
    capital_scope_id: UUID | None = None
    strategy_revision_id: UUID | None = None
    stock_costs: AssetCosts | None = None
    fx_financing_daily_bps: str | None = None
    fx_triple_rollover_weekday: int = Field(default=2, ge=0, le=4, strict=True)
    strategy: str = Field(min_length=3, max_length=80)
    strategy_version: str = Field(min_length=1, max_length=80, pattern=r".*\S.*")
    quantity_step: Money
    capital_limit: Money
    risk_percent: Money
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def names(self):
        if self.stock_costs and self.stock_costs.asset_type not in {"equity", "etf"}:
            raise ValueError("Stock costs must describe an equity or ETF route.")
        for name in (self.portfolio, self.capital_pot, self.strategy):
            if len(name.strip()) < 3:
                raise ValueError("Enter meaningful portfolio, capital pot and strategy names.")
        if self.fx_financing_daily_bps is not None:
            from .research_contracts import AssetCosts

            AssetCosts(
                asset_type="fx", spread_bps="0", financing_daily_bps=self.fx_financing_daily_bps
            )
        return self


class ProposalCreate(Contract):
    request_id: UUID
    deployment_id: UUID
    stop_price: Money
    native_artifact_id: UUID | None = None
    strategy_run_id: UUID | None = None
    reason: str = Field(min_length=3, max_length=500)


class GovernanceReview(Contract):
    request_id: UUID
    outcome: Literal["allow", "reject", "defer"]
    reason: str = Field(min_length=3, max_length=500)


class PaperAction(Contract):
    request_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class PaperAccountView(Contract):
    id: str
    name: str
    currency: str
    starting_balance: str
    cash: str
    realized_pnl: str
    revision: int
    halted: bool
    policy_revision: int
    policy: PaperPolicy
    reconciled: bool
    created_at: datetime


class DeploymentView(Contract):
    fx_financing_daily_bps: str | None = None
    stock_costs: AssetCosts | None = None
    strategy_revision_id: str | None = None
    enabled: bool = True
    control_revision: int = 0
    capital_scope_id: str | None = None
    id: str
    account_id: str
    instrument_id: str
    portfolio: str
    capital_pot: str
    strategy: str
    strategy_version: str
    quantity_step: str
    capital_limit: str
    risk_percent: str
    mode: Literal["paper"] = "paper"
    created_at: datetime


class TradingRecordView(Contract):
    id: str
    kind: Literal["proposal", "governance", "plan", "fill", "policy", "halt"]
    account_id: str
    deployment_id: str | None
    created_at: datetime
    payload: dict
    checksum: str


class PaperPositionView(Contract):
    id: str
    account_id: str
    deployment_id: str
    instrument_id: str
    portfolio: str
    capital_pot: str
    strategy: str
    strategy_version: str
    quantity: str
    entry_price: str
    entry_fee: str
    stop_reference: str
    opened_at: datetime


class PaperState(Contract):
    mode: Literal["paper"] = "paper"
    live_enabled: Literal[False] = False
    autonomy: AutonomyState
    strategy_versions: list[StrategyVersionView]
    strategy_runs: list[StrategyRunView]
    strategy_schedules: list[StrategyScheduleView]
    strategy_scheduler: StrategySchedulerStatus
    capital_scopes: list[CapitalView]
    accounts: list[PaperAccountView]
    deployments: list[DeploymentView]
    positions: list[PaperPositionView]
    records: list[TradingRecordView]
    openclaw_ready: bool = False
    detail: str
