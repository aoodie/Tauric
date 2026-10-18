"""Research Lab input snapshots and guarded job progress."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, identifier, now


class QuantRecord(Base):
    __tablename__ = "quant_records"
    __table_args__ = (UniqueConstraint("owner_id", "kind", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    kind: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(36))
    parent_id: Mapped[str] = mapped_column(ForeignKey("research_records.id"))
    name: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
