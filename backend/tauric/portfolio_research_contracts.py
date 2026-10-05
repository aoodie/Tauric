"""Research-only schedules, asset scope and bounded experiment plans."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from .research_contracts import ReplayParameters
from .schemas import Contract

AssetClass = Literal["crypto_spot", "fx", "equity", "etf", "crypto_futures", "exchange_futures"]


class ExperimentPlan(Contract):
    dataset_id: UUID
    hypothesis: str = Field(min_length=3, max_length=2000, pattern=r".*\S.*")
    parameters: ReplayParameters
    explore_sma_neighbours: bool = Field(default=False, strict=True)


class PortfolioResearchParameters(Contract):
    enabled: bool = Field(default=False, strict=True)
    daily_time: str = Field(default="08:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = "Europe/London"
    additional_cadence_hours: int | None = Field(default=None, ge=1, le=168, strict=True)
    event_keys: list[str] = Field(default_factory=list, max_length=10)
    asset_classes: list[AssetClass] = Field(
        default_factory=lambda: ["crypto_spot"], min_length=1, max_length=6
    )
    universe: list[str] = Field(default_factory=list, max_length=10)
    opportunities: bool = Field(default=True, strict=True)
    strategies: bool = Field(default=True, strict=True)
    scanner_profile_ids: list[UUID] = Field(default_factory=list, max_length=5)
    experiments: list[ExperimentPlan] = Field(default_factory=list, max_length=5)
    max_experiments_per_run: int = Field(default=3, ge=0, le=15, strict=True)
    max_native_jobs_per_day: int = Field(default=0, ge=0, le=20, strict=True)
    shortlist_size: int = Field(default=3, ge=0, le=10, strict=True)
    native_wait_minutes: int = Field(default=90, ge=5, le=360, strict=True)
    model_profile: Literal["cloud", "local"] = "local"

    @field_validator("timezone")
    @classmethod
    def valid_zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose an IANA timezone, such as Europe/London.") from None
        return value

    @model_validator(mode="after")
    def coherent(self):
        for values in (
            self.asset_classes,
            self.universe,
            self.event_keys,
            self.scanner_profile_ids,
        ):
            if len(set(values)) != len(values):
                raise ValueError("Research selections must be distinct.")
        if any(not v.strip() or len(v) > 120 for v in self.universe):
            raise ValueError("Use valid registered instrument IDs.")
        if any(not v.strip() or len(v) > 80 for v in self.event_keys):
            raise ValueError("Use distinct event names of 1–80 characters.")
        if not self.opportunities and not self.strategies:
            raise ValueError("Enable at least one research track.")
        return self


class PortfolioResearchSave(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    parameters: PortfolioResearchParameters
    reason: str = Field(min_length=3, max_length=500, pattern=r".*\S.*")


class PortfolioResearchRequest(Contract):
    request_id: UUID
    reason: str = Field(min_length=3, max_length=500, pattern=r".*\S.*")
    event_key: str | None = Field(default=None, min_length=1, max_length=80)
    evidence: str = Field(default="", max_length=2000)


class SharedAdmissionBudgetSave(Contract):
    request_id: UUID
    expected_revision: int = Field(ge=0, strict=True)
    daily_job_limit: int = Field(ge=0, le=200, strict=True)
    reason: str = Field(min_length=3, max_length=500)


class SharedAdmissionBudgetView(Contract):
    revision: int
    daily_job_limit: int | None
    used_today: int
    day_basis: Literal["UTC"] = "UTC"
    monetary_cap: Literal[False] = False


class PortfolioResearchRunView(Contract):
    id: str
    portfolio_id: str
    policy_version_id: str
    trigger: Literal["daily", "cadence", "event", "manual", "chat"]
    status: Literal["queued", "running", "waiting_native", "succeeded", "partial", "cancelled"]
    due_at: datetime
    created_at: datetime
    finished_at: datetime | None
    steps: list[dict]
    native_jobs: list[dict]
    digest: dict | None
    execution_authority: Literal[False] = False


class PortfolioResearchState(Contract):
    portfolio_id: str
    revision: int
    version_id: str | None
    parameters: PortfolioResearchParameters | None
    next_daily_due: datetime | None
    next_cadence_due: datetime | None
    worker_ready: bool
    worker_seen_at: datetime | None
    asset_capabilities: list[dict]
    shared_budget: SharedAdmissionBudgetView
    runs: list[PortfolioResearchRunView]
