"""Built daily research UI and real coordinator on an isolated database, offline feeds."""

import shutil
import socket
import subprocess
import threading
import time
from datetime import timedelta
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_paper import setup as setup
from test_portfolio_chat import portfolios_ready as portfolios_ready
from test_research import import_request
from test_scanner import configured
from test_scanner import scanner_feed as scanner_feed

from tauric import portfolio_research
from tauric.app import app
from tauric.config import runtime_config
from tauric.models import PortfolioResearchRun, now

pytest_plugins = ["test_chat"]


@pytest.mark.browser
def test_built_daily_research(ready, database, portfolios_ready, scanner_feed):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required for this optional browser test.")
    a, b, *_ = portfolios_ready
    profile, _ = configured(ready)
    r = ready.post("/api/v1/research/datasets/import", json=import_request())
    assert r.status_code == 201, r.text
    routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    runtime_config().allowed_origins = [url]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    stopped = threading.Event()
    errors = []

    def work():
        while not stopped.wait(0.15):
            try:
                with Session(database[0]) as db:
                    portfolio_research.heartbeat(db)
                with Session(database[0]) as db:
                    portfolio_research.process_one(db)
            except Exception as e:
                errors.append(type(e).__name__)

    worker = threading.Thread(target=work, daemon=True)
    try:
        server_thread.start()
        worker.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        result = subprocess.run(
            [
                python,
                str(root / "scripts/portfolio_research_browser_test.py"),
                url,
                a["id"],
                b["id"],
                profile["id"],
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        # The browser is now closed. Force a missed tick and verify independent worker completion.
        with database[0].begin() as db:
            db.execute(
                text(
                    "UPDATE portfolio_research_schedules SET next_daily_due=:due WHERE portfolio_id=:id"
                ),
                {"due": now() - timedelta(days=2), "id": a["id"]},
            )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with Session(database[0]) as db:
                completed = db.scalar(
                    select(PortfolioResearchRun).where(
                        PortfolioResearchRun.portfolio_id == a["id"],
                        PortfolioResearchRun.trigger == "daily",
                        PortfolioResearchRun.status == "partial",
                    )
                )
                if completed:
                    break
            time.sleep(0.15)
        assert completed is not None
        assert not errors, errors
    finally:
        stopped.set()
        worker.join(timeout=10)
        server.should_exit = True
        server_thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
