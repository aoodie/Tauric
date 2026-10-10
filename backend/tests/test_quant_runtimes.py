"""Opt-in, real engines with synthetic prices and a disposable database."""

import os
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session
from test_quant_lab import experiment, save_dataset

from tauric import market, quant_worker
from tauric.market_calendars import default_context, periods
from tauric.quant_contracts import ExperimentCreate

pytestmark = pytest.mark.skipif(
    os.getenv("TAURIC_TEST_QUANT_RUNTIMES") != "1",
    reason="Separate Qlib and LEAN runtimes are optional",
)


def run(database, identity, kind, tmp_path):
    root = Path(__file__).resolve().parents[2]
    with Session(database[0]) as db:
        claimed = quant_worker.claim(db, kind)
    assert claimed and claimed[0] == identity
    token, job = claimed[1:]
    args = SimpleNamespace(engine=kind, artifacts=tmp_path, python=root / ".qlib-venv/bin/python")
    result = quant_worker.execute(identity, token, job, args)
    with Session(database[0]) as db:
        assert quant_worker.finish(db, identity, token, result=result)
    return result


def test_real_qlib_to_lean_handoff(signed_in, database, tmp_path):
    source = save_dataset(signed_in, database)
    request = experiment(source).model_dump(mode="json")
    request["slippage_bps"] = (
        "0"  # This fixture's small positive forecasts exceed a zero extra-cost threshold.
    )
    trained = signed_in.post("/api/v1/research/lab/experiments", json=request)
    assert trained.status_code == 201, trained.text
    model = trained.json()["id"]
    result = run(database, model, "qlib", tmp_path)
    assert result["training_samples"] > 20 and result["metrics"]["test"]["samples"] > 20
    assert result["forecast"]["probability"] is None
    assert result["provenance"]["feature_checksum"]
    execution = {**request, "request_id": str(uuid4()), "engine": "lean", "model_run_id": model}
    # Changing one split rejects a potentially leaked or mismatched model handoff.
    altered = {**execution, "lookback_bars": 13}
    assert signed_in.post("/api/v1/research/lab/experiments", json=altered).status_code == 422
    queued = signed_in.post("/api/v1/research/lab/experiments", json=execution)
    assert queued.status_code == 201, queued.text
    tested = run(database, queued.json()["id"], "lean", tmp_path)
    assert tested["fills"] and tested["equity_curve"]
    assert tested["fills"][0]["time"] > request["validation_end"]
    assert tested["authority"] == "research_only"
    assert tested["provenance"]["dataset_checksum"] == result["provenance"]["dataset_checksum"]


def test_real_yen_account_and_overnight_financing(signed_in, database, tmp_path):
    instrument = "oanda:fx:USD_JPY"
    with Session(database[0]) as db:
        context = default_context(market.instrument(db, instrument))
    grid = list(
        periods(
            context,
            "15m",
            datetime(2025, 1, 5, 22, tzinfo=UTC),
            datetime(2025, 1, 10, 12, tzinfo=UTC),
        )
    )[:360]
    candles = []
    for i, (opening, closing) in enumerate(grid):
        price = 110 + i * 0.01
        row = {
            "open_time": opening.isoformat(),
            "close_time": closing.isoformat(),
            "open": f"{price:.5f}",
            "high": f"{price + 0.02:.5f}",
            "low": f"{price - 0.02:.5f}",
            "close": f"{price + 0.01:.5f}",
            "closed": True,
            "volume": "100",
        }
        row["bid"] = {k: f"{float(row[k]) - 0.005:.5f}" for k in ("open", "high", "low", "close")}
        row["ask"] = {k: f"{float(row[k]) + 0.005:.5f}" for k in ("open", "high", "low", "close")}
        candles.append(row)
    saved = signed_in.post(
        "/api/v1/research/datasets/import",
        json={
            "request_id": str(uuid4()),
            "name": "Yen rollover fixture",
            "instrument_id": instrument,
            "interval": "15m",
            "source": "Synthetic increasing quotes",
            "candles": candles,
            "data_context": context.model_dump(mode="json"),
            "reason": "Verify quote currency and financing",
        },
    )
    assert saved.status_code == 201, saved.text
    request = ExperimentCreate(
        request_id=uuid4(),
        dataset_id=saved.json()["id"],
        name="Yen financing test",
        hypothesis="Explicit overnight costs reduce final equity",
        reason="Verify real execution costs",
        engine="lean",
        train_end=grid[159][1],
        validation_end=grid[249][1],
        test_end=grid[-1][1],
        slippage_bps="0",
        asset_costs={"asset_type": "fx", "spread_bps": "0", "financing_daily_bps": "1"},
    )
    queued = signed_in.post(
        "/api/v1/research/lab/experiments", json=request.model_dump(mode="json")
    )
    assert queued.status_code == 201, queued.text
    result = run(database, queued.json()["id"], "lean", tmp_path)
    assert result["currency"] == "JPY" and result["financing"] > 0
    assert len(result["fills"]) == 2
    entry, exit = result["fills"]
    expected = (
        entry["quantity"] * (exit["price"] - entry["price"]) - result["fees"] - result["financing"]
    )
    assert result["net_pnl"] == pytest.approx(expected, abs=0.01)
