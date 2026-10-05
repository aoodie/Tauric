from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, field_validator

SettingValue = StrictBool | StrictInt | StrictStr
Scope = Literal["global", "user"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    @field_validator("reason", "name", check_fields=False)
    @classmethod
    def meaningful_text(cls, value: str) -> str:
        minimum = 1 if cls.__name__ == "WorkspaceCreate" else 3
        value = value.strip()
        if len(value) < minimum:
            raise ValueError("Enter meaningful text, not just spaces.")
        return value


class LoginRequest(Contract):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=1024)


class SessionView(Contract):
    username: str
    csrf_token: str
    expires_at: datetime


class PasswordChange(Contract):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)
    confirm_password: str = Field(min_length=12, max_length=1024)

    @field_validator("new_password")
    @classmethod
    def nonblank_password(cls, value):
        if not value.strip():
            raise ValueError("Choose a password with more than spaces.")
        return value


class ModelConnectionChange(Contract):
    provider: Literal["openai", "anthropic", "google", "openrouter", "ollama", "openai_compatible"]
    base_url: str = Field(default="", max_length=2048)
    quick_model: str = Field(default="", max_length=120)
    deep_model: str = Field(default="", max_length=120)
    api_key: str = Field(default="", max_length=4096)
    key_action: Literal["keep", "replace", "clear"] = "keep"
    expected_revision: int = Field(ge=0)


class ModelConnectionView(Contract):
    id: Literal["cloud", "local"]
    provider: str
    base_url: str
    quick_model: str
    deep_model: str
    has_key: bool
    configured: bool
    revision: int
    source: Literal["settings", "host", "empty"]
    storage_ready: bool


class SettingDefinitionView(Contract):
    key: str
    label: str
    description: str
    kind: Literal["enum", "timezone", "integer", "boolean"]
    default: SettingValue
    scopes: list[Scope]
    options: list[str] = []
    minimum: int | None = None
    maximum: int | None = None
    restart_required: bool = False
    sensitive: bool = False


class SettingView(Contract):
    definition: SettingDefinitionView
    effective_value: SettingValue
    source_scope: Literal["default", "global", "user"]
    source_revision: int
    global_value: SettingValue
    global_revision: int
    user_revision: int
    overridden: bool


class SettingChange(Contract):
    scope: Scope
    value: SettingValue | None = None
    inherit: bool = False
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


class RestoreRequest(Contract):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=3, max_length=500)


class SettingVersionView(Contract):
    id: str
    revision: int
    value: SettingValue | None
    inherited: bool
    actor: str
    reason: str
    created_at: datetime


class WorkspaceCreate(Contract):
    name: str = Field(min_length=1, max_length=80)
    clone_from: str | None = None


class LayoutChange(Contract):
    layout: dict[str, Any]
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=500)


class WorkspaceView(Contract):
    id: str
    name: str
    revision: int
    layout: dict[str, Any]


class LayoutVersionView(Contract):
    id: str
    revision: int
    layout: dict[str, Any]
    actor: str
    reason: str
    created_at: datetime


class ResourceRef(Contract):
    type: str
    id: str


class AuditView(Contract):
    id: str
    actor: str
    action: str
    resource: ResourceRef
    reason: str
    details: dict[str, Any]
    created_at: datetime


class AuditPage(Contract):
    items: list[AuditView]
    next_cursor: str | None


class HealthView(Contract):
    status: Literal["ok", "unavailable"]


class ResourceView(Contract):
    resource: ResourceRef
    title: str
    data: dict[str, Any]
