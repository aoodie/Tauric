"""Bounded mutable progress counters; trading authority stays in its own journals."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


class WorkerLaneProgress(Base):
    __tablename__ = "worker_lane_progress"
    worker: Mapped[str] = mapped_column(String(24), primary_key=True)
    lane: Mapped[str] = mapped_column(String(40), primary_key=True)
    attempts: Mapped[int] = mapped_column(BigInteger, default=0)
    successes: Mapped[int] = mapped_column(BigInteger, default=0)
    failures: Mapped[int] = mapped_column(BigInteger, default=0)
    work_completed: Mapped[int] = mapped_column(BigInteger, default=0)
    consecutive_failures: Mapped[int] = mapped_column(BigInteger, default=0)
    invocation_id: Mapped[str] = mapped_column(String(36))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_work_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(32))
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
