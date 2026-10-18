from datetime import datetime
from typing import Literal

from .schemas import Contract


class LaneProgressView(Contract):
    worker: str
    lane: str
    status: Literal["unobserved", "stale", "failing", "running", "healthy"]
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    work_completed: int = 0
    consecutive_failures: int = 0
    invocation_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_success_at: datetime | None = None
    last_work_at: datetime | None = None
    last_failure_at: datetime | None = None
    error_category: str | None = None
    duration_ms: int | None = None


class QueueProgressView(Contract):
    count: int
    oldest_age_seconds: float | None


class OperatingStateView(Contract):
    generated_at: datetime
    lanes: list[LaneProgressView]
    native_queue: QueueProgressView
    review_queue: QueueProgressView
    unresolved_entries: QueueProgressView
    unresolved_closes: QueueProgressView
    scope: str
