"""Executable-side regression cases for the reproduced FX audit discrepancy."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from tauric import replay_engine
from tauric.research_contracts import ReplayParameters


def diagnostic():
    source = Path(__file__).resolve().parent / "fixtures/fx-stop-replay.json"
    data = json.loads(source.read_text())
    return data["candles"], ReplayParameters.model_validate(data["parameters"])


def test_bid_only_touch_exits_at_executable_stop_without_second_spread():
    candles, params = diagnostic()
    result = replay_engine.simulate(candles, params)
    trade = result.trades[0]
    assert trade["exit_reason"] == "stop_intrabar_close_time_assumed"
    assert Decimal(trade["exit_price"]) == Decimal("1.1088")
    assert Decimal(result.metrics.net_pnl) < 0
    assert Decimal(result.final_equity) == Decimal(params.starting_balance) + Decimal(
        result.metrics.net_pnl
    )


@pytest.mark.parametrize("slippage", ["0", "5"])
def test_bid_open_gap_precedes_queued_exit_and_uses_actual_bid(slippage):
    candles, params = diagnostic()
    candles = copy.deepcopy(candles)
    # Midpoint is above the stop while a wider spread takes the bid below it.
    entry_bar = next(bar for bar in candles if Decimal(bar["bid"]["low"]) == Decimal("1.1087"))
    entry_bar["bid"]["low"] = "1.1089"
    target = candles[candles.index(entry_bar) + 1]
    target["bid"].update(open="1.1086", low="1.1085")
    params = params.model_copy(update={"slippage_bps": slippage})
    result = replay_engine.simulate(candles, params)
    trade = result.trades[0]
    assert trade["exit_reason"] == "stop_gap"
    assert Decimal(trade["exit_price"]) == Decimal("1.1086") * (1 - Decimal(slippage) / 10000)


def test_bid_remains_above_stop_does_not_invent_fill():
    candles, params = diagnostic()
    candles = copy.deepcopy(candles)
    for bar in candles:
        if Decimal(bar["bid"]["low"]) == Decimal("1.1087"):
            bar["bid"]["low"] = "1.1089"
    result = replay_engine.simulate(candles, params)
    assert result.trades[0]["exit_reason"] == "end_of_dataset_close"
