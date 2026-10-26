"""Host-owned private model connections. API responses never contain credentials."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.fernet import InvalidToken
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy import select

from .config import runtime_config
from .credential_keys import encryption as keyring_encryption
from .market_contracts import ProfileView
from .models import ModelConnectionVersion

PROVIDERS = {"openai", "anthropic", "google", "openrouter", "ollama", "openai_compatible"}
KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
}


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str
    base_url: str = ""
    quick_model: str = Field(default="", max_length=120)
    deep_model: str = Field(default="", max_length=120)
    api_key: SecretStr = SecretStr("")

    @field_validator("provider")
    @classmethod
    def supported_provider(cls, value):
        if value not in PROVIDERS:
            raise ValueError("Unsupported model provider")
        return value

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        if not value:
            return value
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise ValueError("Use an HTTP(S) endpoint without embedded secrets")
        return value

    def configured(self):
        if not self.quick_model.strip() or not self.deep_model.strip():
            return False
        if self.provider in {"ollama", "openai_compatible"}:
            return bool(self.base_url)
        return bool(self.api_key.get_secret_value())

    def public_config(self):
        return {
            "provider": self.provider,
            "base_url": self.base_url or None,
            "quick_model": self.quick_model,
            "deep_model": self.deep_model,
        }

    def fingerprint(self):
        # Excludes the credential; jobs pin endpoint/models and stop if these change.
        return hashlib.sha256(json.dumps(self.public_config(), sort_keys=True).encode()).hexdigest()


def encryption():
    return keyring_encryption(Path(runtime_config().model_key_file).read_bytes())


def connection_head(db, profile):
    return db.scalar(
        select(ModelConnectionVersion)
        .where(ModelConnectionVersion.profile == profile)
        .order_by(ModelConnectionVersion.revision.desc())
        .limit(1)
    )


def decrypt_connection(record):
    try:
        return ModelProfile.model_validate_json(
            encryption().decrypt(record.encrypted_config.encode())
        )
    except (OSError, ValueError, InvalidToken):
        raise ValueError(
            "Saved model connections cannot be unlocked. Restore the matching host encryption key."
        ) from None


def load_profiles(db=None):
    profiles, error = load_host_profiles()
    if db is not None:
        try:
            for key in ["cloud", "local"]:
                record = connection_head(db, key)
                if record:
                    profiles[key] = decrypt_connection(record)
                    error = None
        except ValueError:
            return {}, "Saved AI connections cannot be unlocked. Check the host encryption key."
    return profiles, error


def load_host_profiles():
    try:
        raw = json.loads(Path(runtime_config().model_profiles_file).read_text())
        if not isinstance(raw, dict) or set(raw) - {"cloud", "local"}:
            raise ValueError("Invalid profile file")
        return {key: ModelProfile.model_validate(value) for key, value in raw.items()}, None
    except FileNotFoundError:
        return {}, "Configure this connection in Settings → AI connections."
    except (OSError, ValueError, TypeError):
        return (
            {},
            "The private model profiles file is invalid. Check it on the host; credentials are not displayed.",
        )


def public_profiles(db=None):
    profiles, error = load_profiles(db)
    return [
        ProfileView(
            id=key,
            label="Cloud AI" if key == "cloud" else "External local AI",
            configured=key in profiles and profiles[key].configured(),
            provider=profiles[key].provider if key in profiles else None,
            quick_model=profiles[key].quick_model or None if key in profiles else None,
            deep_model=profiles[key].deep_model or None if key in profiles else None,
            detail=error
            or (
                "Models configured; connection is verified when a run succeeds."
                if key in profiles and profiles[key].configured()
                else "Configure this connection in Settings → AI connections."
            ),
        )
        for key in ["cloud", "local"]
    ]
