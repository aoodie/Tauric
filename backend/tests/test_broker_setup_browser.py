"""Built setup-review UI, real API/database and explicit offline model/broker."""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from test_broker_setup import configured, lifecycle, setup  # noqa: F401
from test_native_broker_setup import Model

from tauric import analysis, broker_setup
from tauric.app import app
from tauric.config import runtime_config
from tauric.models import AnalysisJob
from tauric.native_broker_setup import assess
from tauric.native_runner import artifact


@pytest.mark.browser
def test_broker_setup_browser(database, setup, monkeypatch):  # noqa: F811
    root = Path(__file__).resolve().parents[2]
    browser_python = shutil.which("python3")
    if (
        not browser_python
        or subprocess.run(
            [browser_python, "-c", "import playwright"], capture_output=True
        ).returncode
    ):
        pytest.skip("Python Playwright is needed for the optional browser check.")
    if not (root / "frontend/dist/index.html").exists():
        pytest.skip("Build the frontend first.")
    body, broker, _, _ = setup
    model = Model()
    routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    runtime_config().allowed_origins = [f"http://127.0.0.1:{port}"]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    server_thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    stopped = threading.Event()
    failures = []

    def synthetic_worker():
        try:
            while not stopped.wait(0.1):
                with Session(database[0]) as db:
                    job_id = analysis.claim(db)
                    if not job_id:
                        continue
                    job = db.get(AnalysisJob, job_id)
                    broker_setup.validate_admission(db, job)
                    payload = artifact(
                        {"final_trade_decision": "Original synthetic daily BUY report"},
                        "BUY",
                        job.request,
                        job.request["settings"],
                        "0.5.2",
                    )
                    payload["broker_setup_assessment"] = assess(
                        model,
                        job.request["broker_context"],
                        payload["reports"],
                        payload["native_rating"],
                    )
                    assert analysis.finish(db, job_id, payload=payload)
        except Exception as error:
            failures.append(type(error).__name__)

    worker_thread = threading.Thread(target=synthetic_worker, daemon=True)
    try:
        server_thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        worker_thread.start()
        result = subprocess.run(
            [
                browser_python,
                str(root / "scripts/broker_setup_browser_test.py"),
                f"http://127.0.0.1:{port}",
                body["strategy_version_id"],
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr + str(failures)
        assert failures == [] and len(model.calls) == 1
        assert broker.submissions == [] and broker.cancellations == []
        assert broker.positions()[0].quantity == "7"
    finally:
        stopped.set()
        worker_thread.join(timeout=10)
        server.should_exit = True
        server_thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
