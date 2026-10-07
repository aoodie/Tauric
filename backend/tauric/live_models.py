"""Immutable observations and prospectively registered execution protocols."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, identifier, now


class TradingReadinessRecord(Base):
    __tablename__ = "trading_readiness_records"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    kind: Mapped[str] = mapped_column(String(24))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("trading_readiness_records.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
