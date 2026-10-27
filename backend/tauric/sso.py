"""Owner-only OpenID Connect sign-in using an explicitly pinned subject."""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from .auth import COOKIE_NAME, Db, client_ip, create_owner_session, digest
from .config import runtime_config
from .model_profiles import encryption
from .models import LoginAttempt, Owner, now

router = APIRouter()
ATTEMPT_COOKIE = "tauric_sso_attempt"
CALLBACK_PATH = "/api/v1/sso/callback"
ATTEMPT_SECONDS = 300


class SsoStatus(BaseModel):
    enabled: bool


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_request(url: str, data: dict | None = None) -> dict:
    """Do not follow redirects or accept unlimited metadata/token responses."""
    try:
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            method = "POST" if data is not None else "GET"
            with client.stream(method, url, data=data, headers={"Accept": "application/json"}) as r:
                r.raise_for_status()
                payload = bytearray()
                for part in r.iter_bytes():
                    payload.extend(part)
                    if len(payload) > 65536:
                        raise ValueError("SSO response is too large")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise ValueError("Invalid SSO response")
        return result
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise ValueError("SSO provider unavailable") from exc


def _metadata(config) -> dict:
    document = _json_request(config.sso_issuer + ".well-known/openid-configuration")
    if document.get("issuer") != config.sso_issuer:
        raise ValueError("SSO issuer mismatch")
    issuer = urlsplit(config.sso_issuer)
    for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        value = document.get(field)
        if not isinstance(value, str):
            raise ValueError("Invalid SSO endpoint")
        endpoint = urlsplit(value)
        if (
            endpoint.scheme != "https"
            or endpoint.hostname != issuer.hostname
            or endpoint.port != issuer.port
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise ValueError("Invalid SSO endpoint")
    return document


def _verify_id_token(id_token: str, metadata: dict, config, nonce: str) -> str:
    if not isinstance(id_token, str) or len(id_token) > 20000:
        raise ValueError("Invalid identity token")
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Invalid signing algorithm")
        keys = _json_request(metadata["jwks_uri"]).get("keys")
        if not isinstance(keys, list):
            raise ValueError("Invalid signing keys")
        matches = [
            key
            for key in keys
            if isinstance(key, dict)
            and key.get("kid") == header["kid"]
            and key.get("kty") == "RSA"
            and key.get("use") in (None, "sig")
            and key.get("alg") in (None, "RS256")
        ]
        if len(matches) != 1:
            raise ValueError("Signing key not found")
        key = jwt.PyJWK.from_dict(matches[0]).key
        if key.key_size < 2048:
            raise ValueError("SSO signing key is too small")
        claims = jwt.decode(
            id_token,
            key,
            algorithms=["RS256"],
            audience=config.sso_client_id,
            issuer=config.sso_issuer,
            options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
            leeway=10,
        )
        subject = claims.get("sub")
        actual_nonce = claims.get("nonce")
        audience = claims.get("aud")
        if (
            not isinstance(subject, str)
            or not subject.isascii()
            or not isinstance(actual_nonce, str)
            or not actual_nonce.isascii()
            or not secrets.compare_digest(actual_nonce, nonce)
            or not secrets.compare_digest(subject, config.sso_subject)
            or (
                isinstance(audience, list)
                and len(audience) > 1
                and claims.get("azp") != config.sso_client_id
            )
            or ("azp" in claims and claims["azp"] != config.sso_client_id)
        ):
            raise ValueError("SSO identity does not match the owner")
        return subject
    except (jwt.PyJWTError, KeyError, TypeError) as exc:
        raise ValueError("Invalid identity token") from exc


@router.get("/api/v1/sso/status", response_model=SsoStatus)
def sso_status():
    return SsoStatus(enabled=bool(runtime_config().sso_issuer))


@router.get("/api/v1/sso/start")
def sso_start(request: Request, db: Db):
    config = runtime_config()
    if not config.sso_issuer:
        raise HTTPException(404, "SSO is not configured.")
    key = digest("sso-start:" + client_ip(request))
    db.execute(
        insert(LoginAttempt)
        .values(key=key, attempts=0, window_start=now())
        .on_conflict_do_nothing()
    )
    throttle = db.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
    if (now() - throttle.window_start).total_seconds() >= config.login_window_seconds:
        throttle.attempts = 0
        throttle.window_start = now()
    if throttle.attempts >= config.login_attempt_limit:
        db.commit()
        raise HTTPException(429, "Too many sign-in attempts. Try again later.")
    throttle.attempts += 1
    db.commit()
    try:
        metadata = _metadata(config)
        state, verifier, nonce = (
            secrets.token_urlsafe(32),
            secrets.token_urlsafe(48),
            secrets.token_urlsafe(32),
        )
        attempt = json.dumps(
            {"purpose": "tauric-sso-v1", "state": state, "verifier": verifier, "nonce": nonce}
        ).encode()
        cookie = encryption().encrypt(attempt).decode("ascii")
        query = urlencode(
            {
                "response_type": "code",
                "client_id": config.sso_client_id,
                "redirect_uri": config.sso_callback_uri,
                "scope": "openid",
                "state": state,
                "nonce": nonce,
                "code_challenge": _b64(hashlib.sha256(verifier.encode("ascii")).digest()),
                "code_challenge_method": "S256",
            }
        )
        response = RedirectResponse(
            metadata["authorization_endpoint"] + "?" + query, status_code=302
        )
        response.set_cookie(
            ATTEMPT_COOKIE,
            cookie,
            max_age=ATTEMPT_SECONDS,
            path=CALLBACK_PATH,
            secure=True,
            httponly=True,
            samesite="lax",
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    except (OSError, ValueError, TypeError):
        raise HTTPException(
            503, "SSO is unavailable. You can still use your owner password."
        ) from None


@router.get(CALLBACK_PATH, include_in_schema=False)
def sso_callback(request: Request, db: Db):
    config = runtime_config()
    failure = RedirectResponse("/?sso=failed", status_code=303)
    try:
        if not config.sso_issuer:
            return failure
        raw = request.state.sso_query
        if len(raw) > 8192:
            return failure
        params = parse_qs(
            raw.decode("ascii"), keep_blank_values=True, strict_parsing=True, max_num_fields=12
        )
        if set(params) - {"state", "code", "scope", "iss", "session_state"}:
            return failure
        if any(len(value) != 1 for value in params.values()):
            return failure
        state, code = params.get("state", [""])[0], params.get("code", [""])[0]
        cookie = request.cookies.get(ATTEMPT_COOKIE, "")
        if not state or not code or len(code) > 4096 or len(cookie) > 4096:
            return failure
        attempt = json.loads(encryption().decrypt(cookie.encode("ascii"), ttl=ATTEMPT_SECONDS))
        if (
            not isinstance(attempt, dict)
            or attempt.get("purpose") != "tauric-sso-v1"
            or not isinstance(attempt.get("state"), str)
            or not state.isascii()
            or not secrets.compare_digest(state, attempt["state"])
            or not isinstance(attempt.get("verifier"), str)
            or not isinstance(attempt.get("nonce"), str)
            or ("iss" in params and params["iss"][0] != config.sso_issuer)
        ):
            return failure
        metadata = _metadata(config)
        tokens = _json_request(
            metadata["token_endpoint"],
            {
                "grant_type": "authorization_code",
                "code": code,
                "client_id": config.sso_client_id,
                "redirect_uri": config.sso_callback_uri,
                "code_verifier": attempt["verifier"],
            },
        )
        _verify_id_token(tokens.get("id_token"), metadata, config, attempt["nonce"])
        owner = db.scalar(select(Owner).where(Owner.singleton.is_(True)))
        if not owner:
            return failure
        token, _, _ = create_owner_session(db, owner, "Owner signed in with SSO")
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=True,
            samesite="strict",
            max_age=config.session_hours * 3600,
            path="/",
        )
        return response
    except (ValueError, OSError, TypeError, UnicodeError, InvalidToken):
        return failure
