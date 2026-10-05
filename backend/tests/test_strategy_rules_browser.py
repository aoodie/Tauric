"""Built workstation browser flow on a disposable database and synthetic feed."""

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
from sqlalchemy.orm import Session
from test_strategy_rules import rule_feed as rule_feed
from test_strategy_scheduler import clock as clock

from tauric import autonomous, strategy_scheduler
from tauric.app import app
from tauric.auth import CurrentOwner
from tauric.config import runtime_config
from tauric.database import engine


@pytest.mark.browser
def test_built_executable_strategy_workflow(database, clock, monkeypatch):
    monkeypatch.setattr(autonomous, "now", lambda: clock["now"])
    root = Path(__file__).resolve().parents[2]
    browser_python = shutil.which("python3")
    if (
        not browser_python
        or subprocess.run(
            [browser_python, "-c", "import playwright"], capture_output=True
        ).returncode
    ):
        pytest.skip("Python Playwright is needed for the optional browser workflow.")
    if not (root / "frontend/dist/index.html").exists():
        pytest.skip("Build the frontend before running the optional browser workflow.")
    original_routes = list(app.router.routes)

    @app.post("/api/v1/test-strategy-feed/advance")
    def advance(owner: CurrentOwner):
        clock["now"] += timedelta(minutes=45)
        clock["mode"] = "stop"
        return {"synthetic": True}

    # Never installed in the application: this server exists only inside this test.
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    runtime_config().allowed_origins = [f"http://127.0.0.1:{port}"]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    monitor_stop = threading.Event()
    monitor_errors = []

    def monitor():
        while not monitor_stop.wait(0.2):
            try:
                with Session(engine()) as db:
                    strategy_scheduler.heartbeat(db)
                with Session(engine()) as db:
                    strategy_scheduler.process_one(db)
                with Session(engine()) as db:
                    autonomous.heartbeat(db)
                with Session(engine()) as db:
                    autonomous.process_one(db)
            except Exception as error:
                monitor_errors.append(type(error).__name__)

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    try:
        thread.start()
        monitor_thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        result = subprocess.run(
            [
                browser_python,
                str(root / "scripts/strategy_rules_browser_test.py"),
                f"http://127.0.0.1:{port}",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not monitor_errors, monitor_errors
    finally:
        monitor_stop.set()
        monitor_thread.join(timeout=10)
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = original_routes
