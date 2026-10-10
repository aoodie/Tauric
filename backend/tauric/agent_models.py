"""Task aggregates and append-only lifecycle observations."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base, identifier, now


class AgentTask(Base):
    __tablename__ = "agent_tasks"
    __table_args__ = (UniqueConstraint("owner_id", "request_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    request_id: Mapped[str] = mapped_column(String(36))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("agent_tasks.id"))
    root_id: Mapped[str] = mapped_column(String(36))
    conversation_id: Mapped[str] = mapped_column(String(36))
    objective: Mapped[str] = mapped_column(Text)
    criteria: Mapped[list] = mapped_column(JSON)
    dependencies: Mapped[list] = mapped_column(JSON)
    scope: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    max_wakes: Mapped[int] = mapped_column(Integer)
    max_attempts: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentTaskWake(Base):
    __tablename__ = "agent_task_wakes"
    turn_id: Mapped[str] = mapped_column(ForeignKey("chat_turns.id"), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"))
    task_revision: Mapped[int] = mapped_column(Integer)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class AgentTaskEvent(Base):
    __tablename__ = "agent_task_events"
    __table_args__ = (UniqueConstraint("task_id", "sequence"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class EngineeringJob(Base):
    __tablename__ = "engineering_jobs"
    __table_args__ = (UniqueConstraint("task_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=identifier)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"))
    request_key: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict] = mapped_column(JSON)
    checksum: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="queued")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
