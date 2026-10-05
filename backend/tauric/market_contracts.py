from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, computed_field, field_validator, model_validator

from . import native_scope
from .schemas import Contract

Interval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]


class InstrumentView(Contract):
    id: str
    symbol: str
    name: str
    provider: str
    provider_symbol: str
    asset_type: Literal["crypto_spot", "fx", "equity", "etf"]
    base_currency: str
    quote_currency: str
    volume_type: Literal["exchange_base", "provider_ticks", "feed_shares"]
    native_symbol: str
    native_asset_type: Literal["crypto", "forex", "stock"]
    analysis_scope: str
    calendar_id: Literal["crypto_24x7", "oanda_fx", "us_equity"] = "crypto_24x7"
    price_side: Literal["trades", "midpoint", "bid_ask"] = "trades"
    supported_intervals: list[Interval] = ["1m", "5m", "15m", "1h", "4h", "1d"]

    @field_validator("name")
    @classmethod
    def meaningful_text(cls, value):
        # Provider names can legitimately be one or two characters.
        if not value.strip():
            raise ValueError("Instrument name must not be blank")
        return value.strip()

    @computed_field
    @property
    def native_research_ready(self) -> bool:
        return native_scope.supported(self)

    @model_validator(mode="after")
    def current_scope(self):
        # Registry definitions remain immutable; expose today's integration scope.
        self.analysis_scope = native_scope.daily_scope(self.asset_type)
        if self.asset_type == "fx" and not self.native_research_ready:
            self.analysis_scope = "Forex charts, deterministic screening and retrospective replay. Daily AI research is unavailable until both currencies have supported policy/economic adapters."
        return self


class PriceOHLC(Contract):
    open: str
    high: str
    low: str
    close: str

    @model_validator(mode="after")
    def prices(self):
        try:
            values = [Decimal(getattr(self, key)) for key in ("open", "high", "low", "close")]
        except InvalidOperation:
            raise ValueError("Invalid decimal prices") from None
        if any(not v.is_finite() or v <= 0 or v > Decimal("1e30") for v in values):
            raise ValueError("Invalid OHLC prices")
        op, hi, lo, close = values
        if not lo <= min(op, close) <= max(op, close) <= hi:
            raise ValueError("Inconsistent OHLC prices")
        return self


class MarketSession(Contract):
    open: AwareDatetime
    close: AwareDatetime

    @model_validator(mode="after")
    def order(self):
        if self.close <= self.open:
            raise ValueError("Session close must follow its open")
        return self


class DataContext(Contract):
    calendar_id: Literal["crypto_24x7", "oanda_fx", "us_equity"]
    timezone: Literal["UTC", "America/New_York"]
    price_side: Literal["trades", "midpoint", "bid_ask"]
    volume_type: Literal["exchange_base", "provider_ticks", "feed_shares"]
    adjustment: Literal["none", "raw", "split"] = "none"
    feed: str = Field(default="", max_length=80)
    calendar_source: str = Field(default="", max_length=500)
    coverage_start: AwareDatetime | None = None
    coverage_end: AwareDatetime | None = None
    sessions: list[MarketSession] = Field(default_factory=list, max_length=2000)
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def coverage(self):
        if self.calendar_id == "us_equity":
            if (
                not self.coverage_start
                or not self.coverage_end
                or self.coverage_end <= self.coverage_start
            ):
                raise ValueError("US stock data requires bounded calendar coverage")
            previous = None
            for session in self.sessions:
                if (
                    session.open < self.coverage_start
                    or session.close > self.coverage_end
                    or (previous and session.open < previous)
                ):
                    raise ValueError(
                        "Calendar sessions must be ordered, non-overlapping and within coverage"
                    )
                previous = session.close
        return self


class DataQuality(Contract):
    status: Literal["verified", "gaps", "outdated"] = "verified"
    missing_bars: int = 0
    warnings: list[str] = Field(default_factory=list)


class Candle(Contract):
    open_time: AwareDatetime
    close_time: AwareDatetime
    open: str
    high: str
    low: str
    close: str
    volume: str
    closed: bool
    bid: PriceOHLC | None = None
    ask: PriceOHLC | None = None

    @field_validator("open", "high", "low", "close", "volume")
    @classmethod
    def financial_string(cls, value):
        try:
            number = Decimal(value)
            if not number.is_finite() or number < 0 or number > Decimal("1e30"):
                raise ValueError("Invalid market value")
        except InvalidOperation:
            raise ValueError("Invalid decimal string") from None
        return value


class MarketRefresh(Contract):
    interval: Interval = "15m"


class MarketView(Contract):
    instrument: InstrumentView
    interval: Interval
    snapshot_id: str | None
    candles: list[Candle]
    state: Literal["empty", "fresh", "stale"]
    received_at: datetime | None
    provider_time: datetime | None
    source: str
    checksum: str | None
    gaps: int = 0
    availability: Literal["observed_at_receipt"] = "observed_at_receipt"
    data_context: DataContext | None = None
    quality: DataQuality | None = None
    session_state: Literal["open", "closed", "unknown"] = "unknown"


class DataProviderView(Contract):
    id: str
    label: str
    configured: bool
    implemented: bool = True
    connected_verified: bool = False
    detail: str


class ProfileView(Contract):
    id: Literal["cloud", "local"]
    label: str
    configured: bool
    provider: str | None
    quick_model: str | None
    deep_model: str | None
    detail: str


class AnalysisStatus(Contract):
    profiles: list[ProfileView]
    worker_ready: bool
    worker_seen_at: datetime | None
    runtime_version: str | None
    supported_scope: str


class AnalysisRequest(Contract):
    request_id: UUID
    instrument_id: str = Field(max_length=120)
    profile: Literal["cloud", "local"]
    snapshot_id: str | None = None


class AnalysisView(Contract):
    id: str
    instrument_id: str
    profile: str
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    request: dict
    progress: list[str]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    artifact_id: str | None


class AnalysisArtifactView(Contract):
    id: str
    job_id: str
    payload: dict
    checksum: str
    created_at: datetime
