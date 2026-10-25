from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TAURIC_", extra="ignore")

    database_url: str = "postgresql+psycopg://tauric_app@localhost:5432/tauric"
    db_password_file: str | None = None
    db_user: str = "tauric_app"
    allowed_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
    secure_cookies: bool = True
    session_hours: int = Field(default=12, ge=1, le=168)
    login_attempt_limit: int = Field(default=5, ge=1, le=50)
    login_window_seconds: int = Field(default=300, ge=30, le=3600)
    trusted_proxy_hosts: list[str] = Field(default_factory=list, max_length=8)
    market_url: str = "https://data-api.binance.vision"
    market_timeout_seconds: int = Field(default=10, ge=1, le=30)
    market_refresh_seconds: int = Field(default=30, ge=10, le=3600)
    market_connections_file: str = "/run/tauric/market-data.json"
    model_profiles_file: str = "/run/tauric/model-profiles.json"
    model_key_file: str = "/run/tauric/model-key"
    openclaw_private_file: str = "/run/tauric/openclaw-private.json"
    runtime_directory: str = "/tmp/tauric-runtime"
    analysis_timeout_seconds: int = Field(default=1200, ge=30, le=7200)
    governance_url: str = "http://openclaw:18789"
    memory_private_file: str = "/run/tauric/memory-private.json"
    knowledge_vault_directory: str = "/data/knowledge"
    knowledge_vault_display: str = ""
    engineering_spool_directory: str = "/run/tauric-engineering"
    engineering_public_key_file: str = "/run/tauric-engineering/public/public.pem"
    release_spool_directory: str = "/run/tauric-release"

    @model_validator(mode="after")
    def private_database_password(self):
        if self.db_password_file:
            password = Path(self.db_password_file).read_text().strip()
            if not password:
                raise ValueError("The private database password file is empty.")
            self.database_url = (
                f"postgresql+psycopg://{quote(self.db_user, safe='')}:{quote(password, safe='')}"
                "@db:5432/tauric"
            )
        return self


SCHEMA_REVISION = "0037_research_discovery"


@lru_cache
def runtime_config() -> RuntimeConfig:
    return RuntimeConfig()
