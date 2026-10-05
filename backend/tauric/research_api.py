from uuid import UUID

from fastapi import APIRouter, Query

from . import analytics, research
from .auth import CurrentOwner, Db
from .research_contracts import (
    AnalyticsView,
    DatasetCapture,
    DatasetImport,
    DatasetView,
    ReplayCreate,
    ReplayView,
    ResearchState,
)
from .schemas import ResourceRef, ResourceView

router = APIRouter(prefix="/api/v1", tags=["research and analytics"])


@router.get("/research", response_model=ResearchState)
def state(db: Db, owner: CurrentOwner, offset: int = Query(0, ge=0)):
    return research.state(db, owner, offset)


@router.post("/research/datasets/capture", response_model=DatasetView, status_code=201)
def capture(body: DatasetCapture, db: Db, owner: CurrentOwner):
    return research.capture(db, owner, body)


@router.post("/research/datasets/import", response_model=DatasetView, status_code=201)
def import_dataset(body: DatasetImport, db: Db, owner: CurrentOwner):
    return research.import_dataset(db, owner, body)


@router.post("/research/runs", response_model=ReplayView, status_code=201)
def run(body: ReplayCreate, db: Db, owner: CurrentOwner):
    return research.run(db, owner, body)


@router.get("/research/datasets/{record_id}", response_model=ResourceView)
def dataset(record_id: UUID, db: Db, owner: CurrentOwner):
    return resource("research_dataset", str(record_id), db, owner)


@router.get("/research/runs/{record_id}", response_model=ResourceView)
def result(record_id: UUID, db: Db, owner: CurrentOwner):
    return resource("research_run", str(record_id), db, owner)


@router.get("/analytics", response_model=AnalyticsView)
def outcomes(db: Db, owner: CurrentOwner, account_id: UUID | None = None):
    return analytics.view(db, owner, account_id)


def resource(resource_type, resource_id, db, owner):
    kind = "dataset" if resource_type == "research_dataset" else "run"
    row = research.owned(db, owner, resource_id, kind)
    return ResourceView(
        resource=ResourceRef(type=resource_type, id=resource_id),
        title=row.name,
        data={
            "id": row.id,
            "kind": kind,
            "name": row.name,
            "payload": row.payload,
            "checksum": row.checksum,
            "created_at": row.created_at.isoformat(),
        },
    )
