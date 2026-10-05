"""Owner-only connection edits with encrypted storage and redacted views."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import func, select

from . import services
from .auth import COOKIE_NAME, CurrentOwner, Db, change_password
from .config import runtime_config
from .model_profiles import (
    ModelProfile,
    connection_head,
    decrypt_connection,
    encryption,
    load_host_profiles,
)
from .models import ModelConnectionVersion, identifier
from .schemas import ModelConnectionChange, ModelConnectionView, PasswordChange

router = APIRouter()
Profile = Literal["cloud", "local"]


def stored_profile(db, profile):
    record = connection_head(db, profile)
    if record:
        try:
            return decrypt_connection(record), record.revision, "settings"
        except ValueError:
            raise HTTPException(
                503,
                "Saved AI connections cannot be unlocked. Restore the matching host encryption key.",
            ) from None
    profiles, _ = load_host_profiles()
    if profile in profiles:
        return profiles[profile], 0, "host"
    return (
        ModelProfile(provider="openai" if profile == "cloud" else "openai_compatible"),
        0,
        "empty",
    )


def connection_view(db, profile):
    value, revision, source = stored_profile(db, profile)
    try:
        encryption()
        ready = True
    except (OSError, ValueError):
        ready = False
    return ModelConnectionView(
        id=profile,
        **value.public_config() | {"base_url": value.base_url},
        has_key=bool(value.api_key.get_secret_value()),
        configured=value.configured(),
        revision=revision,
        source=source,
        storage_ready=ready,
    )


@router.get("/api/v1/model-connections", response_model=list[ModelConnectionView])
def connections(db: Db, owner: CurrentOwner):
    return [connection_view(db, key) for key in ["cloud", "local"]]


@router.put("/api/v1/model-connections/{profile}", response_model=ModelConnectionView)
def save_connection(profile: Profile, body: ModelConnectionChange, db: Db, owner: CurrentOwner):
    db.execute(select(func.pg_advisory_xact_lock(913570030 if profile == "cloud" else 913570031)))
    current, revision, _ = stored_profile(db, profile)
    if revision != body.expected_revision:
        raise HTTPException(
            409, "This connection changed in another tab. Reload its current settings."
        )
    if body.key_action != "replace" and body.api_key:
        raise HTTPException(422, "Choose Replace key to supply a new credential.")
    if body.key_action == "replace" and not body.api_key.strip():
        raise HTTPException(422, "Enter the new API key, or choose Clear key.")
    if (
        body.key_action == "keep"
        and current.provider != body.provider
        and current.api_key.get_secret_value()
    ):
        raise HTTPException(422, "Replace or clear the API key when changing provider.")
    secret = (
        current.api_key.get_secret_value()
        if body.key_action == "keep"
        else body.api_key
        if body.key_action == "replace"
        else ""
    )
    try:
        value = ModelProfile(
            provider=body.provider,
            base_url=body.base_url.strip(),
            quick_model=body.quick_model.strip(),
            deep_model=body.deep_model.strip(),
            api_key=secret,
        )
    except ValidationError:
        raise HTTPException(
            422,
            "Use a valid HTTP(S) API address without embedded credentials, query parameters or fragments.",
        ) from None
    try:
        payload = value.model_dump(mode="json")
        # SecretStr intentionally redacts on export; supply it only to encryption.
        payload["api_key"] = secret
        import json

        encrypted = encryption().encrypt(json.dumps(payload).encode()).decode()
    except (OSError, ValueError):
        raise HTTPException(
            503, "AI settings cannot be saved until the host encryption key is available."
        ) from None
    db.add(
        ModelConnectionVersion(
            id=identifier(),
            owner_id=owner.id,
            profile=profile,
            revision=revision + 1,
            encrypted_config=encrypted,
        )
    )
    services.audit(
        db,
        owner,
        "model_connection.changed",
        "model_connection",
        profile,
        "Owner saved AI connection",
        {
            "revision": revision + 1,
            "provider": value.provider,
            "credential_action": body.key_action,
        },
    )
    db.commit()
    return connection_view(db, profile)


@router.post("/api/v1/owner/password", status_code=204)
def password(
    body: PasswordChange, request: Request, response: Response, db: Db, owner: CurrentOwner
):
    change_password(request, db, owner, body)
    response.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=runtime_config().secure_cookies,
        httponly=True,
        samesite="strict",
    )
