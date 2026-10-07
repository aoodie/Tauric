"""Private service boundary. Gateway/operator tokens never reach browsers."""

import json
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from . import governance, paper
from .assistant_contracts import AssistantInvocation, AssistantRead
from .auth import Db
from .chat_contracts import ResearchInvocation
from .config import runtime_config
from .governance_contracts import GatewayHeartbeat
from .models import (
    AnalysisArtifact,
    AnalysisJob,
    GatewayStatus,
    GovernanceJob,
    Owner,
    PaperAccount,
    ScannerCandidate,
    StrategyVersion,
    now,
)
from .portfolio_contracts import ChatContextRead, PortfolioChatAction, PortfolioResearchInvocation

router = APIRouter(prefix="/internal/openclaw", include_in_schema=False)


@router.post("/project-read")
def project_read(body: AssistantRead, request: Request, db: Db):
    from . import assistant

    return assistant.read(db, service_owner(request, db), body)


@router.post("/project-action")
def project_action(body: AssistantInvocation, request: Request, db: Db):
    from . import assistant

    return assistant.execute(db, service_owner(request, db), body)


@router.post("/portfolio-research", status_code=202)
def request_portfolio_research(body: PortfolioResearchInvocation, request: Request, db: Db):
    from uuid import NAMESPACE_URL, uuid5

    from . import chat, chat_research, portfolio_research
    from .portfolio_research_contracts import PortfolioResearchRequest

    owner = service_owner(request, db)
    config, _ = chat.load_config(db)
    turn = chat_research.authorize(db, owner, body, config)
    if not turn or not turn.portfolio_id:
        raise HTTPException(403, "Open a portfolio conversation to request portfolio research.")
    return portfolio_research.enqueue(
        db,
        owner,
        turn.portfolio_id,
        PortfolioResearchRequest(
            request_id=uuid5(
                NAMESPACE_URL,
                "portfolio-research-chat:" + body.session_key + ":" + body.tool_call_id,
            ),
            reason=body.reason,
        ),
        trigger="chat",
    )


@router.post("/portfolio-action")
def portfolio_action(body: PortfolioChatAction, request: Request, db: Db):
    from sqlalchemy import func

    from . import chat, chat_research, portfolios

    owner = service_owner(request, db)
    db.execute(select(func.pg_advisory_xact_lock(chat.LOCK)))
    config, _ = chat.load_config(db)
    turn = chat_research.authorize(db, owner, body, config)
    return portfolios.chat_action(db, owner, turn, body)


@router.post("/chat-read")
def bound_chat_context(body: ChatContextRead, request: Request, db: Db):
    from . import chat, chat_research, portfolios, services
    from .models import ChatResearch, ChatTurn, PortfolioResearchLink
    from .qualification_models import QualificationAdmission, QualificationProtocol

    owner = service_owner(request, db)
    config, _ = chat.load_config(db)
    turn = chat_research.authorize(db, owner, body, config)
    if not turn or not turn.portfolio_id:
        if body.job_id:
            return chat_research.status(db, owner, str(body.job_id))
        return chat_context(request, db, str(body.report_id) if body.report_id else None)
    portfolio_id = turn.portfolio_id
    allowed = (
        select(ChatResearch.job_id)
        .join(ChatTurn, ChatTurn.id == ChatResearch.turn_id)
        .where(ChatTurn.owner_id == owner.id, ChatTurn.portfolio_id == portfolio_id)
    )
    allowed = allowed.union(
        select(PortfolioResearchLink.job_id).where(
            PortfolioResearchLink.owner_id == owner.id,
            PortfolioResearchLink.portfolio_id == portfolio_id,
        ),
        select(QualificationAdmission.job_id)
        .join(QualificationProtocol, QualificationProtocol.id == QualificationAdmission.protocol_id)
        .where(
            QualificationAdmission.owner_id == owner.id,
            QualificationProtocol.portfolio_id == portfolio_id,
        ),
    )
    if body.report_id:
        artifact = db.scalar(
            select(AnalysisArtifact).where(
                AnalysisArtifact.id == str(body.report_id),
                AnalysisArtifact.owner_id == owner.id,
                AnalysisArtifact.job_id.in_(allowed),
            )
        )
        if not artifact:
            raise HTTPException(404, "Original report not found in this portfolio.")
        result = {
            "portfolio_id": portfolio_id,
            "authority": "read_only",
            "original_report": {
                "id": artifact.id,
                "checksum": artifact.checksum,
                "payload": artifact.payload,
            },
        }
    elif body.job_id:
        if not db.scalar(
            select(AnalysisJob.id).where(
                AnalysisJob.owner_id == owner.id,
                AnalysisJob.id == str(body.job_id),
                AnalysisJob.id.in_(allowed),
            )
        ):
            raise HTTPException(404, "Research job not found in this portfolio.")
        result = {"portfolio_id": portfolio_id, **chat_research.status(db, owner, str(body.job_id))}
    else:
        result = portfolios.context(db, owner, portfolio_id).model_dump(mode="json")
        result["research_capabilities"] = chat_research.capabilities(db)
    services.audit(
        db,
        owner,
        "portfolio.context_read",
        "chat",
        turn.id,
        "OpenClaw read its bound portfolio",
        {"portfolio_id": portfolio_id},
    )
    db.commit()
    return result


@router.post("/research", status_code=202)
def request_research(body: ResearchInvocation, request: Request, db: Db):
    from . import chat_research

    return chat_research.request(db, service_owner(request, db), body)


@router.get("/research/{job_id}")
def research_status(job_id: str, request: Request, db: Db):
    from . import chat_research

    return chat_research.status(db, service_owner(request, db), job_id)


@router.get("/chat-context")
def chat_context(request: Request, db: Db, report_id: str | None = None):
    from sqlalchemy import func

    from . import chat, chat_research, live_trading, services

    owner = service_owner(request, db)
    services.audit(
        db,
        owner,
        "chat.context_read",
        "chat",
        report_id or "status",
        "OpenClaw read owner chat context",
        {"report_id": report_id},
    )
    if report_id:
        row = db.get(AnalysisArtifact, report_id)
        if not row or row.owner_id != owner.id:
            raise HTTPException(404, "Original report not found.")
        db.commit()
        return {
            "authority": "read_only",
            "original_report": {"id": row.id, "checksum": row.checksum, "payload": row.payload},
        }
    jobs = list(
        db.scalars(
            select(AnalysisJob)
            .where(AnalysisJob.owner_id == owner.id)
            .order_by(AnalysisJob.created_at.desc())
            .limit(10)
        )
    )
    reports = list(
        db.scalars(
            select(AnalysisArtifact)
            .where(AnalysisArtifact.owner_id == owner.id)
            .order_by(AnalysisArtifact.created_at.desc())
            .limit(10)
        )
    )
    connections = chat.config_view(db, owner)
    research_capabilities = chat_research.capabilities(db)
    readiness = live_trading.state(db, owner)
    db.commit()
    return {
        "authority": "read_only",
        "live_execution_enabled": any(b.live_enabled for b in readiness.brokers),
        "broker_trading": {
            "brokers": [b.model_dump(mode="json") for b in readiness.brokers],
            "valuation_worker_ready": readiness.valuation_worker_ready,
            "forward_worker_ready": readiness.forward_worker_ready,
            "forward_protocol_count": len(readiness.forward_protocols),
            "latest_forward_reports": [
                {
                    "id": r.id,
                    "provider": r.payload["provider"],
                    "status": r.payload["status"],
                    "maximum_risk_percent": r.payload["maximum_risk_percent"],
                }
                for r in readiness.forward_reports[:5]
            ],
            "native_approval_required": True,
            "openclaw_may_raise_native_risk": False,
            "chat_order_authority": False,
        },
        "research_scope": research_capabilities["scope"],
        "research_capabilities": research_capabilities,
        "chat_connections": {
            "profile": connections.profile,
            "web_ready": connections.gateway_ready
            and connections.worker_ready
            and connections.route_ready,
            "telegram_enabled": connections.telegram.enabled,
            "discord_enabled": connections.discord.enabled,
            "external_delivery_verified": False,
            "daily_research_allowance": connections.daily_research_allowance,
            "research_used_today": connections.research_used_today,
        },
        "as_of": now().isoformat(),
        "counts": {
            key: db.scalar(
                select(func.count()).select_from(model).where(model.owner_id == owner.id)
            )
            for key, model in [
                ("paper_accounts", PaperAccount),
                ("strategy_versions", StrategyVersion),
                ("scanner_candidates", ScannerCandidate),
            ]
        },
        "recent_native_jobs": [
            {
                "id": row.id,
                "instrument_id": row.instrument_id,
                "status": row.status,
                "created_at": row.created_at.isoformat(),
            }
            for row in jobs
        ],
        "original_reports": [
            {"id": row.id, "job_id": row.job_id, "checksum": row.checksum} for row in reports
        ],
        "capabilities": [
            "owner web chat",
            "owner Telegram/Discord DMs when configured",
            "crypto, Forex, stock and ETF scanners",
            "scanner and candidate management",
            "strategy version management and retrospective backtests",
            "saved quant research notes",
            "code upgrade proposals for owner review",
            "native daily research",
            "paper trading",
            "paper governance",
        ],
    }


def service_owner(request, db):
    try:
        value = json.loads(Path(runtime_config().openclaw_private_file).read_text())
        token = value["platform_token"]
        if not isinstance(token, str) or len(token) < 40:
            raise ValueError("Invalid private service token")
    except (OSError, ValueError, KeyError):
        raise HTTPException(503, "OpenClaw service access is not configured.") from None
    supplied = request.headers.get("authorization", "")
    if not secrets.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise HTTPException(401, "Service authentication required.")
    owner = db.scalar(select(Owner))
    if not owner:
        raise HTTPException(503, "The platform owner has not been created.")
    return owner


@router.get("/proposals/{proposal_id}")
def context(proposal_id: str, request: Request, db: Db):
    owner = service_owner(request, db)
    prop = paper.record(db, owner, proposal_id, "proposal")
    return governance.proposal_context(db, owner, prop)


@router.get("/runtime-config")
def runtime(request: Request, db: Db):
    return governance.routing(db, service_owner(request, db))


@router.post("/heartbeat", status_code=204)
def heartbeat(body: GatewayHeartbeat, request: Request, db: Db):
    from sqlalchemy.dialects.postgresql import insert

    owner = service_owner(request, db)
    if governance.routing(db, owner)["fingerprint"] != body.fingerprint:
        raise HTTPException(409, "Model configuration changed.")
    db.execute(
        insert(GatewayStatus)
        .values(
            id="openclaw",
            fingerprint=body.fingerprint,
            heartbeat_at=now(),
            capabilities=body.capabilities,
        )
        .on_conflict_do_update(
            index_elements=["id"],
            set_={
                "fingerprint": body.fingerprint,
                "heartbeat_at": now(),
                "capabilities": body.capabilities,
            },
        )
    )
    db.commit()


@router.get("/jobs/{job_id}/context")
def job_context(job_id: str, request: Request, db: Db):
    owner = service_owner(request, db)
    job = db.scalar(select(GovernanceJob).where(GovernanceJob.id == job_id).with_for_update())
    if not job or job.owner_id != owner.id or job.status != "running":
        raise HTTPException(404, "Active review context is unavailable.")
    governance.valid_context(db, job)
    job.context_read_at = now()
    db.commit()
    return {"job_id": job.id, "context_checksum": job.checksum, "context": job.context}


@router.get("/broker-jobs/{job_id}/context")
def broker_job_context(job_id: str, request: Request, db: Db):
    from . import broker_review

    owner = service_owner(request, db)
    job = broker_review.checked(db, owner, job_id, lock=True)
    if job.status != "running":
        raise HTTPException(404, "Active broker review context is unavailable.")
    broker_review.valid_context(db, job)
    job.context_read_at = now()
    db.commit()
    return {"job_id": job.id, "context_checksum": job.checksum, "context": job.context}
