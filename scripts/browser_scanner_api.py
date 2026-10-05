"""Isolated scanner UI fixture with explicitly synthetic candles and no model worker."""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
if not fixture["database_name"].startswith("tauric_browser_"):
    raise SystemExit("A disposable scanner fixture is required.")
os.environ["TAURIC_DATABASE_URL"] = fixture["database_url"]
os.environ["TAURIC_MODEL_KEY_FILE"] = str(root / "artifacts/browser-model-key")
os.environ["TAURIC_OPENCLAW_PRIVATE_FILE"] = str(root / ".env.openclaw-private.json")
os.environ["TAURIC_SECURE_COOKIES"] = "false"
os.environ["TAURIC_ALLOWED_ORIGINS"] = (
    '["http://127.0.0.1:5173","http://localhost:5173"]'
)
sys.path.insert(0, str(root / "backend"))
from tauric import market
from tauric.app import app


def fixture_candles(spec, interval):
    current = datetime.now(timezone.utc)
    length = market.INTERVAL_SECONDS[interval]
    boundary = datetime.fromtimestamp(
        int(current.timestamp()) // length * length, timezone.utc
    )
    bars = []
    for offset in range(-8, 1):
        start = boundary + timedelta(seconds=offset * length)
        end = start + timedelta(seconds=length, milliseconds=-1)
        values = {
            "open": "100",
            "high": "103",
            "low": "99",
            "close": "101",
            "volume": "10",
        }
        if offset == -1:
            values.update(high="111", close="110", volume="50")
        if offset == 0:
            values.update(high="1000", close="999", volume="1000")
        bars.append(
            {
                "open_time": start.isoformat(),
                "close_time": end.isoformat(),
                **values,
                "closed": end <= current,
            }
        )
    return bars, current, current


market.PROVIDERS["binance_spot_public"].collect = fixture_candles
print(
    "Scanner browser fixture: synthetic candle data; no native model worker.",
    flush=True,
)
uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
