import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import paper, replay_engine
from tauric.models import MarketSnapshot, PaperAccount, TradingRecord
from tauric.research_contracts import ReplayParameters

INSTRUMENT = "binance:spot:BTCUSDT"


def bars(closes=(10, 8, 6, 10, 12, 4, 4)):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        {
            "open_time": (start + timedelta(minutes=i)).isoformat(),
            "close_time": (
                start + timedelta(minutes=i + 1) - timedelta(milliseconds=1)
            ).isoformat(),
            "open": str(close),
            "high": str(close + 1),
            "low": str(max(1, close - 1)),
            "close": str(close),
            "volume": "10",
            "closed": True,
        }
        for i, close in enumerate(closes)
    ]


def params(**values):
    return ReplayParameters(
        fast_window=1,
        slow_window=2,
        starting_balance="1000",
        allocation_percent="100",
        risk_percent="100",
        stop_percent="50",
        quantity_step="1",
        fee_bps="100",
        slippage_bps="100",
        **values,
    )


def test_exact_costs_quantity_gap_stop_and_cash_reconciliation():
    candles = bars()
    candles[4].update(open="20", high="21", low="12")
    candles[5].update(open="7", high="8")
    result = replay_engine.simulate(candles, params())
    entry, exit = result.fills
    assert entry["signal_bar_index"] == 3
    assert entry["time"] == candles[4]["open_time"]
    assert entry["price"] == "20.20" and entry["quantity"] == "49"
    assert Decimal(entry["fee"]) == Decimal("9.898")
    assert exit["cause"] == "stop_gap" and Decimal(exit["price"]) == Decimal("6.93")
    assert Decimal(result.metrics.net_pnl) == Decimal("-663.5237")
    assert Decimal(result.final_equity) == Decimal("336.4763")
    assert Decimal(result.metrics.total_fees) == Decimal("13.2937")
    assert result.metrics.closed_trades == 1 and result.metrics.losses == 1
    assert Decimal(result.max_drawdown_percent) == Decimal("66.35237")


def test_intrabar_stops_and_last_bar_liquidation_are_explicit():
    candles = bars((10, 8, 6, 10, 12, 13, 14))
    result = replay_engine.simulate(candles, params())
    assert result.trades[0]["exit_reason"] == "end_of_dataset_close"
    candles[4]["low"] = "5"
    result = replay_engine.simulate(candles, params())
    assert result.trades[0]["exit_reason"] == "stop_intrabar_close_time_assumed"
    assert result.fills[1]["time"] == candles[4]["close_time"]


def test_future_candles_do_not_change_past_signals_or_entry_fills():
    candles = bars((10, 8, 6, 10, 12, 13, 14, 15, 16))
    changed = copy.deepcopy(candles)
    for bar in changed[6:]:
        bar.update(open="500", high="510", low="490", close="500")
    left, right = [replay_engine.simulate(x, params()) for x in (candles, changed)]
    assert [s for s in left.signals if s["bar_index"] < 6] == [
        s for s in right.signals if s["bar_index"] < 6
    ]
    assert left.fills[0] == right.fills[0]
    # A crossover on the final close has no later open and cannot fill.
    last = replay_engine.simulate(bars((10, 8, 6, 10)), params())
    assert last.signals[-1]["execution"] == "no_next_bar"
    assert last.fills == []


def test_no_trades_and_untradable_step_have_honest_metrics():
    result = replay_engine.simulate(bars((10, 10, 10, 10, 10)), params())
    assert result.metrics.win_rate_percent is None and result.metrics.profit_factor is None
    assert result.final_equity == "1000" and result.max_drawdown_percent == "0"
    p = params().model_copy(update={"quantity_step": "10000"})
    result = replay_engine.simulate(bars(), p)
    assert result.skipped_entries == 1 and result.fills == []


@pytest.mark.parametrize("value", ["NaN", "1e3", 10, "0", "-1"])
def test_replay_rejects_invalid_money(value):
    with pytest.raises(ValidationError):
        ReplayParameters(starting_balance=value)


def import_request(candles=None):
    return {
        "request_id": str(uuid4()),
        "name": "Costed crossover dataset",
        "instrument_id": INSTRUMENT,
        "interval": "1m",
        "source": "Explicit offline test fixture",
        "candles": candles or bars(),
        "reason": "Verify retrospective research",
    }


def run_request(dataset_id):
    return {
        "request_id": str(uuid4()),
        "dataset_id": dataset_id,
        "name": "Fee-aware crossover",
        "hypothesis": "A rising fast average may signal momentum",
        "parameters": params().model_dump(),
        "reason": "Verify replay isolation and persistence",
    }


def test_research_requires_session_and_csrf(client):
    assert client.get("/api/v1/research").status_code == 401
    assert client.get("/api/v1/analytics").status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert client.post("/api/v1/research/datasets/import", json=import_request()).status_code == 403


def test_persisted_replay_export_idempotency_immutability_and_isolation(signed_in, database):
    request = import_request()
    response = signed_in.post("/api/v1/research/datasets/import", json=request)
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["availability"] == "retrospective" and dataset["bar_count"] == 7
    again = signed_in.post("/api/v1/research/datasets/import", json=request)
    assert again.json()["id"] == dataset["id"]
    conflict = signed_in.post(
        "/api/v1/research/datasets/import", json={**request, "name": "Different dataset"}
    )
    assert conflict.status_code == 409
    request = run_request(dataset["id"])
    response = signed_in.post("/api/v1/research/runs", json=request)
    assert response.status_code == 201, response.text
    run = response.json()
    assert signed_in.post("/api/v1/research/runs", json=request).json()["id"] == run["id"]
    assert (
        signed_in.post(
            "/api/v1/research/runs", json={**request, "hypothesis": "Changed hypothesis"}
        ).status_code
        == 409
    )
    state = signed_in.get("/api/v1/research").json()
    assert state["runs"][0]["result"] == run["result"]
    export = signed_in.get(f"/api/v1/research/runs/{run['id']}").json()["data"]
    assert paper.checksum(export["payload"]) == export["checksum"]
    assert paper.checksum(export["payload"]["engine_sources"]) == run["engine_checksum"]
    with Session(database[0]) as db:
        assert list(db.scalars(select(PaperAccount))) == []
        assert list(db.scalars(select(TradingRecord))) == []
        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE research_records SET name='tampered' WHERE id=:id"), {"id": run["id"]}
            )
        db.rollback()
    with database[1].begin() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(
                text("DELETE FROM research_records WHERE id=:id"), {"id": dataset["id"]}
            )


@pytest.mark.parametrize("change", ["gap", "future", "duration", "overlap", "ohlc", "unfinished"])
def test_bad_dataset_cannot_be_replayed(signed_in, change):
    candles = bars()
    if change == "gap":
        candles.pop(2)
    elif change == "future":
        candles[0]["close_time"] = datetime(2100, 1, 1, tzinfo=UTC).isoformat()
    elif change == "duration":
        candles[0]["close_time"] = candles[0]["open_time"]
    elif change == "overlap":
        candles[1]["open_time"] = candles[0]["open_time"]
    elif change == "ohlc":
        candles[0]["high"] = "1"
    else:
        candles[0]["closed"] = False
    response = signed_in.post("/api/v1/research/datasets/import", json=import_request(candles))
    assert response.status_code == 422, response.text
    assert signed_in.get("/api/v1/research").json()["datasets"] == []


def test_snapshot_capture_excludes_open_bar_and_preserves_receipt_provenance(signed_in, database):
    candles = bars()
    candles[-1]["closed"] = False
    payload = {
        "candles": candles,
        "source": "binance_spot_public",
        "availability": "observed_at_receipt",
    }
    with Session(database[0]) as db:
        stamp = datetime(2025, 1, 2, tzinfo=UTC)
        row = MarketSnapshot(
            instrument_id=INSTRUMENT,
            interval="1m",
            received_at=stamp,
            provider_time=stamp,
            checksum=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            data=payload,
        )
        db.add(row)
        db.commit()
        snapshot_id = row.id
    response = signed_in.post(
        "/api/v1/research/datasets/capture",
        json={
            "request_id": str(uuid4()),
            "name": "Saved snapshot",
            "snapshot_id": snapshot_id,
            "reason": "Capture known closed bars",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["bar_count"] == 6
    data = signed_in.get(f"/api/v1/research/datasets/{response.json()['id']}").json()["data"][
        "payload"
    ]
    assert data["provenance"]["original_availability"] == "observed_at_receipt"
    assert data["availability"] == "retrospective"


def test_wrong_owner_cannot_read_or_run_dataset(signed_in, database):
    dataset = signed_in.post("/api/v1/research/datasets/import", json=import_request()).json()
    with Session(database[0]) as db:
        owner = SimpleNamespace(id=str(uuid4()))
        detached_id = dataset["id"]
        from fastapi import HTTPException

        from tauric import research

        with pytest.raises(HTTPException) as error:
            research.owned(db, owner, detached_id, "dataset")
        assert error.value.status_code == 404
        db.rollback()
