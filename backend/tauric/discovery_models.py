"""Compressed immutable market history and owner-bound research work."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, identifier, now


class HistoryChunk(Base):
    __tablename__ = "research_history_chunks"
    __table_args__ = (
        Index(
            "research_history_range", "owner_id", "instrument_id", "interval", "start_at", "end_at"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    instrument_id: Mapped[str] = mapped_column(String(120))
    interval: Mapped[str] = mapped_column(String(8))
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bars: Mapped[int] = mapped_column(Integer)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class DiscoveryRecord(Base):
    __tablename__ = "research_discovery_records"
    __table_args__ = (
        UniqueConstraint("owner_id", "kind", "request_id"),
        Index("research_discovery_owner_kind", "owner_id", "kind", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    kind: Mapped[str] = mapped_column(String(24))
    request_id: Mapped[str] = mapped_column(String(36))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("research_discovery_records.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
