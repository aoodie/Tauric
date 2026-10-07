"""Memory is context, never native evidence or an execution permission."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from .schemas import Contract

KnowledgeKind = Literal["strategy", "chat", "research", "preference", "wiki"]


class MemorySettings(Contract):
    enabled: bool = Field(default=False, strict=True)
    record_chats: bool = Field(default=True, strict=True)
    record_strategies: bool = Field(default=True, strict=True)
    record_research: bool = Field(default=True, strict=True)
    use_in_chat: bool = Field(default=True, strict=True)
    honcho_enabled: bool = Field(default=False, strict=True)
    allow_external_context: bool = Field(default=False, strict=True)


class MemorySettingsChange(MemorySettings):
    expected_revision: int = Field(ge=0, strict=True)


class KnowledgeNote(Contract):
    request_id: UUID
    expected_revision: int = Field(default=0, ge=0, strict=True)
    kind: Literal["wiki", "preference"] = "wiki"
    title: str = Field(min_length=3, max_length=160)
    body: str = Field(min_length=3, max_length=20000)

    @field_validator("title", "body")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Enter a title and note")
        return value.strip()


class KnowledgeVisibility(Contract):
    expected_revision: int = Field(ge=1, strict=True)
    excluded: bool = Field(strict=True)


class KnowledgeView(Contract):
    id: str
    kind: KnowledgeKind
    source_kind: str
    title: str
    body: str
    provenance: dict
    checksum: str
    revision: int
    excluded: bool
    updated_at: datetime
    authority: Literal["memory_context_only"] = "memory_context_only"


class KnowledgeSearch(Contract):
    query: str
    items: list[KnowledgeView]
    has_more: bool
    search_method: Literal["local_keyword"] = "local_keyword"


class MemoryState(Contract):
    settings: MemorySettings
    revision: int
    counts: dict[str, int]
    worker_ready: bool
    last_collected_at: datetime | None
    honcho_configured: bool
    honcho_learning_enabled: bool
    last_honcho_at: datetime | None
    honcho_representation: str
    honcho_detail: str
    chat_recall_allowed: bool
    chat_recall_detail: str
    vault_configured: bool
    vault_location: str
    vault_conflicts: int
    authority: Literal["memory_context_only"] = "memory_context_only"
