"""Stock calendar, volume and execution authority boundaries without model calls."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_market_adapters import bar, dt, spec, stocks_context

from tauric import stock_execution
from tauric.market_calendars import periods
from tauric.market_contracts import DataQuality, MarketView
from tauric.research_contracts import AssetCosts

STOCK = "alpaca:equity:AAPL"


def feed(interval="15m"):
    context = stocks_context(
        [
            ("2026-07-02T09:30", "2026-07-02T16:00"),
            ("2026-07-06T09:30", "2026-07-06T16:00"),
        ]
    )
    stamp = dt("2026-07-06T10:00") + timedelta(seconds=5)
    first = (
        dt("2026-07-02T15:15")
        if interval == "15m"
        else stamp.replace(second=0) - timedelta(minutes=3)
    )
    rows = [
        bar(op, close) for op, close in periods(context, interval, first, stamp) if close <= stamp
    ]
    for row in rows:
        row.update(high="11", low="9", close="10", volume="10")
    rows[-1].update(high="13", close="12", volume="50")
    return MarketView(
        instrument=spec(STOCK),
        interval=interval,
        snapshot_id="stock-fixture",
        candles=rows,
        state="fresh",
        received_at=stamp,
        provider_time=stamp,
        source="alpaca_stocks",
        checksum="explicit-fixture-checksum",
        data_context=context,
        quality=DataQuality(),
        session_state="open",
    ), stamp


def parameters():
    return SimpleNamespace(
        lookback=3, minimum_volume_ratio="2", detector_id="closed_bar_breakout_v1"
    )


def test_regular_session_breakout_spans_holiday_without_inventing_bars(monkeypatch):
    value, stamp = feed()
    monkeypatch.setattr(stock_execution, "now", lambda: stamp)
    found, error = stock_execution.detect(value, parameters())
    assert error is None and found["features"]["volume_ratio"] == "5.00000000"
    assert len(found["evidence"]["bars"]) == 4
    assert found["evidence"]["execution_authority"] is False
    assert found["evidence"]["data_context"]["feed"] == "iex"
    assert "not necessarily consolidated" in found["reasons"][1]


def test_forming_high_and_volume_cannot_make_stock_candidate(monkeypatch):
    value, stamp = feed()
    value.candles[-1].close = "10"
    forming = bar(dt("2026-07-06T10:00"), dt("2026-07-06T10:15"))
    forming.update(high="100", close="100", volume="999", closed=False)
    value.candles.append(type(value.candles[0]).model_validate(forming))
    monkeypatch.setattr(stock_execution, "now", lambda: stamp)
    found, reason = stock_execution.detect(value, parameters())
    assert found is None and "did not both pass" in reason


@pytest.mark.parametrize(
    "bad",
    [
        "gap",
        "missing_latest",
        "bad_ohlc",
        "stale_receipt",
        "future_receipt",
        "wrong_feed",
        "unknown_calendar",
        "wrong_volume",
    ],
)
def test_scanner_rejects_invalid_stock_evidence(monkeypatch, bad):
    value, stamp = feed()
    if bad == "gap":
        value.candles.pop(1)
    elif bad == "missing_latest":
        value.candles.pop()
    elif bad == "bad_ohlc":
        value.candles[-1].high = "9"
    elif bad == "stale_receipt":
        value.received_at -= timedelta(seconds=61)
    elif bad == "future_receipt":
        value.received_at += timedelta(seconds=1)
    elif bad == "wrong_feed":
        value.data_context.feed = "unverified"
    elif bad == "unknown_calendar":
        value.data_context.coverage_end = stamp - timedelta(seconds=1)
    else:
        value.data_context.volume_type = "provider_ticks"
    monkeypatch.setattr(stock_execution, "now", lambda: stamp)
    found, reason = stock_execution.detect(value, parameters())
    assert found is None and reason


def test_delayed_research_is_visible_and_cannot_authorize_a_paper_fill(monkeypatch):
    value, stamp = feed()
    observed = stamp + timedelta(minutes=15)
    value.received_at = observed
    monkeypatch.setattr(stock_execution, "now", lambda: observed)
    found, reason = stock_execution.detect(value, parameters())
    assert found is not None and reason is None
    assert found["provider_time"] != found["received_at"]
    with pytest.raises(HTTPException):
        stock_execution.quote(value, 60, stamp=observed)


def test_stock_quote_requires_open_session_and_actual_positive_volume_bar():
    value, stamp = feed("1m")
    assert stock_execution.quote(value, 60, stamp=stamp) == Decimal("12")
    value.candles[-1].volume = "0"
    with pytest.raises(HTTPException):
        stock_execution.quote(value, 60, stamp=stamp)


@pytest.mark.parametrize("when", ["2026-07-06T09:29", "2026-07-06T16:00", "2026-07-04T10:00"])
def test_stocks_cannot_fill_before_open_after_close_or_on_a_holiday(when):
    value, _ = feed("1m")
    stamp = dt(when)
    value.provider_time = value.received_at = stamp
    # Calendar/session authority is checked even when receipt timestamps are fresh.
    value.candles = [] if stamp < value.candles[0].open_time else value.candles
    with pytest.raises(HTTPException):
        stock_execution.quote(value, 60, stamp=stamp)


def test_early_close_is_exclusive_and_intraday_end_bar_is_clipped():
    context = stocks_context([("2025-11-28T09:30", "2025-11-28T13:00")])
    stamp = dt("2025-11-28T12:59") + timedelta(seconds=10)
    rows = [
        bar(op, close)
        for op, close in periods(
            context, "1m", stamp.replace(second=0) - timedelta(minutes=3), stamp
        )
    ]
    rows[-1]["closed"] = False
    value = MarketView(
        instrument=spec(STOCK),
        interval="1m",
        snapshot_id="early",
        candles=rows,
        state="fresh",
        received_at=stamp,
        provider_time=stamp,
        source="alpaca_stocks",
        checksum="fixture",
        data_context=context,
        quality=DataQuality(),
        session_state="open",
    )
    assert stock_execution.quote(value, 60, stamp=stamp) == Decimal("10")
    value.received_at = value.provider_time = dt("2025-11-28T13:00")
    value.candles[-1].closed = True
    with pytest.raises(HTTPException):
        stock_execution.quote(value, 60, stamp=value.provider_time)


@pytest.mark.parametrize("asset", ["equity", "etf"])
def test_stock_deployment_requires_explicit_matching_costs_and_whole_shares(asset):
    instrument = spec(STOCK if asset == "equity" else "alpaca:etf:SPY")
    costs = AssetCosts(
        asset_type=asset, spread_bps="2", commission_per_unit="0.01", minimum_commission="1"
    )
    stock_execution.deployment(instrument, "USD", "1", costs)
    for currency, step, assumptions in [
        ("USDT", "1", costs),
        ("USD", "0.1", costs),
        ("USD", "1.5", costs),
        ("USD", "1", None),
    ]:
        with pytest.raises(HTTPException):
            stock_execution.deployment(instrument, currency, step, assumptions)


def test_stock_costs_cannot_silently_use_fx_rules():
    costs = AssetCosts(asset_type="fx", spread_bps="2", financing_daily_bps="0")
    with pytest.raises(HTTPException):
        stock_execution.deployment(spec(STOCK), "USD", "1", costs)


def test_minimum_commissions_limit_cash_and_strategy_capital(monkeypatch):
    from tauric import capital, stock_paper
    from tauric.trading_contracts import PaperPolicy

    monkeypatch.setattr(capital, "assigned", lambda *args: ({}, []))
    dep = SimpleNamespace(
        id="fixture",
        payload={
            "stock_costs": AssetCosts(
                asset_type="equity", spread_bps="0", minimum_commission="10"
            ).model_dump(mode="json"),
            "capital_limit": "55",
            "risk_percent": "100",
            "quantity_step": "1",
        },
    )
    prop = SimpleNamespace(payload={"stop_price": "9"})
    limits = PaperPolicy(
        risk_percent="100",
        max_total_risk_percent="100",
        max_exposure_percent="100",
        max_positions=5,
        fee_bps="0",
        slippage_bps="0",
        quote_max_age_seconds=60,
        decision_ttl_seconds=300,
    )
    entry, stop, risk, q, _ = stock_paper.sizing(
        None, SimpleNamespace(), dep, prop, limits, Decimal("100"), {}, Decimal("10")
    )
    assert q == 4 and q * entry + stock_paper.fee(dep, q, entry, "buy", limits) == 50
    assert q * risk == 24
    assert (q + 1) * entry + stock_paper.fee(dep, q + 1, entry, "buy", limits) > 55
    dep.payload["capital_limit"] = "20"
    with pytest.raises(HTTPException, match="no tradable"):
        stock_paper.sizing(
            None, SimpleNamespace(), dep, prop, limits, Decimal("100"), {}, Decimal("10")
        )


def test_stock_exit_never_uses_a_bar_that_started_before_fill_and_stop_wins(monkeypatch):
    value, stamp = feed()
    monkeypatch.setattr(stock_execution, "now", lambda: stamp)
    rules = SimpleNamespace(entry_lookback=3, exit_lookback=2, take_profit_r="2")
    position = SimpleNamespace(
        id="stock-position",
        created_at=value.candles[-1].open_time + timedelta(seconds=1),
        payload={"price": "10", "stop_price": "9"},
    )
    held = stock_execution.evaluate(value, rules, position)
    assert held["action"] == "hold"
    position.created_at = value.candles[-1].open_time - timedelta(seconds=1)
    exited = stock_execution.evaluate(value, rules, position)
    assert exited["action"] == "exit" and exited["exit_trigger"] == "stop"
    assert exited["position_id"] == position.id
