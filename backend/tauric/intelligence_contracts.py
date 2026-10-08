from datetime import datetime
from typing import Literal

from pydantic import Field, computed_field

from .market_contracts import InstrumentView
from .scanner_contracts import CandidateView
from .schemas import Contract

IntelligenceMarket = Literal["overview", "fx", "stocks", "crypto", "commodities", "rates"]


class IntelligenceWatchlistView(Contract):
    revision: int = 0
    instrument_ids: list[str] = Field(default_factory=list)


class IntelligenceWatchlistChange(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    instrument_ids: list[str] = Field(max_length=200)


class IntelligenceRefresh(Contract):
    instrument_ids: list[str] = Field(default_factory=list, max_length=24)
    sources: bool = True


class IntelligenceRegister(Contract):
    instrument_id: str = Field(max_length=120)
    asset_type: Literal["fx", "equity", "etf", "crypto_spot"]


class IntelligenceSymbol(InstrumentView):
    asset_type: Literal["crypto_spot", "fx", "equity", "etf", "us_listing"]
    registered: bool = True
    catalog_id: str | None = None

    @computed_field
    @property
    def native_research_ready(self) -> bool:
        from .native_scope import supported

        return self.registered and supported(self)


class IntelligenceCatalog(Contract):
    market: Literal["fx", "stocks", "crypto"]
    label: str
    total: int
    status: Literal["available", "stale", "unavailable", "missing"]
    checked_at: datetime | None = None
    warning: str | None = None


class IntelligenceMove(Contract):
    instrument: IntelligenceSymbol
    price: str | None = None
    changes: dict[str, str | None] = Field(default_factory=dict)
    sparkline: list[str] = Field(default_factory=list)
    observed_at: datetime | None = None
    received_at: datetime | None = None
    state: str
    warning: str | None = None
    snapshot_id: str | None = None
    price_receipt_id: str | None = None
    daily_bars: list[dict] = Field(default_factory=list)
    source: str
    volume_type: str
    activity_ratio: str | None = None


class IntelligenceSource(Contract):
    key: str
    label: str
    url: str
    status: Literal["available", "stale", "unavailable"]
    checked_at: datetime
    receipt_id: str
    currencies: list[str] = Field(default_factory=list)
    instrument_ids: list[str] = Field(default_factory=list)
    warning: str | None = None
    truncated: bool = False


class IntelligenceItem(Contract):
    id: str
    kind: Literal["headline", "policy_meeting"]
    title: str
    url: str
    source: str
    receipt_id: str
    currencies: list[str] = Field(default_factory=list)
    instrument_ids: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    updated_at: datetime | None = None
    event_date: str | None = None
    event_timezone: str | None = None
    date_only: bool = False
    summary: str = ""
    evidence_type: Literal["reported", "official"]
    stale: bool = False


class IntelligenceDashboard(Contract):
    market: IntelligenceMarket
    generated_at: datetime
    total: int
    offset: int
    limit: int
    moves: list[IntelligenceMove]
    sources: list[IntelligenceSource]
    items: list[IntelligenceItem]
    candidates: list[CandidateView]
    watchlist: IntelligenceWatchlistView
    coverage: list[str]
    catalogs: list[IntelligenceCatalog] = Field(default_factory=list)


class IntelligenceRefreshResult(Contract):
    results: list[dict]
    receipt_ids: list[str]
