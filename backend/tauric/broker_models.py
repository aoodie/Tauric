"""Durable broker intents and append-only authority/observation receipts."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, identifier, now


class BrokerPolicyVersion(Base):
    __tablename__ = "broker_policy_versions"
    __table_args__ = (
        UniqueConstraint("owner_id", "provider", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    provider: Mapped[str] = mapped_column(String(20))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerOrderIntent(Base):
    __tablename__ = "broker_order_intents"
    __table_args__ = (
        UniqueConstraint("owner_id", "request_id"),
        UniqueConstraint("client_order_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    provider: Mapped[str] = mapped_column(String(20))
    request_id: Mapped[str] = mapped_column(String(36))
    client_order_id: Mapped[str] = mapped_column(String(48))
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("broker_policy_versions.id"))
    connection_revision: Mapped[int] = mapped_column(Integer)
    account_fingerprint: Mapped[str] = mapped_column(String(64))
    review_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_review_decisions.id"), unique=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerOrderEvent(Base):
    __tablename__ = "broker_order_events"
    __table_args__ = (UniqueConstraint("intent_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    intent_id: Mapped[str] = mapped_column(ForeignKey("broker_order_intents.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerPositionCursor(Base):
    __tablename__ = "broker_position_cursors"
    entry_intent_id: Mapped[str] = mapped_column(
        ForeignKey("broker_order_intents.id"), primary_key=True
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    status: Mapped[str] = mapped_column(String(24), default="unknown")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerCloseRequest(Base):
    __tablename__ = "broker_close_requests"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    entry_intent_id: Mapped[str] = mapped_column(ForeignKey("broker_order_intents.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="prepared")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerReviewJob(Base):
    __tablename__ = "broker_review_jobs"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    assessment_job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"))
    profile: Mapped[str] = mapped_column(String(12))
    request: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    lease_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    context_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_id: Mapped[str | None] = mapped_column(ForeignKey("broker_review_decisions.id"))
    error: Mapped[str | None] = mapped_column(String(500))
    usage: Mapped[dict] = mapped_column(JSON, default=dict)


class BrokerReviewDecision(Base):
    __tablename__ = "broker_review_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("broker_review_jobs.id"), unique=True)
    assessment_job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"), unique=True)
    native_artifact_id: Mapped[str] = mapped_column(ForeignKey("analysis_artifacts.id"))
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("broker_policy_versions.id"))
    provider: Mapped[str] = mapped_column(String(20))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerRouteVersion(Base):
    __tablename__ = "broker_route_versions"
    __table_args__ = (
        UniqueConstraint("route_id", "revision"),
        UniqueConstraint("owner_id", "request_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    route_id: Mapped[str] = mapped_column(String(36))
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    revision: Mapped[int] = mapped_column(Integer)
    request_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerRoute(Base):
    __tablename__ = "broker_routes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("broker_route_versions.id"))
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_event_id: Mapped[str | None] = mapped_column(ForeignKey("broker_route_events.id"))


class BrokerRouteWorkflow(Base):
    __tablename__ = "broker_route_workflows"
    __table_args__ = (UniqueConstraint("owner_id", "signal_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    route_id: Mapped[str] = mapped_column(ForeignKey("broker_routes.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("broker_route_versions.id"))
    signal_key: Mapped[str] = mapped_column(String(64))
    assessment_job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id"), unique=True)
    review_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_review_jobs.id"), unique=True
    )
    intent_id: Mapped[str | None] = mapped_column(
        ForeignKey("broker_order_intents.id"), unique=True
    )
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="native_pending")
    next_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class BrokerRouteEvent(Base):
    __tablename__ = "broker_route_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    route_id: Mapped[str] = mapped_column(ForeignKey("broker_routes.id"))
    version_id: Mapped[str] = mapped_column(ForeignKey("broker_route_versions.id"))
    workflow_id: Mapped[str | None] = mapped_column(ForeignKey("broker_route_workflows.id"))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
