"""Durable owner chat; external DMs use the same restricted OpenClaw agent."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from . import governance, services
from .chat_contracts import ChatConfig, ChatConfigView, ChatTurnView
from .model_profiles import encryption
from .models import (
    AnalysisJob,
    ChatConfigVersion,
    ChatResearch,
    ChatTurn,
    GatewayStatus,
    WorkerStatus,
    identifier,
    now,
)

LOCK = 913570050


def load_config(db):
    row = db.scalar(select(ChatConfigVersion).order_by(ChatConfigVersion.revision.desc()).limit(1))
    if not row:
        return ChatConfig(), 0
    try:
        return ChatConfig.model_validate_json(
            encryption().decrypt(row.encrypted_config.encode())
        ), row.revision
    except Exception:
        raise HTTPException(
            503, "Chat settings cannot be unlocked. Restore the host encryption key."
        ) from None


def config_view(db, owner):
    from .chat_research import charged_today

    value, revision = load_config(db)
    try:
        encryption()
        storage_ready = True
    except (OSError, ValueError):
        storage_ready = False
    runtime = governance.routing(db, owner)
    gateway, worker = db.get(GatewayStatus, "openclaw"), db.get(WorkerStatus, "chat")
    gateway_ready = bool(
        gateway
        and gateway.fingerprint == runtime["fingerprint"]
        and gateway.heartbeat_at > now() - timedelta(seconds=30)
    )
    route_ready = value.profile in runtime["routes"]
    return ChatConfigView(
        revision=revision,
        profile=value.profile,
        daily_research_allowance=value.daily_research_allowance,
        research_used_today=charged_today(db, owner),
        storage_ready=storage_ready,
        gateway_ready=gateway_ready,
        route_ready=route_ready,
        worker_ready=bool(worker and worker.heartbeat_at > now() - timedelta(seconds=45)),
        **{
            key: {
                "enabled": channel.enabled,
                "owner_id": channel.owner_id,
                "has_token": bool(channel.token),
                "detail": "Configured for owner DMs; delivery has not been verified. Send your bot a message after saving."
                if channel.enabled
                else "Disabled; add your bot token and numeric user ID to enable.",
            }
            for key in ("telegram", "discord")
            if (channel := getattr(value, key))
        },
    )


def save_config(db, owner, body):
    db.execute(select(func.pg_advisory_xact_lock(LOCK)))
    current, revision = load_config(db)
    if body.expected_revision != revision:
        raise HTTPException(409, "Chat settings changed in another tab. Reload them.")
    data = {"profile": body.profile, "daily_research_allowance": body.daily_research_allowance}
    for key in ("telegram", "discord"):
        change, old = getattr(body, key), getattr(current, key)
        if change.token_action != "replace" and change.token:
            raise HTTPException(422, "Choose Replace token to enter a new bot token.")
        token = (
            old.token
            if change.token_action == "keep"
            else change.token.strip()
            if change.token_action == "replace"
            else ""
        )
        if change.token_action == "replace" and not token:
            raise HTTPException(422, "Enter the new bot token.")
        if change.enabled and (not token or not change.owner_id):
            raise HTTPException(422, "An enabled bot needs its token and your numeric user ID.")
        data[key] = {"enabled": change.enabled, "owner_id": change.owner_id, "token": token}
    try:
        value = ChatConfig.model_validate(data)
    except ValueError:
        raise HTTPException(422, "Use a positive numeric user ID for each channel.") from None
    try:
        encrypted = encryption().encrypt(value.model_dump_json().encode()).decode()
    except (OSError, ValueError):
        raise HTTPException(503, "Chat settings need the host encryption key.") from None
    db.add(ChatConfigVersion(owner_id=owner.id, revision=revision + 1, encrypted_config=encrypted))
    services.audit(
        db,
        owner,
        "chat.config_changed",
        "chat",
        "channels",
        "Owner saved chat connections",
        {
            "revision": revision + 1,
            "profile": value.profile,
            **{
                k: {
                    "enabled": getattr(value, k).enabled,
                    "token_action": getattr(body, k).token_action,
                }
                for k in ("telegram", "discord")
            },
        },
    )
    db.commit()
    return config_view(db, owner)


def view(db, turn):
    from . import analysis, portfolios
    from .models import PortfolioActionReceipt

    value = ChatTurnView.model_validate(turn, from_attributes=True)
    job_ids = (
        select(ChatResearch.job_id)
        .where(ChatResearch.turn_id == turn.id, ChatResearch.owner_id == turn.owner_id)
        .distinct()
    )
    jobs = list(
        db.scalars(
            select(AnalysisJob).where(AnalysisJob.id.in_(job_ids)).order_by(AnalysisJob.created_at)
        )
    )
    value.research_jobs = [analysis.view(db, row) for row in jobs]
    value.action_receipts = [
        portfolios.receipt_view(row)
        for row in db.scalars(
            select(PortfolioActionReceipt)
            .where(PortfolioActionReceipt.turn_id == turn.id)
            .order_by(PortfolioActionReceipt.created_at)
        )
    ]
    return value


def enqueue(db, owner, body):
    from . import portfolios

    db.execute(select(func.pg_advisory_xact_lock(LOCK)))
    previous = db.scalar(
        select(ChatTurn).where(
            ChatTurn.owner_id == owner.id, ChatTurn.request_id == str(body.request_id)
        )
    )
    if previous:
        if (
            previous.conversation_id != str(body.conversation_id)
            or previous.user_text != body.text
            or previous.portfolio_id != (str(body.portfolio_id) if body.portfolio_id else None)
        ):
            raise HTTPException(409, "This message identifier already belongs to different text.")
        return view(db, previous)
    state = config_view(db, owner)
    if not (state.gateway_ready and state.worker_ready and state.route_ready):
        raise HTTPException(
            503, "OpenClaw chat is not ready. Check Chat connections and the running services."
        )
    busy = list(db.scalars(select(ChatTurn).where(ChatTurn.status.in_(["queued", "running"]))))
    if len(busy) >= 4:
        raise HTTPException(429, "The chat queue is full. Wait for the current replies.")
    if any(t.owner_id == owner.id and t.conversation_id == str(body.conversation_id) for t in busy):
        raise HTTPException(
            409, "Wait for the reply in this conversation before sending another message."
        )
    portfolios.bind(db, owner, body.conversation_id, body.portfolio_id)
    row = ChatTurn(
        id=identifier(),
        owner_id=owner.id,
        request_id=str(body.request_id),
        conversation_id=str(body.conversation_id),
        portfolio_id=str(body.portfolio_id) if body.portfolio_id else None,
        user_text=body.text,
        profile=state.profile,
        routing_fingerprint=governance.routing(db, owner)["fingerprint"],
    )
    db.add(row)
    services.audit(
        db,
        owner,
        "chat.message_queued",
        "chat",
        row.id,
        "Owner requested an OpenClaw reply",
        {"conversation_id": row.conversation_id, "profile": row.profile},
    )
    db.commit()
    return view(db, row)


def heartbeat(db):
    db.execute(
        insert(WorkerStatus)
        .values(id="chat", heartbeat_at=now(), version="2026.9.7")
        .on_conflict_do_update(
            index_elements=["id"], set_={"heartbeat_at": now(), "version": "2026.9.7"}
        )
    )
    db.commit()


def claim(db):
    db.execute(select(func.pg_advisory_xact_lock(LOCK)))
    for row in db.scalars(
        select(ChatTurn)
        .where(ChatTurn.status == "running", ChatTurn.heartbeat_at < now() - timedelta(seconds=45))
        .with_for_update()
    ):
        row.status, row.error, row.finished_at = (
            "interrupted",
            "Chat worker stopped responding. No automatic retry was made.",
            now(),
        )
    if db.scalar(select(ChatTurn.id).where(ChatTurn.status == "running").limit(1)):
        db.commit()
        return None
    row = db.scalar(
        select(ChatTurn)
        .where(ChatTurn.status == "queued")
        .order_by(ChatTurn.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row:
        row.status, row.lease_id, row.heartbeat_at = "running", identifier(), now()
    db.commit()
    return (row.id, row.lease_id) if row else None


def renew(db, turn_id, lease):
    row = db.scalar(select(ChatTurn).where(ChatTurn.id == turn_id).with_for_update())
    if (
        not row
        or row.status != "running"
        or row.lease_id != lease
        or row.heartbeat_at < now() - timedelta(seconds=45)
    ):
        return False
    row.heartbeat_at = now()
    db.commit()
    return True


def finish(db, turn_id, lease, result=None, error=None):
    row = db.scalar(select(ChatTurn).where(ChatTurn.id == turn_id).with_for_update())
    if (
        not row
        or row.status != "running"
        or row.lease_id != lease
        or row.heartbeat_at < now() - timedelta(seconds=45)
    ):
        return False
    row.status, row.finished_at, row.error = "failed" if error else "succeeded", now(), error
    if result:
        row.assistant_text, row.usage = result
    db.commit()
    return True
