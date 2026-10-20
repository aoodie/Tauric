"""Research controls and fixed strategy definitions; never order permissions."""

from datetime import timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .financial_contracts import Cost, Money
from .market_contracts import Interval
from .schemas import Contract

Family = Literal["trend", "breakout", "mean_reversion"]


class ResearchComponent(Contract):
    family: Family
    fast_window: int = Field(default=5, ge=2, le=100, strict=True)
    slow_window: int = Field(default=20, ge=5, le=200, strict=True)
    entry_z: float = Field(default=2, ge=0.5, le=4, allow_inf_nan=False)
    activity_ratio: Money = "1"
    weight: Money = "1"

    @model_validator(mode="after")
    def bounds(self):
        if self.fast_window >= self.slow_window or Decimal(self.weight) > 1:
            raise ValueError("Use an ordered fast/slow window and weights no larger than one")
        return self


class ResearchStrategy(Contract):
    mode: Literal["single", "blend", "regime_switch"] = "single"
    components: list[ResearchComponent] = Field(min_length=1, max_length=3)
    context_interval: Interval | None = None
    context_window: int = Field(default=20, ge=5, le=100, strict=True)

    @model_validator(mode="after")
    def mixture(self):
        if self.mode == "single" and len(self.components) != 1:
            raise ValueError("A single strategy has exactly one component")
        if self.mode != "single" and len(self.components) < 2:
            raise ValueError("A mixture needs at least two components")
        if sum(Decimal(c.weight) for c in self.components) != 1:
            raise ValueError("Component weights must sum to one")
        if self.mode == "regime_switch" and not self.context_interval:
            raise ValueError("Market-condition switching requires a higher timeframe")
        if self.mode == "regime_switch" and (
            len(self.components) != 2
            or sum(c.family == "mean_reversion" for c in self.components) != 1
        ):
            raise ValueError(
                "Switching selects one trend/breakout component or one mean-reversion component"
            )
        return self


class HistoryLoad(Contract):
    request_id: UUID
    instrument_id: str = Field(min_length=1, max_length=120)
    interval: Interval
    start: AwareDatetime
    end: AwareDatetime
    reason: str = Field(default="Save reusable research history", min_length=3, max_length=500)

    @model_validator(mode="after")
    def bounded(self):
        from .market import INTERVAL_SECONDS

        if not self.start < self.end or self.end - self.start > timedelta(days=1500):
            raise ValueError("Choose an ordered history range of at most 1,500 days")
        if (self.end - self.start).total_seconds() / INTERVAL_SECONDS[self.interval] > 100000:
            raise ValueError("Load at most 100,000 calendar intervals per request")
        return self


class DiscoveryParameters(Contract):
    enabled: bool = Field(default=False, strict=True)
    instrument_ids: list[str] = Field(
        default_factory=lambda: ["oanda:fx:EUR_USD"], min_length=1, max_length=5
    )
    intervals: list[Interval] = Field(
        default_factory=lambda: ["1h", "4h"], min_length=1, max_length=4
    )
    families: list[Family] = Field(
        default_factory=lambda: ["trend", "breakout", "mean_reversion"], min_length=1, max_length=3
    )
    multi_timeframe: bool = Field(default=True, strict=True)
    mixtures: bool = Field(default=True, strict=True)
    qlib: bool = Field(default=True, strict=True)
    walk_forward: bool = Field(default=True, strict=True)
    cadence_hours: int = Field(default=24, ge=1, le=168, strict=True)
    daily_hypothesis_limit: int = Field(default=2, ge=1, le=20, strict=True)
    daily_experiment_limit: int = Field(default=6, ge=1, le=40, strict=True)
    lookback_days: int = Field(default=180, ge=30, le=1500, strict=True)
    slippage_bps: Cost = "1"
    stressed_slippage_bps: Cost = "3"
    financing_daily_bps: str = Field(default="1", pattern=r"^-?\d+(?:\.\d+)?$")
    fee_bps: Cost = "0"
    minimum_closed_trades: int = Field(default=20, ge=1, le=1000, strict=True)
    maximum_drawdown_percent: Money = "10"
    minimum_return_percent: str = Field(default="0", pattern=r"^-?\d+(?:\.\d+)?$")
    ai_enabled: bool = Field(default=False, strict=True)
    daily_ai_budget_usd: Cost = "0"
    ai_profile: Literal["cloud", "local"] = "cloud"
    ai_input_usd_per_million: Cost = "0"
    ai_output_usd_per_million: Cost = "0"
    ai_max_output_tokens: int = Field(default=1500, ge=500, le=4000, strict=True)

    @model_validator(mode="after")
    def safe_limits(self):
        for values in (self.instrument_ids, self.intervals, self.families):
            if len(values) != len(set(values)):
                raise ValueError("Research selections must be distinct")
        if Decimal(self.stressed_slippage_bps) < Decimal(self.slippage_bps):
            raise ValueError("Stressed slippage must be at least baseline slippage")
        if (
            abs(Decimal(self.financing_daily_bps)) >= 1000
            or Decimal(self.maximum_drawdown_percent) > 100
        ):
            raise ValueError("Use bounded research financing and drawdown assumptions")
        if (
            max(
                Decimal(self.slippage_bps),
                Decimal(self.stressed_slippage_bps),
                Decimal(self.fee_bps),
            )
            >= 10000
        ):
            raise ValueError("Research costs must be below 10,000 basis points")
        if self.ai_enabled and (
            Decimal(self.daily_ai_budget_usd) <= 0
            or min(Decimal(self.ai_input_usd_per_million), Decimal(self.ai_output_usd_per_million))
            <= 0
        ):
            raise ValueError(
                "Save a positive daily AI ceiling and model token prices before enabling paid ideas"
            )
        return self


class DiscoverySettingsSave(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    parameters: DiscoveryParameters
    reason: str = Field(default="Update strategy research settings", min_length=3, max_length=500)


class DiscoveryStart(Contract):
    request_id: UUID
    reason: str = Field(default="Explore strategies using saved data", min_length=3, max_length=500)


class DiscoveryRecordView(Contract):
    id: str
    kind: str
    parent_id: str | None
    checksum: str
    created_at: AwareDatetime
    payload: dict
