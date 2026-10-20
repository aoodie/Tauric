"""Separated research views against the real built frontend and a disposable API."""

import socket
import subprocess
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_discovery import load, settings
from test_discovery import provider as provider

from tauric import discovery, quant_worker
from tauric.app import app
from tauric.config import runtime_config
from tauric.discovery_models import DiscoveryRecord
from tauric.models import now


@pytest.mark.browser
def test_built_strategy_discovery(signed_in, database, provider):
    root = Path(__file__).resolve().parents[2]
    end = now().replace(minute=0, second=0, microsecond=0)
    for interval in ("1h", "4h"):
        load(signed_in, database, end - timedelta(days=180), end, interval)
    settings(signed_in)
    started = signed_in.post("/api/v1/research/discovery/cycles", json={"request_id": str(uuid4())})
    for _ in range(30):
        with Session(database[0]) as db:
            discovery.process_one(db)
            claimed = quant_worker.claim(db, "lean")
            if claimed:
                key, token, job = claimed
                times = [
                    b["close_time"]
                    for b in job["candles"]
                    if __import__("datetime").datetime.fromisoformat(
                        job["request"]["validation_end"]
                    )
                    < __import__("datetime").datetime.fromisoformat(b["close_time"])
                    <= __import__("datetime").datetime.fromisoformat(job["request"]["test_end"])
                ]
                curve = [{"time": t, "equity": 10000 + i * 0.5} for i, t in enumerate(times)]
                quant_worker.finish(
                    db,
                    key,
                    token,
                    result={
                        "engine": "lean",
                        "authority": "research_only",
                        "currency": "USD",
                        "return_percent": 1,
                        "max_drawdown_percent": 0.2,
                        "fills": [
                            {"time": times[0], "quantity": 100, "price": 1.1, "fee": 0},
                            {"time": times[-1], "quantity": -100, "price": 1.11, "fee": 0},
                        ],
                        "equity_curve": curve,
                    },
                )
            row = db.get(DiscoveryRecord, started.json()["id"])
            if row.payload["status"] == "completed":
                break
    assert row.payload["status"] == "completed"
    with Session(database[0]) as db:
        assert (
            len(list(db.scalars(select(DiscoveryRecord).where(DiscoveryRecord.kind == "trial"))))
            == 2
        )
    routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    runtime_config().allowed_origins = [url]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        result = subprocess.run(
            [sys.executable, str(root / "scripts/discovery_browser_test.py"), url],
            text=True,
            capture_output=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
