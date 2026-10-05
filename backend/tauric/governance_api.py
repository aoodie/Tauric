from fastapi import APIRouter, HTTPException

from . import governance
from .auth import CurrentOwner, Db
from .governance_contracts import GovernanceEnqueue, GovernanceJobView, GovernanceStatusView
from .models import GovernanceJob
from .schemas import ResourceRef, ResourceView

router = APIRouter(prefix="/api/v1/governance", tags=["governance"])


@router.get("", response_model=GovernanceStatusView)
def status(db: Db, owner: CurrentOwner):
    return governance.status(db, owner)


@router.post("/jobs", response_model=GovernanceJobView, status_code=201)
def enqueue(body: GovernanceEnqueue, db: Db, owner: CurrentOwner):
    return governance.enqueue(db, owner, body)


@router.get("/jobs/{job_id}", response_model=GovernanceJobView)
def job(job_id: str, db: Db, owner: CurrentOwner):
    row = db.get(GovernanceJob, job_id)
    if not row or row.owner_id != owner.id:
        raise HTTPException(404, "This review does not exist.")
    return governance.view(row)


def resource(job_id, db, owner):
    row = db.get(GovernanceJob, job_id)
    if not row or row.owner_id != owner.id:
        raise HTTPException(404, "This review does not exist.")
    if governance.paper.checksum(row.context) != row.checksum:
        raise HTTPException(409, "Review context integrity failed.")
    return ResourceView(
        resource=ResourceRef(type="governance_job", id=row.id),
        title="OpenClaw paper review",
        data={
            "job": governance.view(row).model_dump(mode="json"),
            "request": row.request,
            "captured_context": row.context,
        },
    )
