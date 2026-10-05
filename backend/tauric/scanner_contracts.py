"""Versioned deterministic screening, research-only candidates and explicit escalation."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .financial_contracts import Money
from .market_contracts import AnalysisView, Interval
from .schemas import Contract

DetectorId = Literal["closed_bar_breakout_v1", "closed_bar_breakdown_v1", "trend_pullback_v1"]


class StrategyScore(Contract):
    minimum_score: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    priority: int = Field(default=0, ge=0, le=100, strict=True)


class CandidateRanking(Contract):
    minimum_score: float = Field(default=0.65, ge=0, le=1, allow_inf_nan=False)
    max_candidates_per_cycle: int = Field(default=2, ge=1, le=100, strict=True)
    max_candidates_per_asset_class: int = Field(default=2, ge=1, le=100, strict=True)
    max_candidates_per_strategy: int = Field(default=2, ge=1, le=100, strict=True)


class PotentialCandidate(Contract):
    candidate_id: str
    created_at: AwareDatetime
    symbol: str
    instrument_id: str
    asset_class: Literal["crypto_spot", "fx", "equity", "etf"]
    strategy_id: str
    strategy_version: str
    direction: Literal["long", "short"]
    timeframe: Interval
    setup_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    features: dict[str, str]
    market_data_timestamp: AwareDatetime
    scanner_id: str
    scanner_version: str
    profile_version_id: str
    dedupe_key: str
    trace_id: str
    strategy_priority: int = Field(default=0, ge=0, le=100)


class ScannerParameters(Contract):
    enabled: bool = Field(strict=True)
    universe: list[str] = Field(min_length=1, max_length=1000)
    interval: Interval
    detector_id: DetectorId
    detector_ids: list[DetectorId] = Field(default_factory=list, max_length=3)
    lookback: int = Field(ge=2, le=100, strict=True)
    minimum_volume_ratio: Money
    candidate_ttl_seconds: int = Field(ge=60, le=86400, strict=True)
    max_analyses_per_day: int = Field(ge=0, le=100, strict=True)
    model_profile: Literal["cloud", "local"]
    # Defaults preserve saved manual profiles; research settings are consumed downstream.
    disabled_instruments: list[str] = Field(default_factory=list, max_length=1000)
    minimum_setup_score: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    strategy_scores: dict[str, StrategyScore] = Field(default_factory=dict, max_length=100)
    ranking: CandidateRanking = Field(default_factory=CandidateRanking)
    cooldown_seconds: int = Field(default=0, ge=0, le=86400, strict=True)
    automatic_enabled: bool = Field(default=False, strict=True)
    automatic_research_enabled: bool = Field(default=False, strict=True)
    scan_seconds: int = Field(default=30, ge=5, le=3600, strict=True)
    watch_scan_seconds: int = Field(default=10, ge=5, le=3600, strict=True)
    evaluate_on_candle_close: bool = Field(default=True, strict=True)
    max_data_age_seconds: int = Field(default=960, ge=1, le=3600, strict=True)
    require_active_session: bool = Field(default=False, strict=True)
    minimum_volume: Money | None = None
    minimum_price: Money | None = None
    maximum_price: Money | None = None
    minimum_volatility_percent: float = Field(default=0, ge=0, allow_inf_nan=False)
    maximum_spread_bps: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("universe")
    @classmethod
    def universe_unique(cls, values):
        if len(set(values)) != len(values) or any(len(v) > 120 for v in values):
            raise ValueError("Choose distinct instruments from the supported registry.")
        return sorted(values)

    @model_validator(mode="after")
    def coherent_filters(self):
        if len(set(self.detector_ids)) != len(self.detector_ids):
            raise ValueError("Select each setup once.")
        if "trend_pullback_v1" in self.detectors and self.lookback < 4:
            raise ValueError("Trend pullbacks require at least four prior closed candles.")
        if len(set(self.disabled_instruments)) != len(self.disabled_instruments) or not set(
            self.disabled_instruments
        ) <= set(self.universe):
            raise ValueError("Disabled instruments must be distinct members of the universe.")
        if self.minimum_price and self.maximum_price:
            from decimal import Decimal

            if Decimal(self.minimum_price) > Decimal(self.maximum_price):
                raise ValueError("Minimum price cannot exceed maximum price.")
        return self

    @property
    def detectors(self):
        return self.detector_ids or [self.detector_id]


class ScannerSave(Contract):
    request_id: UUID
    profile_id: UUID | None = None
    name: str = Field(default="", min_length=3, max_length=80, pattern=r".*\S.*")
    strategy_version_id: UUID | None = None
    expected_revision: int = Field(ge=0, strict=True)
    parameters: ScannerParameters
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def standalone_name(self):
        if not self.strategy_version_id and not self.name:
            raise ValueError("Give the scanner profile a name.")
        return self


class ScannerProfileView(Contract):
    id: str
    profile_id: str
    name: str
    strategy_version_id: str | None
    strategy_name: str | None
    strategy_label: str | None
    revision: int
    parameters: ScannerParameters
    reason: str
    checksum: str
    created_at: datetime
    charged_analyses_today: int


class ScannerRunRequest(Contract):
    request_id: UUID
    profile_version_id: UUID
    trigger: Literal["manual", "scheduled"] = "manual"


class ScannerRunView(Contract):
    id: str
    profile_version_id: str
    created_at: datetime
    results: list[dict]
    checksum: str
    metrics: dict = Field(default_factory=dict)
    potential_candidates: list[PotentialCandidate] = Field(default_factory=list)
    published_candidates: list[PotentialCandidate] = Field(default_factory=list)


class CandidateAction(Contract):
    request_id: UUID
    action: Literal["watch", "ignore"]
    reason: str = Field(min_length=3, max_length=500)


class CandidateEscalate(Contract):
    request_id: UUID
    profile_version_id: UUID
    reason: str = Field(min_length=3, max_length=500)


class CandidateView(Contract):
    id: str
    asset_class: Literal["crypto_spot", "fx", "equity", "etf"]
    instrument_id: str
    interval: Interval
    detector_id: str
    direction: Literal["long", "short"] = "long"
    detected_at: datetime
    expires_at: datetime
    status: Literal["candidate", "watch", "ignored", "expired", "inactive"]
    features: dict[str, str]
    reasons: list[str]
    snapshot_id: str
    checksum: str
    matches: list[dict]
    analysis: AnalysisView | None
    history: list[dict]
    setup_score: float = Field(default=0, ge=0, le=1)
    potential_candidates: list[PotentialCandidate] = Field(default_factory=list)
    execution_authority: Literal[False] = False


class ScannerState(Contract):
    profiles: list[ScannerProfileView]
    candidates: list[CandidateView]
    runs: list[ScannerRunView]
    automatic_research_ready: bool = False
    supported_family: str = "crypto_spot, fx, equity, etf"
    scan_mode: Literal["manual", "scheduled"] = "manual"
    model_calls_during_scan: Literal[0] = 0
