"""Owner memory copies. These records never participate in trading authorization."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, identifier, now


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("owner_id", "source_kind", "source_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    source_kind: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(250))
    kind: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    provenance: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    export_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemorySettingsVersion(Base):
    __tablename__ = "memory_settings_versions"
    __table_args__ = (UniqueConstraint("owner_id", "revision"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MemorySyncState(Base):
    __tablename__ = "memory_sync_state"
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"), primary_key=True)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_honcho_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(250))
    representation: Mapped[str] = mapped_column(Text, default="")
    honcho_selection_hash: Mapped[str | None] = mapped_column(String(64))
    honcho_workspace: Mapped[str | None] = mapped_column(String(120))
    honcho_retired_workspaces: Mapped[list] = mapped_column(JSON, default=list)
    vault_conflicts: Mapped[int] = mapped_column(Integer, default=0)


class HonchoDelivery(Base):
    __tablename__ = "honcho_deliveries"
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    remote_ids: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
