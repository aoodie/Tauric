from fastapi import APIRouter, HTTPException, Query

from . import intelligence, intelligence_catalog
from .auth import CurrentOwner, Db
from .intelligence_contracts import (
    IntelligenceCatalog,
    IntelligenceDashboard,
    IntelligenceMarket,
    IntelligenceRefresh,
    IntelligenceRefreshResult,
    IntelligenceRegister,
    IntelligenceWatchlistChange,
    IntelligenceWatchlistView,
)
from .intelligence_models import IntelligenceReceipt
from .market_contracts import AnalysisView, InstrumentView

router = APIRouter(prefix="/api/v1/intelligence", tags=["market intelligence"])


@router.post("/catalogs/refresh", response_model=list[IntelligenceCatalog])
def catalogs(db: Db, owner: CurrentOwner):
    return intelligence_catalog.synchronize(db, owner)


@router.post("/register", response_model=InstrumentView)
def register(body: IntelligenceRegister, db: Db, owner: CurrentOwner):
    return intelligence_catalog.register(db, owner, body)


@router.get("", response_model=IntelligenceDashboard)
def dashboard(
    db: Db,
    owner: CurrentOwner,
    market: IntelligenceMarket = "overview",
    offset: int = Query(0, ge=0, le=50000),
    limit: int = Query(24, ge=1, le=24),
    query: str = Query("", max_length=100),
    watched: bool = False,
):
    return intelligence.dashboard(db, owner, market, offset, limit, query.strip(), watched)


@router.post("/refresh", response_model=IntelligenceRefreshResult)
def refresh(body: IntelligenceRefresh, db: Db, owner: CurrentOwner):
    return intelligence.refresh(db, owner, body)


@router.put("/watchlist", response_model=IntelligenceWatchlistView)
def watchlist(body: IntelligenceWatchlistChange, db: Db, owner: CurrentOwner):
    return intelligence.save_watchlist(db, owner, body)


@router.get("/receipts/{receipt_id}")
def receipt(receipt_id: str, db: Db, owner: CurrentOwner):
    row = db.get(IntelligenceReceipt, receipt_id)
    if not row or row.owner_id != owner.id:
        raise HTTPException(404, "This source receipt does not exist.")
    intelligence.checked(row)
    return {
        "id": row.id,
        "checksum": row.checksum,
        "created_at": row.created_at,
        "source": row.payload,
    }


@router.get("/reports/{instrument_id}", response_model=list[AnalysisView])
def reports(instrument_id: str, db: Db, owner: CurrentOwner):
    return intelligence.reports(db, owner, instrument_id)
