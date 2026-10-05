"""Encrypted owner-managed macro provider connection; no secret in public views."""

from datetime import datetime
from typing import Literal

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import func, select

from . import market, services
from .auth import CurrentOwner, Db
from .model_profiles import encryption
from .models import AuditEvent, MarketConnectionVersion, now
from .schemas import Contract

router = APIRouter(prefix="/api/v1/forex-data")
PROVIDER = "trading_economics"


class ForexDataConnection(Contract):
    configured: bool
    revision: int
    checked_at: datetime | None = None


class ForexDataChange(Contract):
    expected_revision: int = Field(ge=0, strict=True)
    key_action: Literal["keep", "replace", "clear"]
    api_key: str = Field(default="", max_length=4096)


class ForexDataCheck(Contract):
    expected_revision: int = Field(ge=0, strict=True)


def head(db):
    return db.scalar(
        select(MarketConnectionVersion)
        .where(MarketConnectionVersion.provider == PROVIDER)
        .order_by(MarketConnectionVersion.revision.desc())
        .limit(1)
    )


def saved(db):
    row = head(db)
    if not row:
        return "", 0
    try:
        return encryption().decrypt(row.encrypted_config.encode()).decode(), row.revision
    except (InvalidToken, ValueError, OSError):
        raise HTTPException(
            503, "Restore the matching private encryption key to unlock the macro connection."
        ) from None


def view(db):
    key, revision = saved(db)
    event = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.resource_type == "forex_data_connection",
            AuditEvent.resource_id == PROVIDER,
            AuditEvent.action.in_(["forex_data.checked", "forex_data.check_failed"]),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    checked = (
        event.created_at
        if event
        and event.action == "forex_data.checked"
        and event.details.get("revision") == revision
        else None
    )
    return ForexDataConnection(configured=bool(key), revision=revision, checked_at=checked)


def lock(db):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(913570088))):
        raise HTTPException(409, "The macro connection is being saved or tested. Retry shortly.")


@router.get("/connection", response_model=ForexDataConnection)
def get_connection(db: Db, owner: CurrentOwner):
    return view(db)


@router.put("/connection", response_model=ForexDataConnection)
def save_connection(body: ForexDataChange, db: Db, owner: CurrentOwner):
    lock(db)
    key, revision = saved(db)
    if body.expected_revision != revision:
        raise HTTPException(409, "Connection changed in another window. Reload before saving.")
    if body.key_action == "replace":
        if not body.api_key.strip() or any(ord(c) < 33 or ord(c) > 126 for c in body.api_key):
            raise HTTPException(422, "Enter a non-empty provider API key without whitespace.")
        key = body.api_key
    elif body.api_key:
        raise HTTPException(422, "Select Replace key to save a new credential.")
    elif body.key_action == "clear":
        key = ""
    db.add(
        MarketConnectionVersion(
            owner_id=owner.id,
            provider=PROVIDER,
            revision=revision + 1,
            encrypted_config=encryption().encrypt(key.encode()).decode(),
        )
    )
    services.audit(
        db,
        owner,
        "forex_data.saved",
        "forex_data_connection",
        PROVIDER,
        "Owner changed encrypted macro provider connection",
        {"revision": revision + 1, "configured": bool(key)},
    )
    db.commit()
    return view(db)


@router.post("/test", response_model=dict)
def test_connection(body: ForexDataCheck, db: Db, owner: CurrentOwner):
    from .forex_sources import collect

    lock(db)
    key, revision = saved(db)
    if revision != body.expected_revision:
        raise HTTPException(409, "Connection changed. Reload before testing.")
    if not key:
        raise HTTPException(422, "Save a macro provider API key before testing.")
    snapshot = collect(db, market.instrument(db, "oanda:fx:EUR_USD"))
    sources = snapshot["data"]["sources"][:4]
    ready = all(s["status"] == "available" for s in sources)
    services.audit(
        db,
        owner,
        "forex_data.checked" if ready else "forex_data.check_failed",
        "forex_data_connection",
        PROVIDER,
        "Read-only calendar and policy benchmark sample check",
        {
            "revision": revision,
            "available_sources": sum(s["status"] == "available" for s in sources),
        },
    )
    db.commit()
    return {
        "revision": revision,
        "checked_at": now().isoformat(),
        "ready": ready,
        "sources": sources,
    }
