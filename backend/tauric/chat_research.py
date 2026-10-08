"""Owner-bound, budgeted research dispatch; unchanged native queue and reports."""

import hashlib
import re
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import case, func, select

from . import analysis, chat, market, native_scope, paper, services
from .market_contracts import AnalysisRequest
from .model_profiles import connection_head, load_profiles
from .models import AnalysisArtifact, AnalysisJob, ChatResearch, ChatTurn, Instrument, now

LOCK = 913570051


def capabilities(db):
    """Fresh registry-backed facts; readiness and permissions are checked at admission."""
    records = list(
        db.scalars(
            select(Instrument)
            .order_by(case((Instrument.id.like("oanda:fx:%"), 0), else_=1), Instrument.id)
            .limit(200)
        )
    )
    instruments = []
    for record in records:
        spec = market.instrument(db, record.id)
        daily = native_scope.supported(spec)
        instruments.append(
            {
                "id": spec.id,
                "symbol": spec.symbol,
                "asset_type": spec.asset_type,
                "daily_native_research": daily,
                "chat_request_enabled": daily,
                "scope": spec.analysis_scope,
            }
        )
    total = db.scalar(select(func.count()).select_from(Instrument))
    return {
        "scope": "Current daily research for supported registered crypto, stocks, ETFs and Forex. Chat can request crypto, stock, ETF and provider-verified Forex daily runs. Forex policy/economic coverage varies by currency; missing sources stay explicit. Owner authorization, connections, readiness and allowances still apply.",
        "forex_policy_currencies": sorted(native_scope.FX_POLICY_CURRENCIES),
        "unsupported_scopes": ["commodities", "weekly", "historical", "intraday", "futures"],
        "market_information": "Fresh candles and chat interpretation are separate from native research. Use tauric_market_data for registered assets or account-verified XAU/USD gold; default intervals are 15m and 1h. No native report, strategy qualification or execution approval is created by an observation.",
        "registered_instruments": instruments,
        "registered_count": total,
        "registry_truncated": total > len(instruments),
        "find_more_symbols": "Use Find more symbols in the chart, scanner or portfolio research. Register supported OANDA FX or Alpaca stock/ETF symbols before requesting research.",
    }


def charged_today(db, owner):
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count())
        .select_from(ChatResearch)
        .where(
            ChatResearch.owner_id == owner.id,
            ChatResearch.charged.is_(True),
            ChatResearch.created_at >= start,
        )
    )


def authorize(db, owner, body, config):
    """Context comes from the pinned plugin factory, never the model's arguments."""
    if not body.sender_is_owner or body.account_id != "default":
        raise HTTPException(403, "Research tools are available only to the configured owner.")
    if body.channel == "webchat":
        prefix = "agent:tauric-chat:openai-user:tauric-web-" + owner.id + "-"
        if not body.session_key.startswith(prefix):
            raise HTTPException(403, "This web conversation is not bound to the owner.")
        conversation = body.session_key[len(prefix) :]
        if not re.fullmatch(r"[a-f0-9-]{36}", conversation):
            raise HTTPException(403, "This web conversation is not bound to the owner.")
        turn = db.scalar(
            select(ChatTurn)
            .where(
                ChatTurn.owner_id == owner.id,
                ChatTurn.conversation_id == conversation,
                ChatTurn.status == "running",
            )
            .with_for_update()
        )
        if not turn or turn.heartbeat_at < now() - timedelta(seconds=45):
            raise HTTPException(409, "The requesting chat turn is no longer active.")
        return turn
    channel = getattr(config, body.channel)
    sender = body.sender_id.removeprefix(body.channel + ":")
    # DMs must use the pinned per-account-channel-peer session. No groups/cron/agents.
    expected = f"agent:tauric-chat:{body.channel}:default:direct:{channel.owner_id}"
    if not channel.enabled or sender != channel.owner_id or body.session_key != expected:
        raise HTTPException(
            403, "Research tools are limited to the configured owner's direct messages."
        )
    return None


def status(db, owner, job_id):
    job = analysis.get_job(db, owner, job_id)
    artifact = db.scalar(
        select(AnalysisArtifact).where(
            AnalysisArtifact.job_id == job.id, AnalysisArtifact.owner_id == owner.id
        )
    )
    return {
        "job": analysis.view(db, job).model_dump(mode="json"),
        "scope": "current_daily_research",
        "execution_authority": False,
        "original_report": {
            "id": artifact.id,
            "checksum": artifact.checksum,
            "report_fields": list(artifact.payload.get("reports", {})),
        }
        if artifact
        else None,
        "detail": "Original report is available. Open it in Tauric or read it with tauric_chat_context."
        if artifact
        else "No automatic retry. Ask again for status; do not wait inside a tool call.",
    }


def request(db, owner, body):
    db.execute(select(func.pg_advisory_xact_lock(LOCK)))
    # Shares the config lock; disabling a bot/budget cannot race admission.
    db.execute(select(func.pg_advisory_xact_lock(chat.LOCK)))
    config, revision = chat.load_config(db)
    turn = authorize(db, owner, body, config)
    spec = market.instrument(db, body.instrument_id)
    if not native_scope.supported(spec):
        raise HTTPException(
            422, "Current daily native research is not enabled for this instrument."
        )
    if turn and turn.portfolio_id:
        from . import portfolios
        from .models import PaperDeployment

        portfolio = portfolios.view(db, owner, turn.portfolio_id)
        instruments = {
            db.get(PaperDeployment, key).instrument_id
            for key in portfolios.deployment_ids(db, owner, portfolio)
        }
        if body.instrument_id not in instruments:
            raise HTTPException(
                403, "Research must concern an instrument deployed in this portfolio."
            )
    request_key = hashlib.sha256((body.session_key + "\0" + body.tool_call_id).encode()).hexdigest()
    old = db.scalar(
        select(ChatResearch).where(
            ChatResearch.owner_id == owner.id, ChatResearch.request_key == request_key
        )
    )
    if old:
        if old.payload["instrument_id"] != body.instrument_id:
            raise HTTPException(409, "This tool call already requested a different instrument.")
        return {**status(db, owner, old.job_id), "reused": True}
    profile = config.profile
    db.execute(select(func.pg_advisory_xact_lock(913570030 if profile == "cloud" else 913570031)))
    for key in sorted(["max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds"]):
        for scope in ("global", "user"):
            services.setting_head(db, owner, "analysis." + key, scope, lock=True)
    profiles, _ = load_profiles(db)
    model = profiles.get(profile)
    if not model or not model.configured():
        raise HTTPException(
            422, "Configure the chat AI connection before requesting daily research."
        )
    connection = connection_head(db, profile)
    settings = {
        key: services.setting_view(db, owner, "analysis." + key).effective_value
        for key in ["max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds"]
    }
    evidence = {
        "instrument_id": body.instrument_id,
        "trade_date": now().date().isoformat(),
        "profile": profile,
        "profile_fingerprint": model.fingerprint(),
        "connection_revision": connection.revision if connection else 0,
        "settings": settings,
        "upstream_revision": analysis.UPSTREAM_REVISION,
    }
    research_key = paper.checksum(evidence)
    from .research_cache import compatible_daily

    job = compatible_daily(db, owner, body.instrument_id, profile)
    reused = bool(job)
    if not reused:
        if charged_today(db, owner) >= config.daily_research_allowance:
            raise HTTPException(
                429,
                "Chat has used its daily research allowance. Change it in Settings → Chat connections, or inspect an existing run.",
            )
        result = analysis.enqueue(
            db,
            owner,
            AnalysisRequest(
                request_id=uuid5(
                    NAMESPACE_URL, "tauric-chat-research:" + owner.id + ":" + request_key
                ),
                instrument_id=body.instrument_id,
                profile=profile,
            ),
            commit=False,
            chat_context={
                "connection_revision": evidence["connection_revision"],
                "channel": body.channel,
                "turn_id": turn.id if turn else None,
                "purpose": "daily_research_only",
            },
        )
        job = db.get(AnalysisJob, result.id)
    payload = {
        "instrument_id": body.instrument_id,
        "evidence": evidence,
        "config_revision": revision,
        "session_hash": hashlib.sha256(body.session_key.encode()).hexdigest(),
        "execution_authority": False,
    }
    row = ChatResearch(
        owner_id=owner.id,
        turn_id=turn.id if turn else None,
        job_id=job.id,
        channel=body.channel,
        request_key=request_key,
        research_key=research_key,
        charged=not bool(reused),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    services.audit(
        db,
        owner,
        "chat.research_requested",
        "analysis",
        job.id,
        "Owner requested current daily research through chat",
        {
            "channel": body.channel,
            "turn_id": row.turn_id,
            "reused": bool(reused),
            "execution_authority": False,
        },
    )
    db.commit()
    return {**status(db, owner, job.id), "reused": bool(reused)}


def validate_admission(db, job):
    context = job.request.get("chat_context")
    if not context:
        return
    saved = connection_head(db, job.profile)
    if context["connection_revision"] != (saved.revision if saved else 0):
        raise ValueError("Chat research connection changed before execution")
