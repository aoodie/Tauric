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
from test_scanner import ready_model as ready_model
from test_scanner import scanner_feed as scanner_feed

from tauric import paper
from tauric.app import app
from tauric.config import runtime_config
from tauric.models import AssistantRecord, Owner


@pytest.mark.browser
def test_built_assistant_workspace(database, scanner_feed, ready_model):
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
        key = str(uuid4())
        inputs = {"synthetic": True}
        for stage, result in [
            ("requested", {}),
            (
                "completed",
                {
                    "hypothesis": "Clear labels reduce navigation errors",
                    "evidence": ["Synthetic UI fixture"],
                    "counter_evidence": ["No usability study has been run"],
                    "test_plan": ["Verify keyboard navigation"],
                    "files": [{"path": "frontend/src/scanner/Scanner.tsx", "sha256": "0" * 64}],
                    "changes": ["Clarify setup labels"],
                    "patch": "--- a/frontend/src/scanner/Scanner.tsx\n+++ b/frontend/src/scanner/Scanner.tsx\n@@ -1 +1 @@\n-old label\n+clear label\n",
                    "code_changes_applied": False,
                    "validation": "not_run",
                    "approval": "owner_review_required",
                },
            ),
        ]:
            payload = {"inputs": inputs, "result": result, "channel": "synthetic_fixture"}
            db.add(
                AssistantRecord(
                    id=str(uuid4()),
                    owner_id=owner.id,
                    request_id=key,
                    stage=stage,
                    operation="prepare_upgrade",
                    title="Synthetic review proposal",
                    payload=payload,
                    checksum=paper.checksum(payload),
                )
            )
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
            [python, str(root / "scripts/assistant_workspace_browser_test.py"), url],
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
