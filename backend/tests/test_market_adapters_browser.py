"""Built FX/stock UI with isolated PostgreSQL and explicit offline adapter fixtures."""

import json
import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from forex_supplement_fixture import fixture as supplement_fixture
from sqlalchemy.orm import Session
from test_market_adapters import FX, bar, dt, spec, stocks_context
from test_native_fx import daily_fixture, policy_fixture

from tauric import (
    analysis,
    forex_macro,
    forex_sources,
    market,
    native_fx,
    tradingview_connection,
    tradingview_sources,
)
from tauric.app import app
from tauric.config import runtime_config
from tauric.market_calendars import default_context, periods
from tauric.models import AnalysisJob
from tauric.native_runner import artifact


@pytest.mark.browser
@pytest.mark.parametrize(
    "script",
    [
        "market_adapters_browser_test.py",
        "market_connections_browser_test.py",
        "stock_daily_browser_test.py",
        "forex_daily_browser_test.py",
    ],
)
def test_built_fx_stock_data_and_costs(database, monkeypatch, tmp_path, script):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required for the browser check")
    if not (root / "frontend/dist/index.html").exists():
        pytest.skip("Build the frontend first")
    connection_file = tmp_path / "market-data.json"
    connection_file.write_text(
        json.dumps({"oanda": {"token": "offline-no-provider-call"}})
        if script == "forex_daily_browser_test.py"
        else "{}"
    )
    monkeypatch.setenv("TAURIC_MARKET_CONNECTIONS_FILE", str(connection_file))
    runtime_config.cache_clear()

    def fx_collect(instrument, interval, *, config=None):
        if interval == "1d":
            return daily_fixture(instrument)
        context = default_context(spec(FX))
        candles = [
            bar(op, close, price, sides=True)
            for (op, close), price in zip(
                periods(context, interval, dt("2026-10-01T09:00"), dt("2026-10-01T10:30")),
                ("10", "8", "6", "10", "12", "4", "4"),
                strict=True,
            )
        ]
        stamp = datetime.now(UTC)
        context.warnings.append("Explicit offline browser fixture; no provider connection")
        return candles, stamp, stamp, context.model_dump(mode="json")

    def stock_collect(instrument, interval, *, config=None):
        context = stocks_context([("2026-10-01T09:30", "2026-10-01T16:00")])
        candles = [
            bar(op, close, price)
            for (op, close), price in zip(
                periods(context, interval, dt("2026-10-01T09:30"), dt("2026-10-01T11:00")),
                ("10", "8", "6", "10", "12", "4", "4"),
                strict=True,
            )
        ]
        stamp = datetime.now(UTC)
        context.warnings.append("Explicit offline browser fixture; no provider connection")
        return candles, stamp, stamp, context.model_dump(mode="json")

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", fx_collect)
    monkeypatch.setattr(market.PROVIDERS["alpaca_stocks"], "collect", stock_collect)
    monkeypatch.setattr(forex_macro, "collect", policy_fixture)

    def supplemental(db, spec, *, deadline=None):
        result = supplement_fixture(spec)
        if tradingview_connection.saved(db)[0].get("access_token"):
            result["data"]["version"] = forex_sources.TRADINGVIEW_VERSION
            result["data"]["coverage"] = (
                "TradingView subscribed economic calendar and verified rate-series fixture."
            )
            for source in result["data"]["sources"][:4]:
                source.update(provider="TradingView official MCP", url=tradingview_sources.URL)
            result["checksum"] = forex_macro.digest(result["data"])
        return result

    monkeypatch.setattr(forex_sources, "collect", supplemental)
    monkeypatch.setattr(tradingview_connection, "metadata", lambda: None)

    def oauth_request(method, url, **kwargs):
        if url == tradingview_connection.REGISTER:
            return {
                "client_id": "offline-browser-client",
                "redirect_uris": kwargs["json"]["redirect_uris"],
                "token_endpoint_auth_method": "none",
            }
        assert url == tradingview_connection.TOKEN
        return {
            "access_token": "offline-browser-access",
            "refresh_token": "offline-browser-refresh",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "mcp:read",
        }

    monkeypatch.setattr(tradingview_connection, "request_json", oauth_request)
    monkeypatch.setattr(
        tradingview_sources,
        "collect",
        lambda db, spec: supplemental(db, spec)["data"]["sources"][:4],
    )
    if script in ("stock_daily_browser_test.py", "forex_daily_browser_test.py"):
        profiles = tmp_path / "profiles.json"
        profiles.write_text(
            json.dumps(
                {
                    "cloud": {
                        "provider": "openai",
                        "quick_model": "offline-quick",
                        "deep_model": "offline-deep",
                        "api_key": "offline-no-model-calls",
                    }
                }
            )
        )
        monkeypatch.setenv("TAURIC_MODEL_PROFILES_FILE", str(profiles))
        runtime_config.cache_clear()
        original_enqueue = analysis.enqueue

        def offline_enqueue(db, owner, body, **kwargs):
            result = original_enqueue(db, owner, body, **kwargs)
            assert analysis.claim(db) == result.id
            job = db.get(AnalysisJob, result.id)
            reports = {
                "market_report": f"Offline browser fixture for {job.request['instrument']['native_symbol']}",
                "final_trade_decision": "Synthetic Hold report; no model call or trading instruction.",
            }
            source = None
            if job.request["instrument"]["asset_type"] == "fx":
                source = native_fx.evidence(
                    db, job.request["instrument"], job.request["forex_context"]
                )
                llm = SimpleNamespace(
                    invoke=lambda messages: SimpleNamespace(
                        content=f"Offline Forex fixture for {source['pair']}", tool_calls=[]
                    )
                )
                reports = native_fx.research(llm, llm, source)
            payload = artifact(
                reports,
                "REVIEW" if source else "Hold",
                job.request,
                {},
                "0.5.2",
            )
            if source:
                payload.update(
                    adapter_version=native_fx.ADAPTER_VERSION,
                    forex_evidence=source,
                    runtime="Tauric Forex daily extension / TradingAgents LLM clients",
                )
            assert analysis.finish(db, job.id, payload)
            return analysis.view(db, job)

        monkeypatch.setattr(analysis, "enqueue", offline_enqueue)
        with Session(database[0]) as db:
            analysis.heartbeat(db, "0.5.2")
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
            [python, str(root / "scripts" / script), url],
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
