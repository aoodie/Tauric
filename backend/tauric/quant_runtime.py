"""Fixed, separately installed research runtimes. No arbitrary submitted code."""

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from . import quant_features

LEAN_IMAGE = (
    "quantconnect/lean@sha256:7a72e2b753bada616422d47ddb80857e9544d1d68210fa86fe453e482a39833b"
)
RUNNERS = Path(__file__).resolve().parents[1] / "runners"


def prepare_lean(folder, job):
    """Replay native tick quotes/trades at observed opens and closes, without inventing an intrabar path."""
    request, spec, bars = job["request"], job["instrument"], job["candles"]
    fx = spec["asset_type"] == "fx"
    ticker = spec["symbol"].replace("/", "").replace("_", "").lower()
    if not re.fullmatch(r"[a-z][a-z0-9.-]{0,19}", ticker):
        raise ValueError("Instrument symbol cannot be represented in LEAN")
    zone = ZoneInfo("UTC" if fx else "America/New_York")
    # Only test rows may generate orders. Earlier bars seed indicators, not reported performance.
    indices = {r["index"] for r in job["features"]["partitions"]["test"]}
    scores = {
        p["index"]: p["prediction"]
        for p in job.get("model_predictions") or []
        if p["partition"] == "test"
    }
    plan, files, selected = {}, {}, []
    cutoff = quant_features.stamp(request["validation_end"])
    if request.get("evaluation_start"):
        cutoff = max(cutoff, quant_features.stamp(request["evaluation_start"]))
        indices = {i for i in indices if quant_features.stamp(bars[i]["close_time"]) > cutoff}
    end = quant_features.stamp(request["test_end"])
    research_plan = None
    if request.get("research_strategy"):
        from .discovery_signals import plan as strategy_plan

        research_plan = strategy_plan(
            bars,
            request["research_strategy"],
            job.get("context_candles"),
            start_index=min(indices) if indices else len(bars),
        )
    for i, bar in enumerate(bars):
        opening, closing = (
            quant_features.stamp(bar["open_time"]),
            quant_features.stamp(bar["close_time"]),
        )
        if opening < cutoff or closing > end:
            continue
        selected.append(bar)
        previous = i - 1
        target = None
        if previous in indices:
            if research_plan is not None:
                target = research_plan[previous]
            elif job.get("model_predictions") is not None:
                # Trading is a separate test, with costs; Qlib's fit never sees these labels.
                threshold = (float(request["fee_bps"]) + float(request["slippage_bps"])) * 2 / 10000
                target = scores.get(previous, -1) > threshold
            elif i >= request["slow_window"]:
                closes = [float(b["close"]) for b in bars[i - request["slow_window"] : i]]
                target = sum(closes[-request["fast_window"] :]) / request["fast_window"] > sum(
                    closes
                ) / len(closes)
        for instant, field in ((opening, "open"), (closing - timedelta(milliseconds=1), "close")):
            local = instant.astimezone(zone)
            day = local.strftime("%Y%m%d")
            ms = (
                (local.hour * 60 + local.minute) * 60 + local.second
            ) * 1000 + local.microsecond // 1000
            if fx:
                line = f"{ms},{bar['bid'][field]},{bar['ask'][field]}"
            else:
                price = int(Decimal(bar[field]) * 10000)
                line = f"{ms},{price},1,X,N,0"
            files.setdefault(day, []).append(line)
            if field == "open" and target is not None:
                plan[instant.isoformat()] = target if isinstance(target, dict) else bool(target)
    if len(selected) < 3:
        raise ValueError("The test interval needs whole historical bars")
    market = "oanda" if fx else "usa"
    root = folder / "data" / ("forex" if fx else "equity") / market
    for day, lines in files.items():
        directory = root / "tick" / ticker
        directory.mkdir(parents=True, exist_ok=True)
        kind = "quote" if fx else "trade"
        with ZipFile(directory / f"{day}_{kind}.zip", "w") as archive:
            archive.writestr(f"{day}_{ticker}_tick_{kind}.csv", "\n".join(lines) + "\n")
    if not fx:
        for kind, content in (
            (
                "map_files",
                f"{next(iter(files))},{ticker},usa\n20501231,{ticker},usa\n",
            ),
            ("factor_files", "19980101,1,1,1\n20501231,1,1,1\n"),
        ):
            (root / kind).mkdir(parents=True, exist_ok=True)
            (root / kind / f"{ticker}.csv").write_text(content)
    settings = {
        **job,
        "candles": selected,
        "ticker": ticker.upper(),
        "plan": plan,
        "last_tick": (
            quant_features.stamp(selected[-1]["close_time"]) - timedelta(milliseconds=1)
        ).isoformat(),
    }
    (folder / "input.json").write_text(json.dumps(settings, allow_nan=False))
    shutil.copy(RUNNERS / "lean_algorithm.py", folder / "algorithm.py")
    config = {
        "environment": "backtesting",
        "algorithm-type-name": "TauricResearch",
        "algorithm-language": "Python",
        "algorithm-location": "/job/algorithm.py",
        "data-folder": "/job/data",
        "results-destination-folder": "/job/results",
        "object-store-root": "/job/storage",
        "close-automatically": True,
        "live-mode": False,
        "force-exchange-always-open": True,
        "log-handler": "QuantConnect.Logging.ConsoleLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "map-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskMapFileProvider",
        "factor-file-provider": "QuantConnect.Data.Auxiliary.LocalDiskFactorFileProvider",
        "data-provider": "QuantConnect.Lean.Engine.DataFeeds.DefaultDataProvider",
        "data-channel-provider": "DataChannelProvider",
        "object-store": "QuantConnect.Lean.Engine.Storage.LocalObjectStore",
        "data-aggregator": "QuantConnect.Lean.Engine.DataFeeds.AggregationManager",
        "setup-handler": "QuantConnect.Lean.Engine.Setup.BacktestingSetupHandler",
        "result-handler": "QuantConnect.Lean.Engine.Results.BacktestingResultHandler",
        "data-feed-handler": "QuantConnect.Lean.Engine.DataFeeds.FileSystemDataFeed",
        "real-time-handler": "QuantConnect.Lean.Engine.RealTime.BacktestingRealTimeHandler",
        "transaction-handler": "QuantConnect.Lean.Engine.TransactionHandlers.BacktestingTransactionHandler",
        "history-provider": ["SubscriptionDataReaderHistoryProvider"],
        "job-user-id": "0",
        "api-access-token": "",
        "job-organization-id": "",
        "maximum-data-points-per-chart-series": 10000,
    }
    (folder / "config.json").write_text(json.dumps(config))


def command(engine, folder, python):
    if engine == "qlib":
        return [python, str(RUNNERS / "qlib_runner.py"), str(folder)]
    # The daemon stays on the host. The untrusted research container sees one job, no network or socket.
    prepare = "mkdir -p /job/data/market-hours /job/data/symbol-properties /job/results; cp /Lean/Data/market-hours/market-hours-database.json /job/data/market-hours/; cp /Lean/Data/symbol-properties/symbol-properties-database.csv /job/data/symbol-properties/; dotnet QuantConnect.Lean.Launcher.dll --config /job/config.json"
    return [
        "docker",
        "run",
        "--rm",
        "--name",
        "tauric-lab-" + folder.name,
        "--network",
        "none",
        "--cpus",
        "2",
        "--memory",
        "3g",
        "--pids-limit",
        "256",
        "--cap-drop",
        "ALL",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "HOME=/job",
        "--env",
        "DOTNET_CLI_HOME=/job",
        "--env",
        "OMP_NUM_THREADS=2",
        "--security-opt",
        "no-new-privileges",
        "--mount",
        f"type=bind,source={folder},target=/job",
        "--entrypoint",
        "sh",
        LEAN_IMAGE,
        "-c",
        prepare,
    ]


def probe(engine, python=sys.executable):
    args = (
        [python, "-c", "import qlib, lightgbm; print(qlib.__version__)"]
        if engine == "qlib"
        else ["docker", "image", "inspect", LEAN_IMAGE, "--format", "{{.Id}}"]
    )
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=True)
    return result.stdout.strip()[:40]
