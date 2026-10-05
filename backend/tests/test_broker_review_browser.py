"""Production UI and actual HTTP/DB with an explicitly offline OpenClaw result."""

import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from test_broker_review import configured as configured
from test_broker_review import lifecycle as lifecycle
from test_broker_review import ready as ready
from test_broker_review import setup as setup

from tauric import broker_execution, broker_review
from tauric.app import app
from tauric.broker_models import BrokerReviewJob
from tauric.config import runtime_config


@pytest.mark.browser
def test_broker_review_browser(database, ready):
    root = Path(__file__).resolve().parents[2]
    browser_python = shutil.which("python3")
    if (
        not browser_python
        or subprocess.run(
            [browser_python, "-c", "import playwright"], capture_output=True
        ).returncode
    ):
        pytest.skip("Python Playwright is needed for the browser check.")
    routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    base = f"http://127.0.0.1:{sock.getsockname()[1]}"
    runtime_config().allowed_origins = [base]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    stopped, failures, calls = threading.Event(), [], []

    def synthetic_worker():
        try:
            while not stopped.wait(0.1):
                with Session(database[0]) as db:
                    claimed = broker_review.claim(db)
                if claimed:
                    with httpx.Client(trust_env=False) as client:
                        response = client.get(
                            f"{base}/internal/openclaw/broker-jobs/{claimed[0]}/context",
                            headers={"Authorization": "Bearer " + ready[3]["platform_token"]},
                        )
                    assert response.status_code == 200, response.text
                    with Session(database[0]) as db:
                        job = db.get(BrokerReviewJob, claimed[0])
                        result = {
                            **broker_review.result_binding(job),
                            "outcome": "allow",
                            "reason": "Offline synthetic OpenClaw bound entry approval",
                        }
                        calls.append(job.id)
                        assert broker_review.finish(
                            db, *claimed, raw=json.dumps(result), usage={"total_tokens": 12}
                        )
                with Session(database[0]) as db:
                    broker_execution.process_one(db)
        except Exception as error:
            failures.append(type(error).__name__ + ": " + str(error))

    worker_thread = threading.Thread(target=synthetic_worker, daemon=True)
    try:
        server_thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        worker_thread.start()
        result = subprocess.run(
            [browser_python, str(root / "scripts/broker_review_browser_test.py"), base],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr + str(failures)
        assert failures == [] and len(calls) == 1 and len(ready[1].submissions) == 1
        assert ready[1].positions()[0].quantity == "207"
    finally:
        stopped.set()
        worker_thread.join(timeout=10)
        server.should_exit = True
        server_thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
