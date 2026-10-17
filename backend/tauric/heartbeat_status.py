"""Owner-visible, sanitized local OpenClaw heartbeat telemetry; no model calls."""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import Field

from .config import runtime_config
from .models import now
from .schemas import Contract


class HeartbeatStatus(Contract):
    observed_at: datetime
    enabled: bool
    status: Literal[
        "disabled", "paused", "waiting", "running", "healthy", "failed", "stale",
        "unavailable", "unverified",
    ]
    model: Literal["tauric-heartbeat:4b"] | None = None
    interval_seconds: float | None = Field(default=None, gt=0)
    last_started_at: datetime | None = None
    last_finished_at: datetime | None = None
    next_check_at: datetime | None = None
    last_check_at: datetime | None = None
    ready: bool | None = None
    http_status: int | None = Field(default=None, ge=100, le=599)
    last_run_status: Literal["running", "ok", "error", "skipped", "interrupted", "superseded"] | None = None
    automatic: bool | None = None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_status() -> HeartbeatStatus:
    try:
        token = json.loads(Path(runtime_config().openclaw_private_file).read_text())["platform_token"]
        if not isinstance(token, str) or len(token) < 40:
            raise ValueError("Invalid service token")
        # Fixed private destination, no redirects/proxies, and no Gateway operator credential.
        request = Request(
            "http://openclaw:18790/heartbeat", headers={"Authorization": "Bearer " + token}
        )
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=2) as response:
            body = response.read(8193)
        if len(body) > 8192:
            raise ValueError("Oversized status")
        status = HeartbeatStatus.model_validate_json(body)
        if abs((now() - status.observed_at).total_seconds()) > 60:
            raise ValueError("Stale telemetry")
        return status
    except (OSError, ValueError, KeyError, TypeError):
        return HeartbeatStatus(observed_at=now(), enabled=False, status="unavailable")
