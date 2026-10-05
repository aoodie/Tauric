"""Synthetic candle collector for disposable process-restart verification only.

Loaded explicitly by the test Compose override. Normal services never import it.
"""

import json
import runpy
import sys
from datetime import timedelta
from pathlib import Path

from tauric import market
from tauric.models import now

STATE = Path("/fixtures/autonomy-feed-state.json")


def collect(spec, interval):
    stamp = now()
    duration = market.INTERVAL_SECONDS[interval]
    boundary = stamp.replace(second=0, microsecond=0)
    boundary -= timedelta(seconds=int(boundary.timestamp()) % duration)
    mode = json.loads(STATE.read_text())["mode"]
    candles = []
    for offset in range(-8, 1):
        start = boundary + timedelta(seconds=offset * duration)
        values = {
            "open": "100",
            "high": "103",
            "low": "90",
            "close": "101",
            "volume": "10",
        }
        if offset == -1:
            values.update(high="111", low="95", close="110", volume="50")
            if mode == "stop":
                values.update(
                    open="100", high="105", low="85", close="100", volume="10"
                )
        if offset == 0:
            values.update(
                high="111", low="95", close="110" if mode == "enter" else "100"
            )
        candles.append(
            {
                "open_time": start.isoformat(),
                "close_time": (
                    start + timedelta(seconds=duration, milliseconds=-1)
                ).isoformat(),
                **values,
                "closed": offset < 0,
            }
        )
    return candles, stamp, stamp


market.PROVIDERS["binance_spot_public"].collect = collect
module = (
    "tauric.strategy_scheduler_worker"
    if sys.argv[1] == "monitor"
    else "tauric.autonomous_worker"
)
runpy.run_module(module, run_name="__main__")
