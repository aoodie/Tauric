"""Owner-initiated read-only OAuth. Tokens and PKCE verifiers remain encrypted."""

import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import services
from .auth import COOKIE_NAME, CurrentOwner, Db, digest
from .config import runtime_config
from .model_profiles import encryption
from .models import (
    AuditEvent,
    MarketConnectionVersion,
    Owner,
    OwnerSession,
    TradingViewOAuthFlow,
    now,
)
from .schemas import Contract

router = APIRouter(prefix="/api/v1/tradingview")
PROVIDER = "tradingview"
SERVER = "https://mcp.tradingview.com/mcp"
ISSUER = "https://www.tradingview.com"
AUTHORIZE = ISSUER + "/mcp/oauth/authorize"
TOKEN = ISSUER + "/mcp/oauth/token"
REGISTER = ISSUER + "/mcp/oauth/register"
LOCK = 913570091
SCOPE = "mcp:read"
OAUTH_COOKIE = "tauric_tradingview_oauth"
CALLBACK_PATH = "/api/v1/tradingview/callback"


class TradingViewConnection(Contract):
    configured: bool
    revision: int
    expires_at: datetime | None = None
    checked_at: datetime | None = None
    ready: bool = False
    scope: str = SCOPE


class TradingViewChange(Contract):
    expected_revision: int = Field(ge=0, strict=True)


class TradingViewAuthorization(Contract):
    authorization_url: str
    expires_at: datetime


def request_json(method, url, **kwargs):
    # Endpoints come from fixed constants, never from a user/token response.
    with httpx.Client(timeout=httpx.Timeout(10, connect=5), follow_redirects=False) as client:
        with client.stream(method, url, **kwargs) as response:
            response.raise_for_status()
            length, chunks = 0, []
            for chunk in response.iter_bytes():
                length += len(chunk)
                if length > 128000:
                    raise ValueError("Oversized authentication response")
                chunks.append(chunk)
    value = json.loads(b"".join(chunks))
    if not isinstance(value, dict):
        raise ValueError("Invalid authentication response")
    return value


def metadata():
    resource = request_json(
        "GET", "https://mcp.tradingview.com/.well-known/oauth-protected-resource/mcp"
    )
    auth = request_json("GET", ISSUER + "/.well-known/oauth-authorization-server")
    if (
        resource.get("resource") != SERVER
        or resource.get("authorization_servers") != [ISSUER]
        or auth.get("issuer") != ISSUER
        or auth.get("authorization_endpoint") != AUTHORIZE
        or auth.get("token_endpoint") != TOKEN
        or auth.get("registration_endpoint") != REGISTER
        or "S256" not in auth.get("code_challenge_methods_supported", [])
        or SCOPE not in auth.get("scopes_supported", [])
        or "none" not in auth.get("token_endpoint_auth_methods_supported", [])
    ):
        raise ValueError("Unsupported TradingView authentication configuration")


def head(db):
    return db.scalar(
        select(MarketConnectionVersion)
        .where(MarketConnectionVersion.provider == PROVIDER)
        .order_by(MarketConnectionVersion.revision.desc())
        .limit(1)
    )


def seal(value):
    return encryption().encrypt(json.dumps(value, allow_nan=False).encode()).decode()


def unseal(value):
    try:
        return json.loads(encryption().decrypt(value.encode()))
    except (InvalidToken, ValueError, OSError):
        raise HTTPException(
            503, "Restore the matching encryption key to unlock TradingView."
        ) from None


def saved(db):
    row = head(db)
    return (unseal(row.encrypted_config), row.revision) if row else ({}, 0)


def lock(db):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
        raise HTTPException(409, "TradingView connection is being updated. Retry shortly.")


def append(db, owner_id, revision, config):
    db.add(
        MarketConnectionVersion(
            owner_id=owner_id,
            provider=PROVIDER,
            revision=revision + 1,
            encrypted_config=seal(config),
        )
    )
    db.flush()


def token_config(data, client_id, redirect_uri, previous=None):
    previous = previous or {}
    token, refresh = (
        data.get("access_token"),
        data.get("refresh_token", previous.get("refresh_token")),
    )
    seconds, scope = data.get("expires_in"), data.get("scope", SCOPE)
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 16000
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
        or not isinstance(refresh, str)
        or not 1 <= len(refresh) <= 16000
        or any(ord(c) < 33 or ord(c) > 126 for c in refresh)
        or type(seconds) is not int
        or not 60 <= seconds <= 86400
        or str(data.get("token_type", "")).lower() != "bearer"
        or scope != SCOPE
    ):
        raise ValueError("Invalid or excessive token grant")
    return {
        "access_token": token,
        "refresh_token": refresh,
        "expires_at": (now() + timedelta(seconds=seconds)).isoformat(),
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scope,
    }


def credentials(db):
    # Refresh in its own transaction: research admission may later roll back.
    with Session(db.get_bind()) as session:
        config, revision = saved(session)
        if not config.get("access_token"):
            return {}, revision
        expiry = datetime.fromisoformat(config["expires_at"])
        if expiry > now() + timedelta(seconds=60):
            return config, revision
        lock(session)
        config, revision = saved(session)
        if datetime.fromisoformat(config["expires_at"]) > now() + timedelta(seconds=60):
            return config, revision
        row = head(session)
        try:
            data = request_json(
                "POST",
                TOKEN,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": config["refresh_token"],
                    "client_id": config["client_id"],
                    "resource": SERVER,
                },
            )
            updated = token_config(data, config["client_id"], config["redirect_uri"], config)
        except Exception:
            raise HTTPException(
                503, "TradingView sign-in expired or could not refresh. Reconnect in Settings."
            ) from None
        append(session, row.owner_id, revision, updated)
        session.commit()
        return updated, revision + 1


def view(db):
    config, revision = saved(db)
    event = db.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.resource_type == "tradingview_connection",
            AuditEvent.action.in_(["tradingview.checked", "tradingview.check_failed"]),
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(1)
    )
    checked = (
        event.created_at
        if event
        and event.action == "tradingview.checked"
        and event.details.get("revision") == revision
        else None
    )
    return TradingViewConnection(
        configured=bool(config.get("access_token")),
        revision=revision,
        expires_at=config.get("expires_at"),
        checked_at=checked,
        ready=bool(checked),
    )


@router.get("/connection", response_model=TradingViewConnection)
def get_connection(db: Db, owner: CurrentOwner):
    return view(db)


@router.post("/connect", response_model=TradingViewAuthorization)
def connect(
    body: TradingViewChange, request: Request, response: Response, db: Db, owner: CurrentOwner
):
    lock(db)
    _, revision = saved(db)
    if body.expected_revision != revision:
        raise HTTPException(409, "Connection changed. Reload before connecting.")
    origin = request.headers["origin"]  # Auth has already validated the exact allowed origin.
    parsed = urlsplit(origin)
    if parsed.scheme != "https" and (
        parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"}
    ):
        raise HTTPException(422, "TradingView sign-in requires HTTPS or this local workstation.")
    redirect_uri = origin + "/api/v1/tradingview/callback"
    try:
        metadata()
        registration = request_json(
            "POST",
            REGISTER,
            json={
                "client_name": "Tauric local research",
                "redirect_uris": [redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": SCOPE,
            },
        )
        client_id = registration["client_id"]
        if (
            not isinstance(client_id, str)
            or not 1 <= len(client_id) <= 1000
            or registration.get("token_endpoint_auth_method") != "none"
            or registration.get("redirect_uris") != [redirect_uri]
        ):
            raise ValueError("Invalid client registration")
    except Exception:
        raise HTTPException(
            503, "TradingView sign-in could not start. Check provider availability and retry."
        ) from None
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    browser_nonce = secrets.token_urlsafe(32)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    expires = now() + timedelta(minutes=10)
    db.add(
        TradingViewOAuthFlow(
            state_hash=digest(state),
            owner_id=owner.id,
            session_hash=request.state.session.token_hash,
            expires_at=expires,
            encrypted_config=seal(
                {
                    "verifier": verifier,
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "revision": revision,
                    "browser_hash": digest(browser_nonce),
                }
            ),
        )
    )
    services.audit(
        db,
        owner,
        "tradingview.connect_started",
        "tradingview_connection",
        PROVIDER,
        "Owner started read-only TradingView sign-in",
        {"revision": revision, "scope": SCOPE},
    )
    db.commit()
    # The main session remains SameSite=Strict. Only this short-lived, HttpOnly
    # callback cookie can cross the provider's top-level GET redirect.
    response.set_cookie(
        OAUTH_COOKIE,
        browser_nonce,
        httponly=True,
        secure=runtime_config().secure_cookies,
        samesite="lax",
        max_age=600,
        path=CALLBACK_PATH,
    )
    return {
        "authorization_url": AUTHORIZE
        + "?"
        + urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": SCOPE,
                "state": state,
                "resource": SERVER,
            }
        ),
        "expires_at": expires,
    }


@router.get("/callback", include_in_schema=False)
def callback(request: Request, db: Db):
    # Middleware removes these credentials from the access-log request scope.
    query = parse_qs(request.state.tradingview_query.decode(), max_num_fields=10)
    state, code = query.get("state", [""])[0], query.get("code", [""])[0]
    if not state or len(state) > 200 or len(code) > 4000:
        raise HTTPException(400, "TradingView sign-in response is invalid.")
    flow = db.scalar(
        select(TradingViewOAuthFlow)
        .where(TradingViewOAuthFlow.state_hash == digest(state))
        .with_for_update()
    )
    if not flow or flow.consumed or flow.expires_at <= now():
        raise HTTPException(
            400, "This sign-in has expired or belongs to another session. Reconnect in Settings."
        )
    config = unseal(flow.encrypted_config)
    nonce = request.cookies.get(OAUTH_COOKIE, "")
    session = db.get(OwnerSession, flow.session_hash)
    owner = db.get(Owner, flow.owner_id)
    current_token = request.cookies.get(COOKIE_NAME)
    if (
        not nonce
        or len(nonce) > 200
        or not secrets.compare_digest(digest(nonce), config["browser_hash"])
        or not session
        or session.revoked_at
        or session.expires_at <= now()
        or session.owner_id != flow.owner_id
        or not owner
        or (current_token and digest(current_token) != flow.session_hash)
    ):
        raise HTTPException(
            400, "This sign-in belongs to another or expired app session. Reconnect in Settings."
        )
    flow.consumed = True
    owner_id = owner.id
    db.commit()  # One-time code: a failed exchange requires a new sign-in.
    if not code or "error" in query:
        return RedirectResponse(
            config["redirect_uri"].removesuffix("/api/v1/tradingview/callback")
            + "/?tradingview=cancelled",
            status_code=303,
        )
    lock(db)
    _, revision = saved(db)
    if revision != config["revision"]:
        raise HTTPException(409, "Connection changed during sign-in. Reconnect in Settings.")
    try:
        data = request_json(
            "POST",
            TOKEN,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": config["client_id"],
                "redirect_uri": config["redirect_uri"],
                "code_verifier": config["verifier"],
                "resource": SERVER,
            },
        )
        tokens = token_config(data, config["client_id"], config["redirect_uri"])
    except Exception:
        raise HTTPException(
            503,
            "TradingView authorization did not complete. Reconnect and confirm your plan supports MCP.",
        ) from None
    append(db, owner_id, revision, tokens)
    services.audit(
        db,
        owner,
        "tradingview.connected",
        "tradingview_connection",
        PROVIDER,
        "Owner authorized encrypted read-only TradingView connection",
        {"revision": revision + 1, "scope": SCOPE},
    )
    db.commit()
    return RedirectResponse(
        config["redirect_uri"].removesuffix("/api/v1/tradingview/callback")
        + "/?tradingview=connected",
        status_code=303,
    )


@router.post("/disconnect", response_model=TradingViewConnection)
def disconnect(body: TradingViewChange, db: Db, owner: CurrentOwner):
    lock(db)
    _, revision = saved(db)
    if body.expected_revision != revision:
        raise HTTPException(409, "Connection changed. Reload before disconnecting.")
    append(db, owner.id, revision, {})
    services.audit(
        db,
        owner,
        "tradingview.disconnected",
        "tradingview_connection",
        PROVIDER,
        "Owner disabled TradingView connection",
        {"revision": revision + 1},
    )
    db.commit()
    return view(db)


@router.post("/test", response_model=dict)
def test_connection(body: TradingViewChange, db: Db, owner: CurrentOwner):
    from . import market, tradingview_sources

    lock(db)
    config, revision = saved(db)
    if body.expected_revision != revision:
        raise HTTPException(409, "Connection changed. Reload before testing.")
    if not config.get("access_token"):
        raise HTTPException(422, "Connect TradingView before testing.")
    # Release lock before a refresh uses its independent credential transaction.
    db.commit()
    sources = tradingview_sources.collect(db, market.instrument(db, "oanda:fx:EUR_USD"))
    current, revision = saved(db)
    if current.get("client_id") != config.get("client_id"):
        raise HTTPException(409, "Connection changed during testing. Reload and test again.")
    ready = all(s["status"] == "available" for s in sources)
    services.audit(
        db,
        owner,
        "tradingview.checked" if ready else "tradingview.check_failed",
        "tradingview_connection",
        PROVIDER,
        "Read-only economic calendar and rate-series check",
        {
            "revision": revision,
            "available_sources": sum(s["status"] == "available" for s in sources),
        },
    )
    db.commit()
    return {"ready": ready, "revision": revision, "sources": sources}
