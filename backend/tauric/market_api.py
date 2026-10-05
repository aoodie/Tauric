from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from . import analysis, instrument_discovery, market
from .auth import CurrentOwner, Db
from .data_providers import provider_status
from .market_contracts import (
    AnalysisArtifactView,
    AnalysisRequest,
    AnalysisStatus,
    AnalysisView,
    DataProviderView,
    InstrumentView,
    Interval,
    MarketRefresh,
    MarketView,
)
from .models import AnalysisArtifact, AnalysisJob, Instrument, MarketSnapshot
from .schemas import ResourceRef, ResourceView

router = APIRouter()


@router.get("/api/v1/market-providers", response_model=list[DataProviderView])
def providers(db: Db, owner: CurrentOwner):
    return provider_status(db)


@router.get("/api/v1/instruments", response_model=list[InstrumentView])
def instruments(db: Db, owner: CurrentOwner):
    return [
        InstrumentView.model_validate(row.definition)
        for row in db.scalars(select(Instrument).order_by(Instrument.id))
    ]


@router.get(
    "/api/v1/instrument-discovery/{provider}", response_model=instrument_discovery.DiscoveryView
)
def discover_instruments(
    provider: instrument_discovery.Provider,
    db: Db,
    owner: CurrentOwner,
    query: str = Query("", max_length=100),
    offset: int = Query(0, ge=0, le=50000),
    limit: int = Query(50, ge=1, le=100),
):
    return instrument_discovery.discover(db, provider, query, offset, limit)


@router.post("/api/v1/instruments", response_model=InstrumentView)
def add_instrument(body: instrument_discovery.InstrumentAdd, db: Db, owner: CurrentOwner):
    return instrument_discovery.register(db, owner, body)


@router.get("/api/v1/market/{instrument_id}", response_model=MarketView)
def candles(instrument_id: str, db: Db, owner: CurrentOwner, interval: Interval = "15m"):
    return market.view(db, instrument_id, interval)


@router.post("/api/v1/market/{instrument_id}/refresh", response_model=MarketView)
def refresh(instrument_id: str, body: MarketRefresh, db: Db, owner: CurrentOwner):
    return market.refresh(db, instrument_id, body.interval)


@router.get("/api/v1/analysis/status", response_model=AnalysisStatus)
def analysis_status(db: Db, owner: CurrentOwner):
    return analysis.status(db)


@router.post("/api/v1/analysis", response_model=AnalysisView, status_code=202)
def request_analysis(body: AnalysisRequest, db: Db, owner: CurrentOwner):
    return analysis.enqueue(db, owner, body)


@router.get("/api/v1/analysis", response_model=list[AnalysisView])
def analysis_jobs(
    db: Db, owner: CurrentOwner, limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)
):
    return [
        analysis.view(db, job)
        for job in db.scalars(
            select(AnalysisJob)
            .where(AnalysisJob.owner_id == owner.id)
            .order_by(AnalysisJob.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ]


@router.get("/api/v1/analysis/{job_id}", response_model=AnalysisView)
def analysis_job(job_id: str, db: Db, owner: CurrentOwner):
    return analysis.view(db, analysis.get_job(db, owner, job_id))


@router.get("/api/v1/analysis-artifacts/{artifact_id}", response_model=AnalysisArtifactView)
def artifact(artifact_id: str, db: Db, owner: CurrentOwner):
    record = db.get(AnalysisArtifact, artifact_id)
    if not record or record.owner_id != owner.id:
        raise HTTPException(404, "This native report does not exist.")
    return record


def resource(resource_type: str, resource_id: str, db: Db, owner: CurrentOwner):
    ref = ResourceRef(type=resource_type, id=resource_id)
    if resource_type == "instrument":
        spec = market.instrument(db, resource_id)
        return ResourceView(resource=ref, title=spec.symbol, data=spec.model_dump(mode="json"))
    if resource_type == "analysis":
        job = analysis.get_job(db, owner, resource_id)
        return ResourceView(
            resource=ref,
            title="Native analysis run",
            data=analysis.view(db, job).model_dump(mode="json"),
        )
    if resource_type == "report":
        record = artifact(resource_id, db, owner)
        return ResourceView(
            resource=ref,
            title="Original Tauric reports",
            data=AnalysisArtifactView.model_validate(record).model_dump(mode="json"),
        )
    if resource_type == "market_snapshot":
        record = db.get(MarketSnapshot, resource_id)
        if record:
            return ResourceView(
                resource=ref,
                title="Chart data provenance",
                data={
                    "instrument_id": record.instrument_id,
                    "interval": record.interval,
                    "received_at": record.received_at.isoformat(),
                    "provider_time": record.provider_time.isoformat(),
                    "checksum": record.checksum,
                    "availability": record.data["availability"],
                    "source": record.data["source"],
                    "candles": len(record.data["candles"]),
                    "volume_type": record.data["volume_type"],
                    "data_context": record.data.get("data_context"),
                    "quality": record.data.get("quality"),
                },
            )
    raise HTTPException(404, "This resource does not exist.")
