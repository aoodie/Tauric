"""Authenticated owner controls for broker-connected demo execution."""

from uuid import UUID

from fastapi import APIRouter

from . import broker_execution, broker_positions, broker_review, broker_routes, broker_setup
from .auth import CurrentOwner, Db
from .broker_contracts import (
    BrokerCloseAction,
    BrokerCloseView,
    BrokerConnectionStatus,
    BrokerEventView,
    BrokerExecutionState,
    BrokerIntentView,
    BrokerJournalView,
    BrokerLifecycleView,
    BrokerOrderCreate,
    BrokerPolicyChange,
    BrokerPolicyView,
    BrokerProvider,
    BrokerReviewedEntrySubmit,
    BrokerReviewRequest,
    BrokerReviewView,
    BrokerRouteChange,
    BrokerRouteEventView,
    BrokerRoutesState,
    BrokerRouteView,
    BrokerSetupPreview,
    BrokerSetupRequest,
    BrokerSetupView,
)
from .market_contracts import AnalysisView

router = APIRouter(prefix="/api/v1/brokers", tags=["demo-brokers"])


@router.get("/routes", response_model=BrokerRoutesState)
def routes(db: Db, owner: CurrentOwner):
    return broker_routes.state(db, owner)


@router.put("/routes/{route_id}", response_model=BrokerRouteView)
def route(route_id: UUID, body: BrokerRouteChange, db: Db, owner: CurrentOwner):
    return broker_routes.save(db, owner, route_id, body)


@router.get("/routes/{route_id}/history", response_model=list[BrokerRouteEventView])
def route_history(route_id: UUID, db: Db, owner: CurrentOwner):
    return broker_routes.history(db, owner, route_id)


@router.get("", response_model=BrokerExecutionState)
def state(db: Db, owner: CurrentOwner):
    return broker_execution.state(db, owner)


@router.post("/{provider}/check", response_model=BrokerConnectionStatus)
def check(provider: BrokerProvider, db: Db, owner: CurrentOwner):
    return broker_execution.check_account(db, provider)


@router.put("/{provider}/policy", response_model=BrokerPolicyView)
def policy(provider: BrokerProvider, body: BrokerPolicyChange, db: Db, owner: CurrentOwner):
    return broker_execution.save_policy(db, owner, provider, body)


@router.post("/{provider}/orders", response_model=BrokerIntentView, status_code=201)
def create(provider: BrokerProvider, body: BrokerOrderCreate, db: Db, owner: CurrentOwner):
    return broker_execution.create_order(db, owner, provider, body)


@router.post("/orders/{intent_id}/reconcile", response_model=BrokerIntentView)
def reconcile(intent_id: str, db: Db, owner: CurrentOwner):
    return broker_execution.reconcile(db, owner, intent_id)


@router.post("/orders/{intent_id}/cancel", response_model=BrokerIntentView)
def cancel(intent_id: str, db: Db, owner: CurrentOwner):
    return broker_execution.cancel(db, owner, intent_id)


@router.get("/orders/{intent_id}/history", response_model=list[BrokerEventView])
def history(intent_id: str, db: Db, owner: CurrentOwner):
    return broker_execution.history(db, owner, intent_id)


@router.get("/journal", response_model=BrokerJournalView)
def journal(db: Db, owner: CurrentOwner):
    return broker_positions.journal(db, owner)


@router.post("/orders/{intent_id}/position", response_model=BrokerLifecycleView)
def position(intent_id: str, db: Db, owner: CurrentOwner):
    return broker_positions.refresh(db, owner, intent_id)


@router.post("/orders/{intent_id}/close", response_model=BrokerCloseView)
def close(intent_id: str, body: BrokerCloseAction, db: Db, owner: CurrentOwner):
    return broker_positions.request_close(db, owner, intent_id, body)


@router.post("/closes/{close_id}/reconcile", response_model=BrokerCloseView)
def reconcile_close(close_id: str, db: Db, owner: CurrentOwner):
    return broker_positions.process_close(db, owner, close_id)


@router.post("/closes/{close_id}/repair-protection", response_model=BrokerCloseView)
def repair_protection(close_id: UUID, body: BrokerReviewedEntrySubmit, db: Db, owner: CurrentOwner):
    return broker_positions.repair_protection(db, owner, str(close_id), body)


@router.post("/setup-preview", response_model=BrokerSetupPreview)
def setup_preview(body: BrokerSetupRequest, db: Db, owner: CurrentOwner):
    result = broker_setup.capture(db, owner, body)
    db.commit()
    evidence = result["assessment_input"]
    return BrokerSetupPreview(
        context=result,
        entry_evidence_ready=evidence["entry_evidence_ready"],
        signal_action=evidence["signal"]["action"],
        reasons=evidence["signal"].get("reasons", [])
        + ([evidence["quote_error"]] if evidence["quote_error"] else []),
        expires_at=evidence["expires_at"],
        quote=evidence["quote"],
        planned_order=evidence["planned_order"],
    )


@router.post("/setup-assessments", response_model=AnalysisView, status_code=202)
def setup_assessment(body: BrokerSetupRequest, db: Db, owner: CurrentOwner):
    return broker_setup.enqueue(db, owner, body)


@router.get("/setup-assessments/{job_id}", response_model=BrokerSetupView)
def setup_report(job_id: str, db: Db, owner: CurrentOwner):
    return broker_setup.report(db, owner, job_id)


@router.get("/setup-assessments", response_model=list[BrokerSetupView])
def setup_reports(db: Db, owner: CurrentOwner):
    return broker_setup.reports(db, owner)


@router.get("/reviews", response_model=list[BrokerReviewView])
def reviews(db: Db, owner: CurrentOwner):
    return broker_review.views(db, owner)


@router.post("/reviews", response_model=BrokerReviewView, status_code=201)
def review(body: BrokerReviewRequest, db: Db, owner: CurrentOwner):
    return broker_review.enqueue(db, owner, body)


@router.post("/reviews/{job_id}/submit", response_model=BrokerIntentView)
def reviewed_entry(job_id: str, body: BrokerReviewedEntrySubmit, db: Db, owner: CurrentOwner):
    job = broker_review.checked(db, owner, job_id)
    if not job.decision_id:
        from fastapi import HTTPException

        raise HTTPException(409, "This broker review has no finalized entry permission.")
    return broker_review.prepare_entry(db, owner, job.decision_id, body)
