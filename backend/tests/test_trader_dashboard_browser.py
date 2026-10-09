"""Real built dashboard on isolated PostgreSQL and explicit synthetic market evidence."""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_assistant_activity import record
from test_independent_scanners import standalone
from test_multifactor_scanner import multifactor_market as multifactor_market
from test_scanner import scan

from tauric.app import app
from tauric.config import runtime_config
from tauric.models import AnalysisJob, Owner


@pytest.mark.browser
def test_built_trader_dashboard(signed_in, database, multifactor_market):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required for browser verification")
    assert (root / "frontend/dist/index.html").exists(), "Build the frontend first"
    profile, _ = standalone(
        signed_in,
        "Synthetic multifactor watch",
        universe=multifactor_market[:2],
        scoring_version="multifactor_v1",
        minimum_setup_score=0,
        minimum_volume_ratio="1.5",
        lookback=20,
        max_analyses_per_day=0,
        candidate_ttl_seconds=3600,
    )
    result, _ = scan(signed_in, profile)
    assert len(result["potential_candidates"]) == 2
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        job = AnalysisJob(
            owner_id=owner.id,
            request_id=str(uuid4()),
            instrument_id=multifactor_market[0],
            profile="local",
            request={},
            status="queued",
        )
        db.add(job)
        db.flush()
        record(db, owner, "research_candidate", {"id": job.id, "status": "queued"})
        db.commit()
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
            [python, str(root / "scripts/trader_dashboard_browser_test.py"), url],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
