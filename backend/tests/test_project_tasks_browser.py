"""Production frontend on an isolated database with synthetic review artifacts."""

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

from tauric import agent_tasks
from tauric.agent_contracts import TaskCreate
from tauric.app import app
from tauric.config import runtime_config
from tauric.models import Owner


@pytest.mark.browser
def test_built_project_task_controls(database):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required")
    if not (root / "frontend/dist/index.html").exists():
        pytest.skip("Build the frontend before browser verification")
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        parent = agent_tasks.create(
            db,
            owner,
            TaskCreate(
                request_id=uuid4(),
                objective="Browser project goal",
                criteria=[{"description": "Actual checks pass", "proof": "tests_passed"}],
            ),
        )
        agent_tasks.create(
            db,
            owner,
            TaskCreate(
                request_id=uuid4(),
                parent_id=parent["id"],
                objective="Browser dependent task",
                criteria=[{"description": "Actual command succeeds", "proof": "command_success"}],
            ),
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
            [python, str(root / "scripts/project_tasks_browser_test.py"), url],
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
