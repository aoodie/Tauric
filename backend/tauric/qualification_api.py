from uuid import UUID

from fastapi import APIRouter

from . import qualification
from .auth import CurrentOwner, Db
from .qualification_contracts import (
    QualificationAction,
    QualificationAssessmentRequest,
    QualificationFreeze,
    QualificationState,
    QualificationView,
)
from .qualification_models import QualificationProtocol

router = APIRouter(prefix="/api/v1/qualification", tags=["strategy qualification"])


@router.get("", response_model=QualificationState)
def state(db: Db, owner: CurrentOwner, portfolio_id: UUID | None = None):
    return qualification.state(db, owner, portfolio_id)


@router.post("/protocols", response_model=QualificationView, status_code=201)
def freeze(body: QualificationFreeze, db: Db, owner: CurrentOwner):
    return qualification.freeze(db, owner, body)


@router.get("/protocols/{protocol_id}", response_model=QualificationView)
def read(protocol_id: UUID, db: Db, owner: CurrentOwner):
    return qualification.view(
        db, owner, qualification.owned(db, owner, QualificationProtocol, protocol_id)
    )


@router.post("/protocols/{protocol_id}/evaluate", response_model=QualificationView, status_code=201)
def evaluate(protocol_id: UUID, body: QualificationAction, db: Db, owner: CurrentOwner):
    return qualification.evaluate(db, owner, protocol_id, body)


@router.post(
    "/protocols/{protocol_id}/assessment", response_model=QualificationView, status_code=202
)
def assess(protocol_id: UUID, body: QualificationAssessmentRequest, db: Db, owner: CurrentOwner):
    return qualification.assess(db, owner, protocol_id, body)


@router.post("/protocols/{protocol_id}/reports", response_model=QualificationView, status_code=201)
def report(protocol_id: UUID, body: QualificationAction, db: Db, owner: CurrentOwner):
    return qualification.report(db, owner, protocol_id, body)
