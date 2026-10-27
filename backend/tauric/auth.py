import hashlib
import secrets
import socket
from datetime import timedelta
from ipaddress import ip_address
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .config import runtime_config
from .database import database_session
from .models import AuditEvent, LoginAttempt, Owner, OwnerSession, now

COOKIE_NAME = "tauric_session"
hasher = PasswordHasher()
# Equal-cost password verification for unknown usernames.
DUMMY_HASH = hasher.hash(secrets.token_urlsafe(32))
Db = Annotated[Session, Depends(database_session)]


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf(token: str) -> str:
    return digest("tauric-csrf:" + token)


def check_origin(request: Request):
    if request.headers.get("origin") not in runtime_config().allowed_origins:
        raise HTTPException(
            403, "This request did not come from the configured workstation address."
        )


def authenticate(request: Request, db: Db) -> Owner:
    token = request.cookies.get(COOKIE_NAME)
    if not token or len(token) > 200:
        raise HTTPException(401, "Sign in to continue.")
    stored = db.get(OwnerSession, digest(token))
    if not stored or stored.revoked_at or stored.expires_at <= now():
        raise HTTPException(401, "Your session has ended. Sign in again.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf(token)):
            raise HTTPException(403, "Reload the workstation before saving changes.")
    owner = db.get(Owner, stored.owner_id)
    if not owner:
        raise HTTPException(401, "Sign in to continue.")
    request.state.session = stored
    return owner


CurrentOwner = Annotated[Owner, Depends(authenticate)]


def client_ip(request: Request):
    """Accept the ingress-overwritten address only from the configured proxy."""
    peer = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-tauric-client-ip")
    if not forwarded:
        return peer
    try:
        address = str(ip_address(forwarded))
        peer_address = ip_address(peer)
    except ValueError:
        return peer
    for host in runtime_config().trusted_proxy_hosts:
        try:
            addresses = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError:
            continue
        if any(peer_address == ip_address(item[4][0]) for item in addresses):
            return address
    return peer


def login(request: Request, db: Session, username: str, password: str):
    check_origin(request)
    config = runtime_config()
    # Database row lock makes throttling persist across API instances and restarts.
    peer = client_ip(request)
    key = digest("login:" + peer)
    from sqlalchemy.dialects.postgresql import insert

    db.execute(
        insert(LoginAttempt)
        .values(key=key, attempts=0, window_start=now())
        .on_conflict_do_nothing()
    )
    throttle = db.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
    current = now()
    if (current - throttle.window_start).total_seconds() >= config.login_window_seconds:
        throttle.attempts = 0
        throttle.window_start = current
    if throttle.attempts >= config.login_attempt_limit:
        db.commit()
        raise HTTPException(
            429,
            "Too many sign-in attempts. Try again in a few minutes.",
            headers={"Retry-After": str(config.login_window_seconds)},
        )
    owner = db.scalar(
        select(Owner).where(Owner.username == username.strip().lower()).with_for_update()
    )
    try:
        valid = hasher.verify(owner.password_hash if owner else DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid or not owner:
        throttle.attempts += 1
        db.commit()
        raise HTTPException(401, "The username or password is incorrect.")
    throttle.attempts = 0
    return create_owner_session(db, owner, "Owner signed in")


def create_owner_session(db: Session, owner: Owner, reason: str):
    current = now()
    config = runtime_config()
    token = secrets.token_urlsafe(48)
    stored = OwnerSession(
        token_hash=digest(token),
        owner_id=owner.id,
        expires_at=current + timedelta(hours=config.session_hours),
    )
    db.add(stored)
    db.add(
        AuditEvent(
            owner_id=owner.id,
            actor=owner.username,
            action="session.signed_in",
            resource_type="owner",
            resource_id=owner.id,
            reason=reason,
            details={},
        )
    )
    db.commit()
    return token, owner, stored


def change_password(request, db, owner, body):
    # Serialize with sign-in and other password changes so old credentials cannot
    # create a session after the revocation transaction.
    owner = db.scalar(
        select(Owner)
        .where(Owner.id == owner.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    session = db.scalar(
        select(OwnerSession)
        .where(OwnerSession.token_hash == request.state.session.token_hash)
        .execution_options(populate_existing=True)
    )
    if session.revoked_at or session.expires_at <= now():
        raise HTTPException(401, "Your session has ended. Sign in again.")
    from sqlalchemy.dialects.postgresql import insert

    key = digest("password-change:" + owner.id)
    db.execute(
        insert(LoginAttempt)
        .values(key=key, attempts=0, window_start=now())
        .on_conflict_do_nothing()
    )
    throttle = db.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
    config = runtime_config()
    if (now() - throttle.window_start).total_seconds() >= config.login_window_seconds:
        throttle.attempts = 0
        throttle.window_start = now()
    if throttle.attempts >= config.login_attempt_limit:
        db.commit()
        raise HTTPException(
            429,
            "Too many password attempts. Try again in a few minutes.",
            headers={"Retry-After": str(config.login_window_seconds)},
        )
    try:
        valid = hasher.verify(owner.password_hash, body.current_password)
    except (VerificationError, InvalidHashError):
        valid = False
    if not valid:
        throttle.attempts += 1
        db.commit()
        raise HTTPException(400, "The current password is incorrect.")
    if body.new_password != body.confirm_password:
        raise HTTPException(422, "The new passwords do not match.")
    if body.new_password == body.current_password:
        raise HTTPException(422, "Choose a different new password.")
    owner.password_hash = hasher.hash(body.new_password)
    throttle.attempts = 0
    db.execute(
        update(OwnerSession)
        .where(OwnerSession.owner_id == owner.id, OwnerSession.revoked_at.is_(None))
        .values(revoked_at=now())
    )
    db.add(
        AuditEvent(
            owner_id=owner.id,
            actor=owner.username,
            action="owner.password_changed",
            resource_type="owner",
            resource_id=owner.id,
            reason="Owner changed password and revoked all sessions",
            details={},
        )
    )
    db.commit()
