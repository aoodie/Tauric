from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from . import candidate_research, scanner, scanner_context, scanner_paper
from .auth import CurrentOwner, Db
from .market_contracts import AnalysisView
from .models import ScannerCandidate, ScannerProfileVersion, ScannerRun
from .scanner_contracts import (
    CandidateAction,
    CandidateEscalate,
    CandidatePaperReadiness,
    CandidatePaperStage,
    CandidateView,
    ScannerProfileView,
    ScannerRunRequest,
    ScannerRunView,
    ScannerSave,
    ScannerState,
)
from .schemas import Contract, ResourceRef, ResourceView
from .trading_contracts import TradingRecordView


class ScannerContextRefresh(Contract):
    instrument_ids: list[str] = Field(min_length=1, max_length=50)


router = APIRouter(prefix="/api/v1/scanners", tags=["scanners"])


@router.post("/context")
def refresh_context(body: ScannerContextRefresh, db: Db, owner: CurrentOwner):
    return scanner_context.refresh(db, owner, body.instrument_ids)


@router.get("", response_model=ScannerState)
def state(db: Db, owner: CurrentOwner):
    return scanner.state(db, owner)


@router.put("/profiles", response_model=ScannerProfileView)
def save(body: ScannerSave, db: Db, owner: CurrentOwner):
    return scanner.save(db, owner, body)


@router.get("/profiles/{profile_id}/history", response_model=list[ScannerProfileView])
def history(profile_id: str, db: Db, owner: CurrentOwner):
    row = scanner.head(db, profile_id)
    if not row or row.owner_id != owner.id:
        scanner.paper.fail("This scanner resource does not exist.", 404)
    return [
        scanner.profile_view(db, owner, row)
        for row in db.scalars(
            select(ScannerProfileVersion)
            .where(
                ScannerProfileVersion.profile_id == profile_id,
                ScannerProfileVersion.owner_id == owner.id,
            )
            .order_by(ScannerProfileVersion.revision.desc())
        )
    ]


@router.post("/runs", response_model=ScannerRunView)
def run(body: ScannerRunRequest, db: Db, owner: CurrentOwner):
    return scanner.run(db, owner, body)


@router.get("/candidates/{candidate_id}", response_model=CandidateView)
def candidate(candidate_id: str, db: Db, owner: CurrentOwner):
    return scanner.candidate_view(
        db, owner, scanner.owned(db, ScannerCandidate, owner, candidate_id)
    )


@router.post("/candidates/{candidate_id}/actions", response_model=CandidateView)
def action(candidate_id: str, body: CandidateAction, db: Db, owner: CurrentOwner):
    return scanner.candidate_action(db, owner, candidate_id, body)


@router.post("/candidates/{candidate_id}/analysis", response_model=AnalysisView)
def escalate(candidate_id: str, body: CandidateEscalate, db: Db, owner: CurrentOwner):
    return candidate_research.escalate(db, owner, candidate_id, body)


@router.get("/candidates/{candidate_id}/paper-readiness", response_model=CandidatePaperReadiness)
def paper_readiness(candidate_id: str, db: Db, owner: CurrentOwner):
    return scanner_paper.readiness(db, owner, candidate_id)


@router.post("/candidates/{candidate_id}/paper-plan", response_model=TradingRecordView)
def paper_stage(candidate_id: str, body: CandidatePaperStage, db: Db, owner: CurrentOwner):
    return scanner_paper.stage(db, owner, candidate_id, body)


def resource(kind, resource_id, db, owner):
    models = {
        "scanner_candidate": ScannerCandidate,
        "scanner_profile": ScannerProfileVersion,
        "scanner_run": ScannerRun,
    }
    row = scanner.owned(db, models[kind], owner, resource_id)
    if kind == "scanner_candidate":
        view = scanner.candidate_view(db, owner, row)
        data = {**view.model_dump(mode="json"), "original_evidence": row.payload}
        title = "Scanner candidate · " + row.instrument_id
    elif kind == "scanner_profile":
        view = scanner.profile_view(db, owner, row)
        data, title = view.model_dump(mode="json"), view.name + " scanner profile"
    else:
        data, title = scanner.run_view(row).model_dump(mode="json"), "Deterministic scanner run"
    return ResourceView(resource=ResourceRef(type=kind, id=resource_id), title=title, data=data)
