"""Built stock controls and reviewed paper journal on an isolated local server."""

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
from test_governance import ready as ready
from test_paper import setup as setup
from test_scanner import body, scan
from test_stock_paper import planned
from test_stock_paper import stock_clock as stock_clock
from test_stock_paper import stocks as stocks

from tauric.app import app
from tauric.config import runtime_config


@pytest.mark.browser
def test_built_stock_workflow(database, signed_in, ready, stocks, stock_clock):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright required for built browser verification")
    plan = planned(signed_in, database, ready, stocks)
    response = signed_in.put(
        "/api/v1/scanners/profiles", json=body(stocks[2], universe=["alpaca:equity:AAPL"])
    )
    assert response.status_code == 200, response.text
    scan(signed_in, response.json())
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
        metadata = json.dumps(
            {
                "deployment_id": stocks[1]["id"],
                "proposal_id": plan["payload"]["proposal_id"],
                "account_id": stocks[0]["id"],
            }
        )
        result = subprocess.run(
            [python, str(root / "scripts/stock_paper_browser_test.py"), url, metadata],
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
