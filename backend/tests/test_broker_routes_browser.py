"""Built UI route recovery, with disposable broker and model fixtures."""

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
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_broker_routes import configured as configured
from test_broker_routes import lifecycle as lifecycle
from test_broker_routes import ready as ready
from test_broker_routes import setup as setup
from test_broker_setup import finish as native_finish

from tauric import analysis, broker_positions, broker_review, broker_routes
from tauric.app import app
from tauric.broker_models import BrokerOrderIntent, BrokerReviewJob, BrokerRouteWorkflow
from tauric.config import runtime_config
from tauric.models import AnalysisJob, Owner


@pytest.mark.browser
def test_automatic_routes_browser(database, ready, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is needed for the browser check.")
    monkeypatch.setattr(broker_routes, "now", lambda: ready[2])
    monkeypatch.setattr(broker_positions, "now", lambda: ready[2])
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
            while not stopped.wait(0.15):
                with Session(database[0]) as db:
                    broker_routes.discover_one(db)
                with Session(database[0]) as db:
                    native = db.scalar(select(AnalysisJob).where(AnalysisJob.status == "queued"))
                    pending = analysis.view(db, native).model_dump(mode="json") if native else None
                if pending:
                    native_finish(database, pending)
                with Session(database[0]) as db:
                    flow = db.scalar(
                        select(BrokerRouteWorkflow).where(
                            BrokerRouteWorkflow.next_due_at.is_not(None)
                        )
                    )
                    if flow:
                        flow.next_due_at = ready[2]
                        db.commit()
                    broker_routes.advance_one(db)
                with Session(database[0]) as db:
                    claimed = broker_review.claim(db)
                if claimed:
                    response = httpx.get(
                        f"{base}/internal/openclaw/broker-jobs/{claimed[0]}/context",
                        headers={"Authorization": "Bearer " + ready[3]["platform_token"]},
                        trust_env=False,
                    )
                    assert response.status_code == 200, response.text
                    with Session(database[0]) as db:
                        job = db.get(BrokerReviewJob, claimed[0])
                        calls.append(job.id)
                        assert broker_review.finish(
                            db,
                            *claimed,
                            raw=json.dumps(
                                {
                                    **broker_review.result_binding(job),
                                    "outcome": "allow",
                                    "reason": "Offline exact automatic risk-sized demo approval",
                                }
                            ),
                        )
                with Session(database[0]) as db:
                    broker_review.deliver_one(db)
                    entry = db.scalar(select(BrokerOrderIntent))
                    if entry:
                        broker_positions.refresh(db, db.get(Owner, entry.owner_id), entry.id)
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
            [python, str(root / "scripts/broker_routes_browser_test.py"), base],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr + str(failures)
        assert failures == [] and len(calls) == 1 and len(ready[1].submissions) == 1
        assert ready[1].positions()[0].quantity == "40"
    finally:
        stopped.set()
        worker_thread.join(timeout=10)
        server.should_exit = True
        server_thread.join(timeout=10)
        app.router.routes[:] = routes
