from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, identifier, now


class QualificationProtocol(Base):
    __tablename__ = "qualification_protocols"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    portfolio_id: Mapped[str] = mapped_column(String(36))
    portfolio_version_id: Mapped[str] = mapped_column(ForeignKey("portfolio_policy_versions.id"))
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"))
    dataset_id: Mapped[str] = mapped_column(ForeignKey("research_records.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    name: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QualificationEvaluation(Base):
    __tablename__ = "qualification_evaluations"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"), UniqueConstraint("protocol_id"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    protocol_id: Mapped[str] = mapped_column(ForeignKey("qualification_protocols.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QualificationAdmission(Base):
    __tablename__ = "qualification_admissions"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id"),
        UniqueConstraint("evaluation_id", "context_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    protocol_id: Mapped[str] = mapped_column(ForeignKey("qualification_protocols.id"))
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("qualification_evaluations.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    context_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class QualificationReport(Base):
    __tablename__ = "qualification_reports"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id"),
        UniqueConstraint("protocol_id", "revision"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    protocol_id: Mapped[str] = mapped_column(ForeignKey("qualification_protocols.id"))
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("qualification_evaluations.id"))
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("analysis_artifacts.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
