"""Production browser bundle, offline source adapters and disposable storage."""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from test_chat import ready as ready
from test_intelligence import feeds as feeds
from test_intelligence import refresh
from test_intelligence_catalog import sync
from test_intelligence_catalog import wide_markets as wide_markets

from tauric.app import app
from tauric.config import runtime_config


@pytest.mark.browser
def test_built_intelligence(database, signed_in, ready, feeds, wide_markets):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    assert (root / "frontend/dist/index.html").exists()
    refresh(signed_in, ["binance:spot:BTCUSDT", "alpaca:equity:AAPL", "oanda:fx:EUR_USD"])
    sync(signed_in)
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
            [python, str(root / "scripts/intelligence_browser_test.py"), url],
            capture_output=True,
            text=True,
            timeout=150,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
