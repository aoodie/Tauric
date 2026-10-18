"""Production knowledge UI with a synthetic remote chat endpoint, no inference."""

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_chat import ready as ready

from tauric import knowledge
from tauric.app import app
from tauric.config import runtime_config
from tauric.knowledge_contracts import MemorySettingsChange
from tauric.models import Owner


@pytest.mark.browser
def test_built_knowledge(ready, database):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required")
    if not (root / "frontend/dist/index.html").exists():
        pytest.skip("Build the frontend first")
    profile = Path(runtime_config().model_profiles_file)
    value = json.loads(profile.read_text())
    value["local"]["base_url"] = "https://remote.invalid/v1"
    profile.write_text(json.dumps(value))
    with Session(database[0]) as db:
        knowledge.save_settings(
            db, db.scalar(select(Owner)), MemorySettingsChange(expected_revision=0, enabled=True)
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
            [python, str(root / "scripts/knowledge_browser_test.py"), url],
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
