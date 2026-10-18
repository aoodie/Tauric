"""Owner-managed, encrypted market credentials with redacted public views."""

import json
from datetime import datetime
from typing import Literal

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import func, select

from . import services
from .auth import CurrentOwner, Db
from .data_providers import AlpacaConnection, Connections, OandaConnection, host_connections
from .data_quality import freshness, verify
from .model_profiles import encryption
from .models import AuditEvent, MarketConnectionVersion, identifier, now
from .schemas import Contract

router = APIRouter()
Provider = Literal["oanda", "alpaca"]
Environment = Literal["practice", "paper", "live"]


class MarketConnectionView(Contract):
    id: Provider
    account_id: str | None = None
    environment: Environment
    feed: Literal["iex", "sip"] | None = None
    delay_minutes: int | None = None
    configured: bool
    revision: int
    source: Literal["host", "settings", "empty"]
    storage_ready: bool
    last_check: "MarketConnectionCheckView | None" = None


class MarketConnectionChange(Contract):
    environment: Environment
    account_id: str | None = Field(default=None, max_length=128, pattern=r"^$|^[0-9]+(?:-[0-9]+)*$")
    feed: Literal["iex", "sip"] = "iex"
    delay_minutes: int = Field(default=15, ge=0, le=15, strict=True)
    credential_action: Literal["keep", "replace", "clear"]
    token: str = Field(default="", max_length=4096)
    key: str = Field(default="", max_length=4096)
    secret: str = Field(default="", max_length=4096)
    expected_revision: int = Field(ge=0, strict=True)


class MarketConnectionCheck(Contract):
    expected_revision: int = Field(ge=0, strict=True)


class MarketConnectionCheckView(Contract):
    provider: Provider
    revision: int
    checked_at: datetime
    symbol: str
    interval: Literal["15m"] = "15m"
    candles: int
    account_checked: bool
    feed: str
    quality: Literal["verified", "gaps", "outdated"]
    missing_bars: int
    warnings: list[str]


@router.post("/api/v1/market-connections/{provider}/test", response_model=MarketConnectionCheckView)
def check_connection(provider: Provider, body: MarketConnectionCheck, db: Db, owner: CurrentOwner):
    from . import market

    if not db.scalar(
        select(func.pg_try_advisory_xact_lock(913570032 if provider == "oanda" else 913570033))
    ):
        raise HTTPException(409, "This connection is being saved or tested. Try again shortly.")
    value, revision, _ = stored_connection(db, provider)
    if revision != body.expected_revision:
        raise HTTPException(409, "This connection changed in another tab. Reload its settings.")
    config = Connections(**{provider: value})
    instrument_id = "oanda:fx:EUR_USD" if provider == "oanda" else "alpaca:equity:AAPL"
    spec = market.instrument(db, instrument_id)
    try:
        candles, as_of, received, context = market.PROVIDERS[spec.provider].collect(
            spec, "15m", config=config
        )
        quality = verify(candles, "15m", context, as_of)
        if not freshness(candles, "15m", context, as_of):
            quality.status = "outdated"
            quality.warnings.append("The provider omitted recent expected trading bars.")
    except HTTPException as error:
        # Provider exceptions are sanitized by the adapter; audit only the status code.
        services.audit(
            db,
            owner,
            "market_connection.check_failed",
            "market_connection",
            provider,
            "Market-data connection check failed",
            {"revision": revision, "status_code": error.status_code},
        )
        db.commit()
        raise
    except (ValueError, ArithmeticError, TypeError, KeyError):
        services.audit(
            db,
            owner,
            "market_connection.check_failed",
            "market_connection",
            provider,
            "Market-data sample failed verification",
            {"revision": revision, "status_code": 502},
        )
        db.commit()
        raise HTTPException(
            502, "Access responded, but the market-data sample failed quality checks."
        ) from None
    result = MarketConnectionCheckView(
        provider=provider,
        revision=revision,
        checked_at=now(),
        symbol=spec.symbol,
        candles=len(candles),
        account_checked=provider == "oanda" and bool(value.account_id),
        feed=value.feed if provider == "alpaca" else "midpoint / bid / ask",
        quality=quality.status,
        missing_bars=quality.missing_bars,
        warnings=quality.warnings,
    )
    services.audit(
        db,
        owner,
        "market_connection.checked",
        "market_connection",
        provider,
        "Market-data access checked",
        result.model_dump(mode="json"),
    )
    db.commit()
    return result


def stored_connection(db, provider):
    row = db.scalar(
        select(MarketConnectionVersion)
        .where(MarketConnectionVersion.provider == provider)
        .order_by(MarketConnectionVersion.revision.desc())
        .limit(1)
    )
    cls = OandaConnection if provider == "oanda" else AlpacaConnection
    if row:
        try:
            value = cls.model_validate_json(encryption().decrypt(row.encrypted_config.encode()))
        except (OSError, ValueError, InvalidToken):
            raise HTTPException(
                503,
                "Saved market connections cannot be unlocked. Restore the matching host encryption key.",
            ) from None
        return value, row.revision, "settings"
    value = getattr(host_connections(), provider)
    configured = bool(
        value.token.get_secret_value()
        if provider == "oanda"
        else value.key.get_secret_value() and value.secret.get_secret_value()
    )
    return value, 0, "host" if configured else "empty"


def load_connections(db):
    return Connections(
        oanda=stored_connection(db, "oanda")[0],
        alpaca=stored_connection(db, "alpaca")[0],
    )


def connection_view(db, provider):
    value, revision, source = stored_connection(db, provider)
    try:
        encryption()
        ready = True
    except (OSError, ValueError):
        ready = False
    last_check = None
    if revision:
        event = db.scalar(
            select(AuditEvent)
            .where(
                AuditEvent.resource_type == "market_connection",
                AuditEvent.resource_id == provider,
                AuditEvent.action.in_(
                    ["market_connection.checked", "market_connection.check_failed"]
                ),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(1)
        )
        if (
            event
            and event.action == "market_connection.checked"
            and event.details.get("revision") == revision
        ):
            last_check = MarketConnectionCheckView.model_validate(event.details)
    return MarketConnectionView(
        id=provider,
        environment=value.environment,
        account_id=value.account_id if provider == "oanda" else None,
        feed=value.feed if provider == "alpaca" else None,
        delay_minutes=value.delay_minutes if provider == "alpaca" else None,
        configured=bool(
            value.token.get_secret_value()
            if provider == "oanda"
            else value.key.get_secret_value() and value.secret.get_secret_value()
        ),
        revision=revision,
        source=source,
        storage_ready=ready,
        last_check=last_check,
    )


@router.get("/api/v1/market-connections", response_model=list[MarketConnectionView])
def list_connections(db: Db, owner: CurrentOwner):
    return [connection_view(db, provider) for provider in ("oanda", "alpaca")]


@router.put("/api/v1/market-connections/{provider}", response_model=MarketConnectionView)
def save_connection(provider: Provider, body: MarketConnectionChange, db: Db, owner: CurrentOwner):
    db.execute(select(func.pg_advisory_xact_lock(913570032 if provider == "oanda" else 913570033)))
    current, revision, _ = stored_connection(db, provider)
    if revision != body.expected_revision:
        raise HTTPException(409, "This connection changed in another tab. Reload its settings.")
    from .broker_models import BrokerCloseRequest, BrokerOrderIntent, BrokerPositionCursor
    from .models import Owner

    # Entry admission uses this owner lock; account changes cannot cross an in-flight mutation.
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    authority_changed = (
        body.environment != current.environment
        or (
            provider == "oanda"
            and body.account_id is not None
            and body.account_id != current.account_id
        )
        or body.credential_action == "clear"
    )
    pending_fingerprints = set()
    if authority_changed or body.credential_action == "replace":
        pending_fingerprints.update(
            db.scalars(
                select(BrokerOrderIntent.account_fingerprint).where(
                    BrokerOrderIntent.owner_id == owner.id,
                    BrokerOrderIntent.provider == provider,
                    BrokerOrderIntent.status.not_in(["filled", "cancelled", "rejected", "expired"]),
                )
            )
        )
        pending_fingerprints.update(
            db.scalars(
                select(BrokerOrderIntent.account_fingerprint)
                .select_from(BrokerPositionCursor)
                .join(
                    BrokerOrderIntent, BrokerOrderIntent.id == BrokerPositionCursor.entry_intent_id
                )
                .where(
                    BrokerOrderIntent.owner_id == owner.id,
                    BrokerOrderIntent.provider == provider,
                    BrokerPositionCursor.status.not_in(["closed", "unfilled"]),
                )
            )
        )
        pending_fingerprints.update(
            db.scalars(
                select(BrokerOrderIntent.account_fingerprint)
                .select_from(BrokerCloseRequest)
                .join(BrokerOrderIntent, BrokerOrderIntent.id == BrokerCloseRequest.entry_intent_id)
                .where(
                    BrokerOrderIntent.owner_id == owner.id,
                    BrokerOrderIntent.provider == provider,
                    BrokerCloseRequest.status.not_in(["filled", "rejected"]),
                )
            )
        )
        if pending_fingerprints:
            if authority_changed:
                raise HTTPException(
                    409,
                    "Reconcile and close attributed positions and orders before switching accounts. Position monitoring needs the original connection.",
                )
    if body.environment not in (("practice", "live") if provider == "oanda" else ("paper", "live")):
        raise HTTPException(422, "Choose a valid environment for this provider.")
    if provider != "oanda" and body.account_id:
        raise HTTPException(422, "An OANDA account ID can only be saved for OANDA.")
    names = ("token",) if provider == "oanda" else ("key", "secret")
    if any(getattr(body, name) for name in {"token", "key", "secret"} - set(names)):
        raise HTTPException(422, "Supply credentials only for the selected provider.")
    supplied = {name: getattr(body, name) for name in names}
    if body.credential_action != "replace" and any(supplied.values()):
        raise HTTPException(422, "Choose Replace credentials to supply new credentials.")
    if body.credential_action == "replace" and any(
        not value.strip() or any(char.isspace() for char in value) for value in supplied.values()
    ):
        raise HTTPException(422, "Enter all required credentials without spaces or line breaks.")
    if (
        body.credential_action == "keep"
        and current.environment != body.environment
        and any(getattr(current, name).get_secret_value() for name in names)
    ):
        raise HTTPException(422, "Replace or clear credentials when changing environment.")
    secrets = {
        name: getattr(current, name).get_secret_value()
        if body.credential_action == "keep"
        else supplied[name]
        if body.credential_action == "replace"
        else ""
        for name in names
    }
    payload = {"environment": body.environment, **secrets}
    if provider == "oanda":
        payload["account_id"] = (
            ""
            if body.credential_action == "clear"
            else current.account_id
            if body.account_id is None
            else body.account_id
        )
    if provider == "alpaca":
        payload.update(feed=body.feed, delay_minutes=body.delay_minutes)
    if pending_fingerprints:
        from . import broker_execution
        from .broker_adapters import BrokerError

        proposed = (OandaConnection if provider == "oanda" else AlpacaConnection).model_validate(
            payload
        )
        try:
            with broker_execution.adapter(provider, proposed) as broker:
                if {broker.account().account_fingerprint} != pending_fingerprints:
                    raise BrokerError("Replacement credentials identify a different account.")
        except BrokerError:
            raise HTTPException(
                409,
                "Active positions require replacement credentials for the same verified account. Resolve positions before selecting a different account.",
            ) from None
    try:
        encrypted = encryption().encrypt(json.dumps(payload).encode()).decode()
    except (OSError, ValueError):
        raise HTTPException(
            503, "Market settings cannot be saved until the host encryption key is available."
        ) from None
    db.add(
        MarketConnectionVersion(
            owner_id=owner.id,
            provider=provider,
            revision=revision + 1,
            encrypted_config=encrypted,
            id=identifier(),
        )
    )
    services.audit(
        db,
        owner,
        "market_connection.changed",
        "market_connection",
        provider,
        "Owner saved market-data connection",
        {"revision": revision + 1, "credential_action": body.credential_action},
    )
    db.commit()
    return connection_view(db, provider)
