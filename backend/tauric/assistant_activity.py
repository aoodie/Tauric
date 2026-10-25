"""Owner-scoped projections of authoritative work. No effects or model calls."""

import base64
import json
import re
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import and_, exists, literal, or_, select
from sqlalchemy.orm import aliased

from . import paper
from .assistant_contracts import AssistantActivityFeed, AssistantActivityItem, AssistantActivityLink
from .broker_models import BrokerCloseRequest, BrokerOrderIntent
from .models import (
    AnalysisArtifact,
    AnalysisJob,
    AssistantRecord,
    AuditEvent,
    ChatResearch,
    ChatTurn,
    GovernanceJob,
    ScannerAnalysis,
    ScannerCandidate,
    ScannerRun,
    TradingRecord,
    now,
)
from .quant_models import QuantRecord

ACTIVE = {"queued", "running", "waiting", "blocked", "retry_scheduled", "reconciling"}
STATUS = {"succeeded": "completed", "saved": "completed", "canceled": "cancelled"}
SAFE_FIELDS = {
    "id",
    "status",
    "instrument_id",
    "symbol",
    "interval",
    "candidate_id",
    "backtest_id",
    "decision_id",
    "plan_id",
    "deployment_id",
    "strategy_version_id",
    "snapshot_id",
    "checksum",
    "execution_authority",
    "authority",
    "validation",
    "tests_executed",
    "runtime_code_changed",
    "source_applied",
    "model_calls",
    "model_calls_during_scan",
    "universe_count",
    "candidate_count",
    "published_candidate_count",
    "prefilter_pass_count",
    "scan_duration",
    "pipeline_duration",
    "engine",
    "evidence_status",
    "approval",
    "code_changes_applied",
    "setup_score",
    "catalog_provider",
    "processed_universe_count",
    "deferred_count",
    "catalog_pass_complete",
    "request_budget_remaining",
    "retry_after",
    "total_high_score",
    "score_meaning",
}


def projection(values):
    """Allowlisted scalars only. Never expose arbitrary inputs, errors or credentials."""
    if not isinstance(values, dict):
        return {}
    return {
        key: value[:400] if isinstance(value, str) else value
        for key, value in values.items()
        if key in SAFE_FIELDS and isinstance(value, (str, int, float, bool, type(None)))
    }


def verified(row):
    if paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Activity source receipt integrity failed.")
    return row.payload


def cursor_decode(value):
    if not value:
        return None
    try:
        if len(value) > 400:
            raise ValueError()
        stamp, identity = json.loads(base64.b64decode(value, altchars=b"-_", validate=True))
        stamp = datetime.fromisoformat(stamp)
        if stamp.tzinfo is None or not re.fullmatch(r"[a-z_]+:[a-zA-Z0-9-]{1,120}", identity):
            raise ValueError()
        return stamp, identity
    except (ValueError, TypeError, KeyError):
        raise HTTPException(
            422, "The activity cursor is invalid. Reload recent activity."
        ) from None


def cursor_encode(item):
    return base64.urlsafe_b64encode(
        json.dumps([item.timestamp.isoformat(), item.id]).encode()
    ).decode()


def rows(db, owner, model, prefix, limit, cursor=None, active=False, where=()):
    query = select(model).where(model.owner_id == owner.id, model.created_at <= now(), *where)
    if cursor:
        stamp, identity = cursor
        query = query.where(
            or_(
                model.created_at < stamp,
                and_(model.created_at == stamp, literal(prefix + ":") + model.id < identity),
            )
        )
    if active:
        pending = model.status.in_(["queued", "running"])
        if model is AnalysisJob:
            pending = or_(
                pending,
                and_(
                    model.status == "succeeded",
                    ~exists(
                        select(AnalysisArtifact.id).where(
                            AnalysisArtifact.owner_id == owner.id,
                            AnalysisArtifact.job_id == model.id,
                        )
                    ),
                ),
            )
        if model is GovernanceJob:
            pending = or_(pending, and_(model.status == "succeeded", model.decision_id.is_(None)))
        query = query.where(pending)
    return list(db.scalars(query.order_by(model.created_at.desc(), model.id.desc()).limit(limit)))


def status(value):
    value = STATUS.get(value, value)
    return (
        value
        if value in ACTIVE | {"completed", "failed", "cancelled", "review_required"}
        else "reconciling"
    )


def item(
    row,
    prefix,
    actor,
    category,
    title,
    state,
    resource,
    *,
    summary="",
    details=None,
    checksum=None,
    verification="observed_state",
    operation="",
    symbols=None,
    links=None,
):
    return AssistantActivityItem(
        id=prefix + ":" + row.id,
        timestamp=row.created_at,
        updated_at=getattr(row, "finished_at", None),
        actor=actor,
        category=category,
        title=title[:160],
        summary=summary[:500],
        status=state,
        operation=operation,
        resource_type=resource,
        resource_id=row.id,
        request_id=getattr(row, "request_id", None),
        details=details or {},
        checksum=checksum,
        verification=verification,
        symbols=symbols or [],
        links=links or [],
    )


def job_actor(db, owner, job_id):
    # Initiator attribution comes from explicit relationships, never audit actor guesses.
    if db.scalar(
        select(ChatResearch.id).where(
            ChatResearch.owner_id == owner.id,
            ChatResearch.job_id == job_id,
        )
    ) or db.scalar(
        select(AssistantRecord.id).where(
            AssistantRecord.owner_id == owner.id,
            AssistantRecord.stage == "completed",
            AssistantRecord.operation == "research_candidate",
            AssistantRecord.payload["result"]["id"].as_string() == job_id,
        )
    ):
        return "openclaw"
    if db.scalar(
        select(ScannerAnalysis.id).where(
            ScannerAnalysis.owner_id == owner.id,
            ScannerAnalysis.job_id == job_id,
        )
    ):
        # A scanner association alone cannot distinguish manual from automatic admission.
        return "unknown"
    return "owner"


def job_item(db, owner, row, category="research"):
    state = status(row.status)
    details = {"status_observed_at": now().isoformat()}
    if row.status == "running" and (
        not row.heartbeat_at or now() - row.heartbeat_at > timedelta(seconds=120)
    ):
        state = "blocked"
        details["observation"] = "Worker heartbeat is stale; completion is unverified."
    proof = None
    if isinstance(row, AnalysisJob) and row.status == "succeeded":
        artifact = db.scalar(
            select(AnalysisArtifact).where(
                AnalysisArtifact.owner_id == owner.id,
                AnalysisArtifact.job_id == row.id,
            )
        )
        if artifact:
            verified(artifact)
            proof = artifact.checksum
            details["result_id"] = artifact.id
        else:
            state = "blocked"
            details["observation"] = "The job reports success but its result is unavailable."
    if isinstance(row, GovernanceJob) and row.status == "succeeded":
        decision = db.get(TradingRecord, row.decision_id) if row.decision_id else None
        if decision and decision.owner_id == owner.id:
            verified(decision)
            proof = decision.checksum
            details["decision_id"] = decision.id
        else:
            state = "blocked"
            details["observation"] = "The review reports success but its decision is unavailable."
    if state == "failed":
        details["error_code"] = "SERVICE_FAILED"
    is_research = isinstance(row, AnalysisJob)
    return item(
        row,
        "research" if is_research else "governance",
        job_actor(db, owner, row.id) if is_research else "openclaw",
        category,
        "Native research" if is_research else "Governance review",
        state,
        "analysis" if is_research else "governance_job",
        summary="Authoritative job state; a request receipt does not establish completion.",
        details=details,
        checksum=proof,
        verification="checksum_verified" if proof else "observed_state",
        symbols=[row.instrument_id] if is_research else [],
    )


def receipt_item(db, owner, row):
    payload = verified(row)
    result = payload.get("result", {})
    operation = row.operation
    category = (
        "upgrade"
        if operation in {"prepare_upgrade", "stage_upgrade"}
        else "scan"
        if operation in {"save_scanner", "run_scanner"}
        else "paper_trade"
        if "paper" in operation
        else "research"
        if operation
        in {
            "research_candidate",
            "capture_dataset",
            "run_backtest",
            "retrieve_history",
            "session_research",
            "quant_experiment",
            "save_research_note",
        }
        else "governance"
        if "review" in operation
        else "proposal"
        if operation
        in {
            "create_strategy",
            "revise_strategy",
            "candidate_action",
            "propose_high_conviction_candidates",
        }
        else "operations"
    )
    state = "review_required" if category == "upgrade" else "completed"
    links = []
    if row.stage == "requested":
        state = "waiting" if now() - row.created_at < timedelta(seconds=120) else "reconciling"
    else:
        identity = result.get("id") or result.get("entry_intent_id")
        model = {
            "research_candidate": AnalysisJob,
            "request_paper_review": GovernanceJob,
            "quant_experiment": QuantRecord,
        }.get(operation)
        job = db.get(model, identity) if model and isinstance(identity, str) else None
        if model:
            state = "blocked"
            if job and job.owner_id == owner.id:
                if model is QuantRecord:
                    verified(job)
                    state = status(job.payload.get("status"))
                    resource = "research_lab"
                else:
                    observed = job_item(
                        db, owner, job, "governance" if model is GovernanceJob else "research"
                    )
                    state = observed.status
                    resource = observed.resource_type
                links.append(AssistantActivityLink(resource_type=resource, resource_id=job.id))
        elif operation in {
            "submit_broker_entry",
            "close_broker_position",
            "cancel_broker_order",
            "reconcile_broker_order",
            "reconcile_broker_close",
            "repair_broker_protection",
        }:
            from . import broker_execution, broker_positions

            entry_repair = operation == "repair_broker_protection" and not result.get("id")
            close_operation = (
                operation
                in {
                    "close_broker_position",
                    "reconcile_broker_close",
                    "repair_broker_protection",
                }
                and not entry_repair
            )
            model = BrokerCloseRequest if close_operation else BrokerOrderIntent
            broker_row = db.get(model, identity) if isinstance(identity, str) else None
            state = "blocked"
            if broker_row and broker_row.owner_id == owner.id:
                if broker_row.checksum != broker_execution.checksum(broker_row.payload):
                    raise HTTPException(409, "Broker activity source integrity failed.")
                observed = (
                    broker_positions.close_view(db, broker_row)
                    if close_operation
                    else broker_positions.latest(db, broker_row)
                    if entry_repair
                    else broker_execution.view(db, broker_row)
                )
                if observed is None:
                    observed = broker_execution.view(db, broker_row)
                terminal = (
                    getattr(observed, "protection_status", None) in {"covered", "not_required"}
                    if entry_repair
                    else observed.status
                    in {
                        "filled",
                        "closed",
                        "cancelled",
                        "rejected",
                        "expired",
                    }
                )
                state = (
                    "completed"
                    if terminal
                    else "reconciling"
                    if observed.status == "unknown"
                    else "waiting"
                )
                links.append(
                    AssistantActivityLink(
                        resource_type="broker_close" if close_operation else "broker_order",
                        resource_id=broker_row.id,
                    )
                )
                result = {**result, "status": observed.status}
    details = projection(result)
    symbols = []
    if operation == "propose_high_conviction_candidates":
        briefs = []
        for opportunity in result.get("opportunities", [])[:25]:
            if not isinstance(opportunity, dict):
                continue
            brief = projection(opportunity)
            assessment = opportunity.get("assessment", {})
            if isinstance(assessment, dict):
                brief["assessment"] = {
                    key: assessment[key][:80]
                    for key in ("version", "score", "rating")
                    if isinstance(assessment.get(key), str)
                }
            briefs.append(brief)
            if isinstance(brief.get("instrument_id"), str):
                symbols.append(brief["instrument_id"])
            if isinstance(brief.get("candidate_id"), str):
                links.append(
                    AssistantActivityLink(
                        resource_type="scanner_candidate", resource_id=brief["candidate_id"]
                    )
                )
        details["opportunities"] = briefs
    if category == "upgrade":
        details["files"] = [
            {"path": f["path"][:240]}
            for f in result.get("files", [])[:30]
            if isinstance(f, dict)
            and isinstance(f.get("path"), str)
            and not any(p.startswith(".") for p in f["path"].split("/"))
        ]
    return item(
        row,
        "assistant",
        "openclaw",
        category,
        row.title,
        state,
        "assistant_receipt",
        summary="Action requested; await its recorded outcome."
        if row.stage == "requested"
        else "Saved action receipt; linked jobs retain their own completion state.",
        details=details,
        checksum=row.checksum,
        verification="checksum_verified",
        operation=operation,
        links=links,
        symbols=symbols,
    )


def collect(db, owner, limit, cursor=None, active=False):
    output = []
    for model, category in ((AnalysisJob, "research"), (GovernanceJob, "governance")):
        prefix = "research" if model is AnalysisJob else "governance"
        output.extend(
            job_item(db, owner, r, category)
            for r in rows(
                db,
                owner,
                model,
                prefix,
                limit,
                cursor,
                active,
            )
        )
    for row in rows(db, owner, ChatTurn, "chat", limit, cursor, active):
        state = status(row.status)
        if state == "running" and (
            not row.heartbeat_at or now() - row.heartbeat_at > timedelta(seconds=120)
        ):
            state = "blocked"
        output.append(
            item(
                row,
                "chat",
                "openclaw",
                "chat",
                "OpenClaw request",
                state,
                "chat_turn",
                summary="Conversation work; tool actions have separate receipts.",
            )
        )
    completed = aliased(AssistantRecord)
    unmatched = or_(
        AssistantRecord.stage == "completed",
        ~exists(
            select(completed.id).where(
                completed.owner_id == AssistantRecord.owner_id,
                completed.request_id == AssistantRecord.request_id,
                completed.stage == "completed",
            )
        ),
    )
    assistant_where = [unmatched]
    if active:
        async_job = exists(
            select(AnalysisJob.id).where(
                AnalysisJob.owner_id == owner.id,
                AnalysisJob.id == AssistantRecord.payload["result"]["id"].as_string(),
                AnalysisJob.status.in_(["queued", "running"]),
            )
        )
        async_review = exists(
            select(GovernanceJob.id).where(
                GovernanceJob.owner_id == owner.id,
                GovernanceJob.id == AssistantRecord.payload["result"]["id"].as_string(),
                GovernanceJob.status.in_(["queued", "running"]),
            )
        )
        async_quant = exists(
            select(QuantRecord.id).where(
                QuantRecord.owner_id == owner.id,
                QuantRecord.id == AssistantRecord.payload["result"]["id"].as_string(),
                QuantRecord.payload["status"].as_string().in_(ACTIVE),
            )
        )
        assistant_where.append(
            or_(
                AssistantRecord.stage == "requested",
                and_(AssistantRecord.operation == "research_candidate", async_job),
                and_(AssistantRecord.operation == "request_paper_review", async_review),
                and_(AssistantRecord.operation == "quant_experiment", async_quant),
            )
        )
    for row in rows(db, owner, AssistantRecord, "assistant", limit, cursor, where=assistant_where):
        observed = receipt_item(db, owner, row)
        if not active or observed.status in ACTIVE:
            output.append(observed)
    quant_where = [QuantRecord.kind == "quant_job"]
    if active:
        quant_where.append(QuantRecord.payload["status"].as_string().in_(ACTIVE))
    for row in rows(db, owner, QuantRecord, "quant", limit, cursor, where=quant_where):
        payload = verified(row)
        state = status(payload.get("status"))
        if state == "completed" and not payload.get("result"):
            state = "blocked"
        if active and state not in ACTIVE:
            continue
        output.append(
            item(
                row,
                "quant",
                "platform_worker",
                "research",
                row.name,
                state,
                "research_lab",
                checksum=row.checksum,
                verification="checksum_verified",
                summary="External-engine job; status comes from the saved Lab record.",
                details=projection(payload),
            )
        )
    if active:
        return output
    for row in rows(db, owner, ScannerRun, "scan", limit, cursor):
        payload = verified(row)
        scheduled = payload.get("request", {}).get("trigger") == "scheduled"
        initiated = db.scalar(
            select(AssistantRecord.id).where(
                AssistantRecord.owner_id == owner.id,
                AssistantRecord.stage == "completed",
                AssistantRecord.operation == "run_scanner",
                AssistantRecord.payload["result"]["id"].as_string() == row.id,
            )
        )
        output.append(
            item(
                row,
                "scan",
                "openclaw" if initiated else "platform_worker" if scheduled else "owner",
                "scan",
                "Market scan",
                "completed",
                "scanner_run",
                summary="Deterministic scan completed. No model calls during detection.",
                details=projection(payload.get("metrics", {})),
                checksum=row.checksum,
                verification="checksum_verified",
            )
        )
    for row in rows(db, owner, ScannerCandidate, "candidate", limit, cursor):
        payload = verified(row)
        output.append(
            item(
                row,
                "candidate",
                "platform_worker",
                "proposal",
                "Setup detected",
                "completed",
                "scanner_candidate",
                symbols=[row.instrument_id],
                summary="Research candidate. Trade permission remains separate.",
                details=projection(payload),
                checksum=row.checksum,
                verification="checksum_verified",
            )
        )
    audit_filter = or_(
        *(
            AuditEvent.action.startswith(p)
            for p in (
                "paper.",
                "governance.",
                "research.",
                "broker.",
                "operations.",
                "scanner.paper_plan",
            )
        )
    )
    for row in rows(db, owner, AuditEvent, "audit", limit, cursor, where=(audit_filter,)):
        category = (
            "paper_trade"
            if row.action.startswith("paper.")
            else "governance"
            if row.action.startswith("governance.")
            else "research"
            if row.action.startswith("research.")
            else "operations"
        )
        output.append(
            item(
                row,
                "audit",
                "unknown",
                category,
                row.action.replace(".", " · "),
                "failed"
                if "failed" in row.action
                else "waiting"
                if any(v in row.action for v in ("requested", "queued", "started", "scheduled"))
                else "completed",
                "audit",
                summary="Recorded service event; actor attribution is unavailable.",
                details=projection(row.details),
                verification="append_only",
                operation=row.action,
                links=[
                    AssistantActivityLink(
                        resource_type=row.resource_type,
                        resource_id=row.resource_id,
                    )
                ],
            )
        )
    return output


def stream(db, owner, limit=50, cursor=None):
    if not 1 <= limit <= 100:
        raise HTTPException(422, "Choose an activity limit from 1 to 100.")
    boundary = cursor_decode(cursor)
    ordered = sorted(
        collect(db, owner, limit + 1, boundary), key=lambda v: (v.timestamp, v.id), reverse=True
    )
    visible = ordered[:limit]
    current = sorted(
        collect(db, owner, 16, active=True), key=lambda v: (v.timestamp, v.id), reverse=True
    )[:16]
    return AssistantActivityFeed(
        generated_at=now(),
        items=visible,
        current_work=current,
        next_cursor=cursor_encode(visible[-1]) if len(ordered) > limit and visible else None,
        coverage=[
            "Assistant actions, conversation work, scans, candidates, research and governance jobs.",
            "Statuses are read from authoritative service records at refresh time.",
            "Current work shows the 16 most recent active records; history can contain additional active work.",
            "Historical audit actor attribution can be unknown; it is never assumed to be OpenClaw.",
            "A scan is visible after its transaction completes; running scan telemetry is not available.",
            "General engineering, release and durable goal supervision require the ownership runtime.",
        ],
    )


def observations(db, owner, identities):
    """Refresh older loaded work by identity without changing history pagination."""
    models = {
        "assistant": AssistantRecord,
        "research": AnalysisJob,
        "governance": GovernanceJob,
        "chat": ChatTurn,
        "quant": QuantRecord,
    }
    output = []
    if not 1 <= len(identities) <= 100:
        raise HTTPException(422, "Refresh up to 100 work records at a time.")
    for identity in dict.fromkeys(identities):
        if not isinstance(identity, str) or not re.fullmatch(
            r"(?:assistant|research|governance|chat|quant):[a-zA-Z0-9-]{1,120}", identity
        ):
            raise HTTPException(422, "Choose a recorded work identity.")
        prefix, key = identity.split(":", 1)
        row = db.get(models[prefix], key)
        if not row or row.owner_id != owner.id:
            continue
        if prefix == "assistant":
            output.append(receipt_item(db, owner, row))
        elif prefix in {"research", "governance"}:
            output.append(
                job_item(db, owner, row, "research" if prefix == "research" else "governance")
            )
        elif prefix == "chat":
            state = status(row.status)
            if state == "running" and (
                not row.heartbeat_at or now() - row.heartbeat_at > timedelta(seconds=120)
            ):
                state = "blocked"
            output.append(
                item(
                    row,
                    prefix,
                    "openclaw",
                    "chat",
                    "OpenClaw request",
                    state,
                    "chat_turn",
                    summary="Conversation work; tool actions have separate receipts.",
                )
            )
        else:
            payload = verified(row)
            state = status(payload.get("status"))
            if state == "completed" and not payload.get("result"):
                state = "blocked"
            output.append(
                item(
                    row,
                    prefix,
                    "platform_worker",
                    "research",
                    row.name,
                    state,
                    "research_lab",
                    checksum=row.checksum,
                    verification="checksum_verified",
                    details=projection(payload),
                    summary="External-engine job; status comes from the saved Lab record.",
                )
            )
    return output
