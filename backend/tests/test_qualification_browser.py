"""Built qualification UI on a disposable database; synthetic offline native verdict."""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_paper import setup as setup
from test_portfolio_chat import portfolios_ready as portfolios_ready
from test_qualification import evidence, finish
from test_research import import_request
from test_strategy_replay import register

from tauric import analysis, qualification
from tauric.app import app
from tauric.config import runtime_config
from tauric.models import AnalysisJob, Owner, TradingRecord
from tauric.qualification_models import QualificationProtocol

pytest_plugins = ["test_chat"]


@pytest.mark.browser
def test_built_qualification(ready, database, portfolios_ready):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required for the optional browser test.")
    first, second, *_ = portfolios_ready
    saved = register(ready, risk_percent="1", quantity_step="0.00001")
    r = ready.post("/api/v1/research/datasets/import", json=import_request(evidence()))
    assert r.status_code == 201, r.text
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
        before = db.scalar(select(func.count()).select_from(TradingRecord))
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
                    analysis.heartbeat(db, "0.5.2")
                    job = db.scalar(select(AnalysisJob).where(AnalysisJob.status == "queued"))
                    if job:
                        assert job.request["mode"] == "strategy_qualification"
                        protocol = db.get(
                            QualificationProtocol,
                            job.request["qualification_context"]["protocol_id"],
                        )
                        value = qualification.view(
                            db, db.scalar(select(Owner)), protocol
                        ).model_dump(mode="json")
                    else:
                        value = None
                if value:
                    finish(database, value)
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
                str(root / "scripts/qualification_browser_test.py"),
                url,
                first["id"],
                second["id"],
                saved["id"],
                r.json()["id"],
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not errors, errors
        with Session(database[0]) as db:
            assert db.scalar(select(func.count()).select_from(TradingRecord)) == before
            assert db.scalar(select(func.count()).select_from(QualificationProtocol)) == 1
    finally:
        stopped.set()
        worker.join(timeout=10)
        server.should_exit = True
        server_thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
