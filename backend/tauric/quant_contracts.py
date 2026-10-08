"""Bounded research requests; no order or deployment permissions."""

from datetime import time, timedelta
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, Field, field_validator, model_validator

from .financial_contracts import Cost, Money
from .market_contracts import Interval
from .research_contracts import AssetCosts
from .schemas import Contract


class HistoricalDatasetCreate(Contract):
    request_id: UUID
    name: str = Field(min_length=3, max_length=120)
    instrument_id: str = Field(min_length=1, max_length=120)
    interval: Interval
    start: AwareDatetime
    end: AwareDatetime
    reason: str = Field(min_length=3, max_length=500)

    @model_validator(mode="after")
    def range(self):
        if self.start >= self.end or self.end - self.start > timedelta(days=1500):
            raise ValueError("Select a historical range of at most 1,500 days")
        return self


class SessionWindow(Contract):
    name: str = Field(default="All trading hours", min_length=1, max_length=80)
    timezone: str = "UTC"
    opening: str = "00:00"
    closing: str = "00:00"
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid IANA time zone") from None
        return value

    @field_validator("opening", "closing")
    @classmethod
    def clock(cls, value):
        if len(value) != 5 or time.fromisoformat(value).isoformat(timespec="minutes") != value:
            raise ValueError("Use a 24-hour HH:MM session time")
        return value

    @field_validator("weekdays")
    @classmethod
    def days(cls, values):
        if any(type(v) is not int or not 0 <= v <= 6 for v in values) or len(set(values)) != len(
            values
        ):
            raise ValueError("Use distinct weekdays from Monday=0 to Sunday=6")
        return values


class EvidenceNote(Contract):
    title: str = Field(min_length=3, max_length=180)
    source: str = Field(min_length=3, max_length=500)
    content: str = Field(min_length=3, max_length=4000)
    published_at: AwareDatetime
    available_at: AwareDatetime

    @model_validator(mode="after")
    def available(self):
        if self.available_at < self.published_at:
            raise ValueError("Evidence cannot be available before publication")
        return self


class LabRequest(Contract):
    request_id: UUID
    dataset_id: UUID
    name: str = Field(min_length=3, max_length=120)
    hypothesis: str = Field(min_length=3, max_length=2000)
    reason: str = Field(min_length=3, max_length=500)


class SessionReportCreate(LabRequest):
    mode: Literal["historical", "current", "forecast"] = "current"
    start: AwareDatetime
    as_of: AwareDatetime
    session: SessionWindow = Field(default_factory=SessionWindow)
    forecast_bars: int = Field(default=4, ge=1, le=100, strict=True)
    evidence: list[EvidenceNote] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def range(self):
        if self.start >= self.as_of or self.as_of - self.start > timedelta(days=1500):
            raise ValueError("Choose an ordered research range of at most 1,500 days")
        if any(note.available_at > self.as_of for note in self.evidence):
            raise ValueError("Evidence published or available after the as-of cutoff is excluded")
        return self


class ExperimentCreate(LabRequest):
    engine: Literal["lean", "qlib"]
    session: SessionWindow = Field(default_factory=SessionWindow)
    train_end: AwareDatetime
    validation_end: AwareDatetime
    test_end: AwareDatetime
    horizon_bars: int = Field(default=4, ge=1, le=100, strict=True)
    lookback_bars: int = Field(default=12, ge=3, le=200, strict=True)
    fast_window: int = Field(default=5, ge=1, le=100, strict=True)
    slow_window: int = Field(default=20, ge=2, le=300, strict=True)
    starting_balance: Money = "10000"
    allocation_percent: Money = "25"
    fee_bps: Cost = "0"
    slippage_bps: Cost = "5"
    asset_costs: AssetCosts
    model_run_id: UUID | None = None

    @model_validator(mode="after")
    def bounds(self):
        from decimal import Decimal

        if not self.train_end < self.validation_end < self.test_end:
            raise ValueError("Training, validation and unseen test cutoffs must be chronological")
        if self.fast_window >= self.slow_window:
            raise ValueError("The fast average must be shorter than the slow average")
        if Decimal(self.allocation_percent) > 100:
            raise ValueError("Allocation cannot exceed 100%")
        if max(Decimal(self.fee_bps), Decimal(self.slippage_bps)) >= 10000:
            raise ValueError("Execution costs must be below 10,000 basis points")
        if self.engine == "qlib" and self.model_run_id:
            raise ValueError("A training request cannot reference another model")
        return self


class LabRecordView(Contract):
    id: str
    kind: str
    name: str
    checksum: str
    created_at: AwareDatetime
    payload: dict


class LabState(Contract):
    engines: list[dict]
    sessions: list[SessionWindow]
    records: list[LabRecordView]
    authority: Literal["research_only"] = "research_only"
