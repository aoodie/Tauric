"""Production frontend plus real API/database, with offline demo broker fixtures."""

import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
from test_broker_positions import LifecycleBroker

from tauric import broker_execution, market
from tauric.app import app
from tauric.broker_contracts import BrokerQuote
from tauric.config import runtime_config
from tauric.models import now


class BrowserBroker(LifecycleBroker):
    def quote(self, symbol):
        return BrokerQuote(
            symbol=symbol, bid="150", ask="150.01", observed_at=now(), tradable=True, feed="iex"
        )

    def submit(self, order):
        value = super().submit(order)
        if order.position_effect == "open":
            value = value.model_copy(
                update={"status": "pending", "filled_quantity": "0", "average_fill_price": None}
            )
            self.orders[order.client_order_id] = value
            self.activities.pop(value.broker_order_id)
        return value

    def cancel(self, client_id):
        self.cancellations.append(client_id)
        value = self.orders[client_id].model_copy(
            update={
                "status": "cancelled",
                "filled_quantity": "1",
                "average_fill_price": "150",
                "observed_at": now(),
            }
        )
        self.orders[client_id] = value
        self.fill(value)
        return value


@pytest.mark.browser
def test_broker_execution_browser(database, signed_in, monkeypatch, tmp_path):
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
    private = tmp_path / "empty-market.json"
    private.write_text("{}")
    monkeypatch.setenv("TAURIC_MARKET_CONNECTIONS_FILE", str(private))
    runtime_config.cache_clear()
    saved = signed_in.put(
        "/api/v1/market-connections/alpaca",
        json={
            "environment": "paper",
            "credential_action": "replace",
            "key": "fixture-key",
            "secret": "fixture-secret",
            "expected_revision": 0,
        },
    )
    assert saved.status_code == 200
    broker = BrowserBroker()
    monkeypatch.setattr(broker_execution, "adapter", lambda *args, **kwargs: broker)

    def offline(*args, **kwargs):
        raise HTTPException(503, "Offline browser fixture; no provider calls.")

    for provider in market.PROVIDERS.values():
        monkeypatch.setattr(provider, "collect", offline)
    routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    runtime_config().allowed_origins = [f"http://127.0.0.1:{port}"]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        result = subprocess.run(
            [
                browser_python,
                str(root / "scripts/broker_execution_browser_test.py"),
                f"http://127.0.0.1:{port}",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(broker.submissions) == 2 and len(broker.cancellations) == 1
        assert broker.positions()[0].quantity == "7"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
