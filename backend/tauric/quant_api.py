from uuid import UUID

from fastapi import APIRouter

from . import quant_research
from .auth import CurrentOwner, Db
from .quant_contracts import (
    ExperimentCreate,
    HistoricalDatasetCreate,
    LabRecordView,
    LabState,
    SessionReportCreate,
)
from .research_contracts import DatasetView

router = APIRouter(prefix="/api/v1/research/lab", tags=["session research and machine learning"])


@router.post("/datasets/history", response_model=DatasetView, status_code=201)
def history(body: HistoricalDatasetCreate, db: Db, owner: CurrentOwner):
    return quant_research.historical_dataset(db, owner, body)


@router.get("", response_model=LabState)
def state(db: Db, owner: CurrentOwner):
    return quant_research.state(db, owner)


@router.post("/reports", response_model=LabRecordView, status_code=201)
def report(body: SessionReportCreate, db: Db, owner: CurrentOwner):
    return quant_research.report(db, owner, body)


@router.post("/experiments", response_model=LabRecordView, status_code=201)
def experiment(body: ExperimentCreate, db: Db, owner: CurrentOwner):
    return quant_research.enqueue(db, owner, body)


@router.get("/records/{record_id}", response_model=LabRecordView)
def record(record_id: UUID, db: Db, owner: CurrentOwner):
    from .quant_models import QuantRecord

    row = db.get(QuantRecord, str(record_id))
    kind = row.kind if row and row.kind in quant_research.KINDS else "session_report"
    return quant_research.view(quant_research.owned(db, owner, record_id, kind))


@router.post("/experiments/{record_id}/cancel", response_model=LabRecordView)
def cancel(record_id: UUID, db: Db, owner: CurrentOwner):
    return quant_research.cancel(db, owner, record_id)
