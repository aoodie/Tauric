from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .financial_contracts import Cost, Money
from .market_contracts import DataContext, DataQuality, Interval, PriceOHLC
from .schemas import Contract
from .strategy_contracts import StrategyVersionView


class ResearchBar(Contract):
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: Money
    high: Money
    low: Money
    close: Money
    volume: Cost
    closed: Literal[True] = True
    bid: PriceOHLC | None = None
    ask: PriceOHLC | None = None

    @model_validator(mode="after")
    def prices(self):
        if (
            not Decimal(self.low)
            <= min(Decimal(self.open), Decimal(self.close))
            <= max(Decimal(self.open), Decimal(self.close))
            <= Decimal(self.high)
        ):
            raise ValueError("Each bar must have consistent open, high, low and close prices.")
        return self


class DatasetCapture(Contract):
    request_id: UUID
    name: str = Field(min_length=3, max_length=120)
    snapshot_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class AssetCosts(Contract):
    asset_type: Literal["fx", "equity", "etf"]
    spread_bps: Cost
    commission_per_unit: Cost = "0"
    minimum_commission: Cost = "0"
    sell_tax_bps: Cost = "0"
    financing_daily_bps: str | None = None
    triple_rollover_weekday: int = Field(default=2, ge=0, le=4, strict=True)

    @field_validator("financing_daily_bps")
    @classmethod
    def rate(cls, value):
        if value is None:
            return value
        from .financial_contracts import nonnegative

        nonnegative(value.removeprefix("-"))
        if abs(Decimal(value)) >= 1000:
            raise ValueError("Daily financing must be below 1,000 basis points")
        return value

    @model_validator(mode="after")
    def scope(self):
        if max(Decimal(self.spread_bps), Decimal(self.sell_tax_bps)) >= 10000:
            raise ValueError("Asset costs must be below 10,000 basis points")
        if self.asset_type == "fx" and self.financing_daily_bps is None:
            raise ValueError("Enter an explicit FX daily financing assumption, including zero")
        if self.asset_type != "fx" and self.financing_daily_bps is not None:
            raise ValueError("FX rollover financing does not apply to stocks")
        return self


class DatasetImport(Contract):
    request_id: UUID
    name: str = Field(min_length=3, max_length=120)
    instrument_id: str = Field(max_length=120)
    interval: Interval
    source: str = Field(min_length=3, max_length=500)
    candles: list[ResearchBar] = Field(min_length=3, max_length=5000)
    reason: str = Field(min_length=3, max_length=500)
    data_context: DataContext | None = None


class BrokerProtectionReplay(Contract):
    price_step: Money
    entry_delay_seconds: int = Field(default=0, ge=0, le=86400, strict=True)


class ReplayParameters(Contract):
    engine: Literal["sma_cross_spot_v1", "closed_bar_breakout_v1"] = "sma_cross_spot_v1"
    strategy_version_id: UUID | None = None
    fast_window: int = Field(default=5, ge=1, le=200, strict=True)
    slow_window: int = Field(default=20, ge=2, le=500, strict=True)
    starting_balance: Money = "10000"
    allocation_percent: Money = "50"
    risk_percent: Money = "1"
    stop_percent: Money = "5"
    quantity_step: Money = "0.00001"
    fee_bps: Cost = "10"
    slippage_bps: Cost = "5"
    asset_costs: AssetCosts | None = None
    broker_protection: BrokerProtectionReplay | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def limits(self):
        if (self.engine == "closed_bar_breakout_v1") != (self.strategy_version_id is not None):
            raise ValueError("Select a saved strategy version only for the breakout replay.")
        if self.fast_window >= self.slow_window:
            raise ValueError("The fast window must be shorter than the slow window.")
        if max(Decimal(self.allocation_percent), Decimal(self.risk_percent)) > 100:
            raise ValueError("Allocation and risk cannot exceed 100%.")
        if Decimal(self.stop_percent) >= 100:
            raise ValueError("The stop distance must be below 100%.")
        if max(Decimal(self.fee_bps), Decimal(self.slippage_bps)) >= 10000:
            raise ValueError("Costs must be below 10,000 basis points.")
        if self.broker_protection and (
            self.engine != "closed_bar_breakout_v1" or not self.asset_costs
        ):
            raise ValueError("Broker protection replay requires saved breakout rules and asset costs.")
        return self


class ReplayCreate(Contract):
    request_id: UUID
    dataset_id: UUID
    name: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=3, max_length=2000, pattern=r".*\S.*")
    parameters: ReplayParameters
    reason: str = Field(min_length=3, max_length=500)


class OutcomeMetrics(Contract):
    closed_trades: int
    wins: int
    losses: int
    breakeven: int
    net_pnl: str
    total_fees: str
    win_rate_percent: str | None
    average_trade_pnl: str | None
    profit_factor: str | None
    average_holding_seconds: str | None


class EquityPoint(Contract):
    time: datetime
    equity: str
    drawdown_percent: str


class ReplayTiming(Contract):
    observations: int
    median_seconds: str | None
    p95_seconds: str | None
    maximum_seconds: str | None


class ReplayExecutionEvidence(Contract):
    model: Literal["broker_protected_ohlc_long_v1"]
    entry_delay_seconds: int
    signal_to_fill: ReplayTiming
    expired_entries: int
    unfilled_entries: int
    same_bar_exits: int
    ambiguous_exits: list[dict]
    partial_fills_modeled: Literal[False] = False
    intrabar_path_observed: Literal[False] = False
    broker_parity_verified: Literal[False] = False


class ReplayResult(Contract):
    metrics: OutcomeMetrics
    final_equity: str
    return_percent: str
    max_drawdown_percent: str
    max_drawdown_amount: str
    equity: list[EquityPoint]
    trades: list[dict]
    fills: list[dict]
    signals: list[dict]
    skipped_entries: int
    bars: int
    financing_cost: str = "0"
    financing_events: list[dict] = Field(default_factory=list)
    execution_evidence: ReplayExecutionEvidence | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class DatasetView(Contract):
    id: str
    name: str
    instrument_id: str
    interval: str
    source: str
    availability: Literal["retrospective"] = "retrospective"
    bar_count: int
    start_time: datetime
    end_time: datetime
    checksum: str
    created_at: datetime
    asset_type: Literal["crypto_spot", "fx", "equity", "etf"] = "crypto_spot"
    quote_currency: str = "USDT"
    data_context: DataContext | None = None
    quality: DataQuality | None = None


class ReplayView(Contract):
    id: str
    name: str
    dataset_id: str
    dataset_checksum: str
    hypothesis: str
    parameters: ReplayParameters
    strategy_version: StrategyVersionView | None = None
    engine_version: str
    engine_checksum: str
    result: ReplayResult
    assumptions: list[str]
    checksum: str
    created_at: datetime
    asset_type: Literal["crypto_spot", "fx", "equity", "etf"] = "crypto_spot"
    quote_currency: str = "USDT"


class ResearchState(Contract):
    datasets: list[DatasetView]
    runs: list[ReplayView]


class StrategyAnalytics(Contract):
    deployment_id: str
    strategy: str
    strategy_version: str
    instrument_id: str
    open_positions: int
    metrics: OutcomeMetrics


class AccountAnalytics(Contract):
    account_id: str
    name: str
    currency: str
    starting_balance: str
    cash: str
    realized_balance: str
    realized_return_percent: str
    realized_max_drawdown_percent: str
    realized_max_drawdown_amount: str
    open_positions: int
    ledger_digest: str
    metrics: OutcomeMetrics
    strategies: list[StrategyAnalytics]
    equity: list[EquityPoint]
    trades: list[dict]


class AnalyticsView(Contract):
    basis: Literal["closed_paper_trades"] = "closed_paper_trades"
    accounts: list[AccountAnalytics]
    generated_at: datetime
    detail: str
