"""Research-only OHLC protection scenarios; no provider, model or account actions."""

import copy
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from test_market_adapters import FX, STOCK, dt, spec, stocks_context
from test_strategy_replay import candles, parameters, register, version

from tauric import replay_engine
from tauric.market_calendars import default_context
from tauric.research_contracts import ReplayParameters

D = Decimal


def replay(bars=None, *, delay=0, slip="0", spread="0", price_step="1", rules=None):
    saved = version()
    if rules:
        saved.parameters.rules = saved.parameters.rules.model_copy(update=rules)
    params = parameters(saved, fee_bps="0", slippage_bps=slip).model_dump(mode="json")
    params.update(
        asset_costs={"asset_type": "equity", "spread_bps": spread},
        broker_protection={"price_step": price_step, "entry_delay_seconds": delay},
    )
    return replay_engine.simulate(bars or candles(), ReplayParameters.model_validate(params), saved)


def test_protection_frozen_before_fill_and_not_repriced_after_slippage():
    bars = candles()
    bars[4].update(high="22", low="13")
    result = replay(bars, slip="100")
    entry, exit = result.fills
    assert entry["price"] == "14.14"
    assert entry["reference_ask"] == "13"
    assert entry["stop_price"] == "9" and entry["target_price"] == "21"
    assert exit["price"] == "21"
    assert exit["time"] == bars[4]["close_time"]
    assert exit["cause"] == "protected_target_intrabar_time_assumed"
    assert result.execution_evidence.broker_parity_verified is False
    assert D(result.final_equity) == 1000 + D(result.metrics.net_pnl)


@pytest.mark.parametrize("target", [False, True])
def test_protection_covers_entry_bar_and_limit_never_fills_below_target(target):
    bars = candles()
    bars[3].update(high="22" if target else "15", low="13" if target else "8")
    result = replay(bars, slip="500")
    exit = result.fills[1]
    assert exit["time"] == bars[3]["close_time"]
    assert D(exit["price"]) == (D("21") if target else D("9") * D("0.95"))
    assert result.execution_evidence.same_bar_exits == 1


def test_both_levels_touched_is_counted_without_claiming_an_observed_path():
    bars = candles()
    bars[4].update(high="30", low="8")
    result = replay(bars)
    assert result.fills[1]["price"] == "9"
    ambiguity = result.execution_evidence.ambiguous_exits
    assert len(ambiguity) == 1 and ambiguity[0]["bar_index"] == 4
    assert ambiguity[0]["selected_exit"] == "stop"
    assert result.execution_evidence.intrabar_path_observed is False


def test_known_target_gap_precedes_later_stop_touch():
    bars = candles()
    bars[4].update(open="23", high="25", low="8")
    result = replay(bars, slip="100")
    assert result.fills[1]["cause"] == "protected_target_gap"
    assert result.fills[1]["time"] == bars[4]["open_time"]
    assert result.fills[1]["price"] == "22.77"
    assert result.execution_evidence.ambiguous_exits == []


def test_stop_gap_precedes_a_queued_channel_exit():
    bars = candles()
    bars[4].update(open="14", high="15", low="10", close="11")
    bars[5].update(open="6", low="5", high="15")
    result = replay(bars, slip="100")
    assert result.signals[1]["exit_trigger"] == "channel"
    assert result.fills[1]["cause"] == "protected_stop_gap"
    assert result.fills[1]["price"] == "5.94"
    assert result.fills[1]["time"] == bars[5]["open_time"]


def test_delay_uses_next_observed_open_without_inventing_an_intrabar_entry():
    bars = candles()
    result = replay(bars, delay=1)
    assert result.fills[0]["time"] == bars[4]["open_time"]
    assert result.fills[0]["target_price"] == "21"
    timing = result.execution_evidence.signal_to_fill
    assert timing.observations == 1
    assert timing.median_seconds == "60.001" and timing.p95_seconds == "60.001"


def test_expired_signal_does_not_enter_or_become_an_sma_trade():
    bars = candles()
    # A new breakout would be visible while the original signal is still waiting.
    bars[3].update(open="14", high="17", close="16", volume="100")
    result = replay(bars, delay=120, rules={"signal_ttl_seconds": 60})
    assert result.fills == []
    assert result.execution_evidence.expired_entries == 1
    assert result.signals[0]["execution"] == "expired_before_entry"


def test_unobserved_pending_entry_is_retained_as_unfilled_not_executed():
    result = replay(candles()[:4], delay=120)
    assert result.fills == []
    assert result.execution_evidence.unfilled_entries == 1
    assert result.execution_evidence.signal_to_fill.observations == 0


def test_quantity_and_capital_ceilings_survive_the_new_execution_model():
    result = replay()
    assert D(result.fills[0]["quantity"]) * D(result.fills[0]["price"]) <= 1000
    assert D(result.fills[0]["quantity"]) % 1 == 0
    with pytest.raises(ValueError, match="rounded protective stop"):
        replay(price_step="100")


def test_historical_parameters_and_results_do_not_acquire_new_null_fields():
    saved = version()
    params = parameters(saved)
    assert "broker_protection" not in params.model_dump(mode="json")
    old_result = replay_engine.simulate(candles(), params, saved)
    assert "execution_evidence" not in old_result.model_dump(mode="json")
    assert old_result.fills[0]["price"] == "14.14"


@pytest.mark.parametrize("config", [{"price_step": "0"}, {"price_step": "1", "entry_delay_seconds": True}, {"price_step": "1", "entry_delay_seconds": -1}])
def test_invalid_research_execution_parameters_fail_closed(config):
    saved = version()
    raw = parameters(saved).model_dump(mode="json")
    raw.update(asset_costs={"asset_type": "equity", "spread_bps": "0"}, broker_protection=config)
    with pytest.raises(ValidationError):
        ReplayParameters.model_validate(raw)


def test_protection_is_not_available_for_sma_or_implicit_asset_costs():
    with pytest.raises(ValidationError):
        ReplayParameters(broker_protection={"price_step": "1"})


def test_future_candles_cannot_change_frozen_levels_or_fills():
    original = candles()
    future = copy.deepcopy(original)
    future[5].update(open="500", high="510", low="490", close="500")
    left, right = replay(original), replay(future)
    assert left.fills[0] == right.fills[0]
    assert left.signals[0] == right.signals[0]


def test_timing_summary_uses_median_and_nearest_rank_p95():
    assert replay_engine.timing_summary([D(v) for v in (1, 4, 2, 3)]) == {
        "observations": 4,
        "median_seconds": "2.5",
        "p95_seconds": "4",
        "maximum_seconds": "4",
    }


def test_fx_bid_only_stop_and_missing_quotes_fail_closed_without_double_spread():
    bars = candles()
    bars[4]["low"] = "9"
    start = dt("2026-10-01T09:30")
    for i, bar in enumerate(bars):
        bar["open_time"] = (start + timedelta(minutes=i)).isoformat()
        bar["close_time"] = (start + timedelta(minutes=i + 1)).isoformat()
        bar["bid"] = {key: str(D(bar[key]) - D("0.2")) for key in ("open", "high", "low", "close")}
        bar["ask"] = {key: str(D(bar[key]) + D("0.2")) for key in ("open", "high", "low", "close")}
    saved = version()
    raw = parameters(saved, fee_bps="0", slippage_bps="5").model_dump(mode="json")
    raw.update(
        asset_costs={"asset_type": "fx", "spread_bps": "0", "financing_daily_bps": "0"},
        broker_protection={"price_step": "0.1", "entry_delay_seconds": 0},
    )
    params = ReplayParameters.model_validate(raw)
    context = default_context(spec(FX)).model_dump(mode="json")
    result = replay_engine.simulate(bars, params, saved, data_context=context)
    assert result.fills[0]["stop_price"] == "8.8"
    assert D(bars[4]["low"]) > D(result.fills[0]["stop_price"])
    assert D(result.fills[1]["price"]) == D("8.7956")
    bars[4].pop("bid")
    with pytest.raises(ValueError, match="historical bid/ask"):
        replay_engine.simulate(bars, params, saved, data_context=context)


def stock_dataset(client):
    bars = candles()
    start = dt("2026-10-01T09:30")
    for i, bar in enumerate(bars):
        bar["open_time"] = (start + timedelta(minutes=i)).isoformat()
        bar["close_time"] = (start + timedelta(minutes=i + 1)).isoformat()
    bars[3].update(high="22", low="8")
    context = stocks_context([("2026-10-01T09:30", "2026-10-01T16:00")])
    response = client.post(
        "/api/v1/research/datasets/import",
        json={
            "request_id": str(uuid4()),
            "name": "Synthetic protected stock history",
            "instrument_id": STOCK,
            "interval": "1m",
            "source": "Offline protection fixture, not market evidence",
            "candles": bars,
            "data_context": context.model_dump(mode="json"),
            "reason": "Verify research execution independently of brokers",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def protected_request(client):
    saved = register(client)
    dataset = stock_dataset(client)
    return {
        "request_id": str(uuid4()),
        "dataset_id": dataset["id"],
        "name": "Protected replay fixture",
        "hypothesis": "Inspect ambiguous protection and frozen order prices",
        "reason": "Offline acceptance of research execution",
        "parameters": {
            "engine": "closed_bar_breakout_v1",
            "strategy_version_id": saved["id"],
            "fee_bps": "0",
            "slippage_bps": "0",
            "asset_costs": {"asset_type": "equity", "spread_bps": "0"},
            "broker_protection": {"price_step": "1", "entry_delay_seconds": 0},
        },
    }


def test_saved_protected_run_keeps_evidence_on_retry_reload_and_export(signed_in):
    request = protected_request(signed_in)
    response = signed_in.post("/api/v1/research/runs", json=request)
    assert response.status_code == 201, response.text
    run = response.json()
    evidence = run["result"]["execution_evidence"]
    assert run["engine_version"] == replay_engine.PROTECTED_ENGINE_VERSION
    assert len(evidence["ambiguous_exits"]) == 1
    assert evidence["broker_parity_verified"] is False
    assert signed_in.post("/api/v1/research/runs", json=request).json()["id"] == run["id"]
    altered = copy.deepcopy(request)
    altered["parameters"]["broker_protection"]["entry_delay_seconds"] = 1
    assert signed_in.post("/api/v1/research/runs", json=altered).status_code == 409
    reloaded = signed_in.get("/api/v1/research").json()["runs"][0]
    assert reloaded["result"]["execution_evidence"] == evidence
    exported = signed_in.get(f"/api/v1/resources/research_run/{run['id']}")
    assert exported.status_code == 200, exported.text
    assert exported.json()["data"]["payload"]["result"]["execution_evidence"] == evidence
