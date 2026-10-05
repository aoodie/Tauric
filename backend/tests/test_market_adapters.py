import copy
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tauric import data_providers, market, replay_engine
from tauric.asset_costs import commission, execution_price, rollover_times
from tauric.config import runtime_config
from tauric.data_quality import freshness, verify
from tauric.instrument_catalog import INSTRUMENTS
from tauric.market_calendars import NY, default_context, periods
from tauric.market_contracts import DataContext, InstrumentView, MarketSession
from tauric.models import MarketSnapshot
from tauric.research_contracts import AssetCosts, ReplayParameters

FX = "oanda:fx:EUR_USD"
STOCK = "alpaca:equity:AAPL"


def spec(key):
    return InstrumentView.model_validate(next(s for s in INSTRUMENTS if s["id"] == key))


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=NY).astimezone(UTC)


def bar(opening, closing, price="10", *, sides=False):
    prices = dict.fromkeys(("open", "high", "low", "close"), price)
    result = {
        "open_time": opening.isoformat(),
        "close_time": closing.isoformat(),
        **prices,
        "volume": "10",
        "closed": True,
    }
    if sides:
        result.update(
            bid=dict.fromkeys(prices, str(Decimal(price) - Decimal("0.01"))),
            ask=dict.fromkeys(prices, str(Decimal(price) + Decimal("0.01"))),
        )
    return result


def stocks_context(sessions):
    return DataContext(
        calendar_id="us_equity",
        timezone="America/New_York",
        price_side="trades",
        volume_type="feed_shares",
        adjustment="split",
        feed="iex",
        calendar_source="synthetic test sessions",
        coverage_start=dt("2025-01-01T00:00"),
        coverage_end=dt("2027-01-01T00:00"),
        sessions=[MarketSession(open=dt(op), close=dt(close)) for op, close in sessions],
    )


def test_fx_weekend_and_dst_are_not_missing_bars():
    context = default_context(spec(FX))
    friday, sunday = dt("2026-03-06T16:45"), dt("2026-03-08T17:00")
    assert sunday - friday == timedelta(hours=47, minutes=15)
    candles = [bar(op, close, sides=True) for op, close in periods(context, "15m", friday, sunday)]
    assert len(candles) == 2
    assert verify(candles, "15m", context, sunday + timedelta(minutes=15)).missing_bars == 0
    assert freshness(candles[:1], "15m", context, dt("2026-03-07T12:00"))
    assert not freshness(candles[:1], "15m", context, dt("2026-03-09T12:00"))


def test_fx_maintenance_and_daily_alignment():
    context = default_context(spec(FX))
    expected = list(periods(context, "1m", dt("2026-10-01T16:58"), dt("2026-10-01T17:05")))
    assert [op.astimezone(NY).strftime("%H:%M") for op, _ in expected] == ["16:58", "17:05"]
    daily = list(periods(context, "1d", dt("2026-03-05T17:00"), dt("2026-03-08T17:00")))
    assert len(daily) == 2
    assert daily[0][0].hour == 22 and daily[1][0].hour == 21
    assert all(op.astimezone(NY).hour == 17 for op, _ in daily)
    h4 = list(periods(context, "4h", dt("2026-10-01T17:00"), dt("2026-10-01T21:00")))
    assert [op.astimezone(NY).hour for op, _ in h4] == [17, 21]


def test_stocks_holidays_early_close_and_missing_trading_day():
    context = stocks_context(
        [("2026-07-02T09:30", "2026-07-02T16:00"), ("2026-07-06T09:30", "2026-07-06T16:00")]
    )
    daily = [
        bar(op, close)
        for op, close in periods(context, "1d", dt("2026-07-02T09:30"), dt("2026-07-06T09:30"))
    ]
    assert len(daily) == 2
    assert verify(daily, "1d", context, dt("2026-07-07T00:00")).missing_bars == 0
    early = stocks_context([("2025-11-28T09:30", "2025-11-28T13:00")])
    grid = list(periods(early, "1h", dt("2025-11-28T09:30"), dt("2025-11-28T13:00")))
    assert len(grid) == 4 and grid[-1][1] - grid[-1][0] == timedelta(minutes=30)
    assert sum((close - op).total_seconds() for op, close in grid) == 12600
    three_days = stocks_context(
        [
            ("2026-07-06T09:30", "2026-07-06T16:00"),
            ("2026-07-07T09:30", "2026-07-07T16:00"),
            ("2026-07-08T09:30", "2026-07-08T16:00"),
        ]
    )
    missing = [bar(dt(day + "T09:30"), dt(day + "T16:00")) for day in ("2026-07-06", "2026-07-08")]
    assert verify(missing, "1d", three_days, dt("2026-07-09T00:00")).missing_bars == 1


@pytest.mark.parametrize(
    "bad",
    ["duplicate", "unordered", "future", "duration", "crossed", "no_ask", "nan", "old_partial"],
)
def test_bad_fx_evidence_is_rejected(bad):
    context = default_context(spec(FX))
    candles = [
        bar(op, close, sides=True)
        for op, close in periods(context, "15m", dt("2026-10-01T09:00"), dt("2026-10-01T09:30"))
    ]
    if bad == "duplicate":
        candles[1] = copy.deepcopy(candles[0])
    elif bad == "unordered":
        candles.reverse()
    elif bad == "future":
        candles[-1]["close_time"] = dt("2026-10-03T00:00").isoformat()
    elif bad == "duration":
        candles[0]["close_time"] = candles[0]["open_time"]
    elif bad == "crossed":
        candles[0]["bid"] = dict.fromkeys(("open", "high", "low", "close"), "11")
    elif bad == "no_ask":
        candles[0].pop("ask")
    elif bad == "nan":
        candles[0]["bid"]["open"] = "NaN"
    elif bad == "old_partial":
        candles[0]["closed"] = False
    with pytest.raises(ValueError):
        verify(candles, "15m", context, dt("2026-10-02T00:00"))


def test_stock_aggregation_preserves_regular_session_and_rejects_missing_constituents():
    context = stocks_context([("2026-10-01T09:30", "2026-10-01T16:00")])
    rows = [
        {
            "t": op.isoformat(),
            "o": Decimal("10.123456789"),
            "h": Decimal("11"),
            "l": Decimal("10"),
            "c": Decimal("10.5"),
            "v": 7,
        }
        for op, _ in periods(context, "15m", dt("2026-10-01T09:30"), dt("2026-10-01T16:00"))
    ]
    rows.append(
        {"t": dt("2026-10-01T09:00").isoformat(), "o": 999, "h": 999, "l": 999, "c": 999, "v": 999}
    )
    result = data_providers.aggregate_stocks(rows, "1d", "15m", context, dt("2026-10-02T00:00"))
    assert len(result) == 1 and result[0]["open"] == "10.123456789"
    assert result[0]["high"] == "11" and result[0]["volume"] == "182"
    assert result[0]["open_time"] == dt("2026-10-01T09:30").isoformat().replace("+00:00", "Z")
    with pytest.raises(ValueError, match="Missing constituent"):
        data_providers.aggregate_stocks(
            rows[:5] + rows[6:], "1d", "15m", context, dt("2026-10-02T00:00")
        )


@pytest.mark.parametrize("bad", ["misaligned", "bad_ohlc", "duplicate"])
def test_malformed_stock_constituents_cannot_be_hidden_in_aggregates(bad):
    context = stocks_context([("2026-10-01T09:30", "2026-10-01T16:00")])
    rows = [{"t": dt("2026-10-01T09:30").isoformat(), "o": 10, "h": 11, "l": 9, "c": 10, "v": 7}]
    if bad == "misaligned":
        rows[0]["t"] = dt("2026-10-01T09:31").isoformat()
    elif bad == "bad_ohlc":
        rows[0]["h"] = 8
    else:
        rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(ValueError):
        data_providers.aggregate_stocks(rows, "1h", "15m", context, dt("2026-10-02T00:00"))


@pytest.fixture
def private_connections(monkeypatch, tmp_path):
    path = tmp_path / "connections.json"
    path.write_text(
        json.dumps(
            {
                "oanda": {"token": "test-secret"},
                "alpaca": {"key": "test-key", "secret": "test-secret"},
            }
        )
    )
    monkeypatch.setenv("TAURIC_MARKET_CONNECTIONS_FILE", str(path))
    runtime_config.cache_clear()
    yield path
    runtime_config.cache_clear()


@pytest.mark.parametrize("available", [True, False])
def test_oanda_account_access_and_changed_settings_bypass_cache(
    signed_in, database, private_connections, monkeypatch, available
):
    paths = []
    account = "101-001-1234567-001"

    def handle(request):
        assert request.method == "GET"
        assert request.url.host == "api-fxpractice.oanda.com"
        assert request.headers["Authorization"] == "Bearer test-secret"
        paths.append(request.url.path)
        if request.url.path == f"/v3/accounts/{account}/instruments":
            assert request.url.params["instruments"] == "EUR_USD"
            return httpx.Response(
                200, json={"instruments": [{"name": "EUR_USD"}] if available else []}
            )
        assert request.url.path == "/v3/instruments/EUR_USD/candles"
        return httpx.Response(200, json=oanda_payload())

    transport(monkeypatch, handle)
    first = signed_in.post(f"/api/v1/market/{FX}/refresh", json={"interval": "15m"})
    assert first.status_code == 200
    saved = signed_in.put(
        "/api/v1/market-connections/oanda",
        json={
            "environment": "practice",
            "credential_action": "keep",
            "account_id": account,
            "expected_revision": 0,
        },
    )
    assert saved.status_code == 200
    refreshed = signed_in.post(f"/api/v1/market/{FX}/refresh", json={"interval": "15m"})
    assert refreshed.status_code == (200 if available else 503)
    assert paths[:2] == ["/v3/instruments/EUR_USD/candles", f"/v3/accounts/{account}/instruments"]
    assert len(paths) == (3 if available else 2)
    if available:
        assert refreshed.json()["snapshot_id"] != first.json()["snapshot_id"]
    else:
        assert "unavailable" in refreshed.text and account not in refreshed.text


def transport(monkeypatch, handle):
    original = httpx.Client
    monkeypatch.setattr(
        data_providers.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )


def oanda_payload():
    rows = []
    for op in ("2026-10-02T16:15", "2026-10-02T16:30", "2026-10-02T16:45"):
        prices = dict.fromkeys(("o", "h", "l", "c"), "1.123456789")
        rows.append(
            {
                "time": dt(op).isoformat(),
                "complete": True,
                "volume": 17,
                "mid": prices,
                "bid": dict.fromkeys(prices, "1.123356789"),
                "ask": dict.fromkeys(prices, "1.123556789"),
            }
        )
    return {"instrument": "EUR_USD", "granularity": "M15", "candles": rows}


def test_oanda_adapter_exact_read_only_request_and_snapshot(
    signed_in, database, private_connections, monkeypatch
):
    observed = dt("2026-10-03T12:00")
    monkeypatch.setattr(data_providers, "now", lambda: observed)
    monkeypatch.setattr(market, "now", lambda: observed)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET" and request.url.host == "api-fxpractice.oanda.com"
        assert request.url.path == "/v3/instruments/EUR_USD/candles"
        assert request.url.params["price"] == "MBA" and request.url.params["smooth"] == "false"
        assert request.url.params["alignmentTimezone"] == "America/New_York"
        return httpx.Response(200, json=oanda_payload())

    transport(monkeypatch, handle)
    response = signed_in.post(f"/api/v1/market/{FX}/refresh", json={"interval": "15m"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert (
        result["state"] == "fresh" and result["session_state"] == "closed" and result["gaps"] == 0
    )
    assert (
        result["candles"][0]["open"] == "1.123456789"
        and result["candles"][0]["bid"]["open"] == "1.123356789"
    )
    assert result["data_context"]["volume_type"] == "provider_ticks"
    assert (
        signed_in.post(f"/api/v1/market/{FX}/refresh", json={"interval": "15m"}).json()[
            "snapshot_id"
        ]
        == result["snapshot_id"]
    )
    assert len(requests) == 1
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 1
    public = signed_in.get("/api/v1/market-providers")
    assert "test-secret" not in public.text and public.json()[1]["configured"]
    assert not public.json()[1]["connected_verified"]
    native = signed_in.post(
        "/api/v1/analysis",
        json={"request_id": str(uuid4()), "instrument_id": FX, "profile": "local"},
    )
    assert native.status_code == 503  # A worker is required before owner-requested research.


def test_alpaca_read_only_calendar_pagination_split_feed_and_precision(
    private_connections, monkeypatch
):
    observed = dt("2026-10-03T12:00")
    monkeypatch.setattr(data_providers, "now", lambda: observed)
    requests = []

    def handle(request):
        requests.append(request)
        assert request.method == "GET"
        if request.url.path == "/v2/calendar":
            assert request.url.host == "paper-api.alpaca.markets"
            return httpx.Response(
                200, json=[{"date": "2026-10-02", "open": "09:30", "close": "16:00"}]
            )
        assert request.url.path == "/v2/stocks/bars"
        assert request.url.params["adjustment"] == "split" and request.url.params["feed"] == "iex"
        assert request.url.params["timeframe"] == "15Min"
        minute = "15:30" if "page_token" not in request.url.params else "15:15"
        # This literal JSON number would lose precision through ordinary float parsing.
        payload = (
            '{"bars":{"AAPL":[{"t":"'
            + dt("2026-10-02T" + minute).isoformat()
            + '","o":10.123456789123,"h":11,"l":10,"c":10.5,"v":10}]},"next_page_token":'
            + ('"page-two"' if minute == "15:30" else "null")
            + "}"
        )
        return httpx.Response(200, text=payload)

    transport(monkeypatch, handle)
    candles, as_of, received, context = data_providers.AlpacaStocks().collect(spec(STOCK), "15m")
    assert len(requests) == 3 and len(candles) == 2
    assert candles[0]["open"] == "10.123456789123"
    assert as_of == observed - timedelta(minutes=15) and received == observed
    assert context["adjustment"] == "split" and context["calendar_source"] == "Alpaca /v2/calendar"


@pytest.mark.parametrize("code,expected", [(401, 503), (403, 503), (429, 429), (500, 502)])
def test_provider_failures_redact_response(private_connections, monkeypatch, code, expected):
    transport(
        monkeypatch, lambda request: httpx.Response(code, text="test-secret private provider error")
    )
    with pytest.raises(HTTPException) as error:
        data_providers.OandaFX().collect(spec(FX), "15m")
    assert error.value.status_code == expected and "test-secret" not in error.value.detail


def test_unconfigured_and_malformed_connections_do_not_claim_ready(private_connections):
    private_connections.write_text('{"oanda":{"token":""},"alpaca":{}}')
    assert not data_providers.provider_status()[1].configured
    with pytest.raises(HTTPException) as error:
        data_providers.OandaFX().collect(spec(FX), "15m")
    assert error.value.status_code == 503
    private_connections.write_text(
        '{"oanda":{"token":"test-secret","url":"https://untrusted.invalid"}}'
    )
    with pytest.raises(HTTPException) as error:
        data_providers.provider_status()
    assert "test-secret" not in error.value.detail


def test_explicit_spread_minimum_commission_and_sell_tax():
    costs = AssetCosts(
        asset_type="equity",
        spread_bps="20",
        commission_per_unit="0.01",
        minimum_commission="1",
        sell_tax_bps="2",
    )
    assert execution_price(Decimal(100), "buy", None, "open", costs, Decimal("0.001")) == Decimal(
        "100.200100"
    )
    assert execution_price(Decimal(100), "sell", None, "open", costs, Decimal("0.001")) == Decimal(
        "99.800100"
    )
    assert commission(Decimal(10), Decimal(100), "buy", Decimal("0"), costs) == 1
    assert commission(Decimal(10), Decimal(100), "sell", Decimal("0"), costs) == Decimal("1.2")
    with pytest.raises(ValidationError):
        AssetCosts(asset_type="fx", spread_bps="0")


def test_ny_rollover_triple_day_and_credit_dst():
    costs = AssetCosts(asset_type="fx", spread_bps="0", financing_daily_bps="-1")
    events = list(rollover_times(dt("2026-03-04T16:00"), dt("2026-03-09T18:00"), costs))
    assert [days for _, days in events] == [3, 1, 1, 1]
    assert events[0][0].hour == 22 and events[-1][0].hour == 21


def test_fx_replay_measured_sides_financing_and_independent_reconciliation():
    candles = []
    context = default_context(spec(FX))
    for (op, close), price in zip(
        periods(context, "1d", dt("2026-09-28T17:00"), dt("2026-10-05T17:00")),
        ("10", "8", "6", "10", "12", "14"),
        strict=True,
    ):
        candles.append(bar(op, close, price, sides=True))
    parameters = ReplayParameters(
        fast_window=1,
        slow_window=2,
        starting_balance="1000",
        allocation_percent="90",
        risk_percent="100",
        stop_percent="50",
        quantity_step="1",
        fee_bps="0",
        slippage_bps="0",
        asset_costs=AssetCosts(asset_type="fx", spread_bps="0", financing_daily_bps="1"),
    )
    result = replay_engine.simulate(candles, parameters)
    assert result.fills[0]["price"] == candles[4]["ask"]["open"]
    assert result.fills[-1]["price"] == candles[-1]["bid"]["close"]
    assert result.financing_events and Decimal(result.financing_cost) > 0
    entries = sum(
        Decimal(f["quantity"]) * Decimal(f["price"]) + Decimal(f["fee"])
        for f in result.fills
        if f["side"] == "buy"
    )
    exits = sum(
        Decimal(f["quantity"]) * Decimal(f["price"]) - Decimal(f["fee"])
        for f in result.fills
        if f["side"] == "sell"
    )
    assert Decimal(result.final_equity) == Decimal(1000) - entries + exits - Decimal(
        result.financing_cost
    )
    assert Decimal(result.final_equity) == Decimal(1000) + Decimal(result.metrics.net_pnl)


def test_fx_import_requires_semantics_and_replay_requires_matching_costs(signed_in):
    context = default_context(spec(FX)).model_dump(mode="json")
    candles = [
        bar(op, close, price, sides=True)
        for (op, close), price in zip(
            periods(context, "15m", dt("2026-10-01T09:00"), dt("2026-10-01T10:30")),
            ("10", "8", "6", "10", "12", "4", "4"),
            strict=True,
        )
    ]
    body = {
        "request_id": str(uuid4()),
        "name": "FX test dataset",
        "instrument_id": FX,
        "interval": "15m",
        "source": "Synthetic contract test",
        "candles": candles,
        "reason": "Verify FX import semantics",
    }
    assert signed_in.post("/api/v1/research/datasets/import", json=body).status_code == 422
    body["data_context"] = context
    response = signed_in.post("/api/v1/research/datasets/import", json=body)
    assert response.status_code == 201, response.text
    dataset = response.json()
    assert dataset["asset_type"] == "fx" and dataset["quote_currency"] == "USD"
    run = {
        "request_id": str(uuid4()),
        "dataset_id": dataset["id"],
        "name": "Costed FX test",
        "hypothesis": "Test the explicit-cost FX pipeline",
        "reason": "Contract verification",
        "parameters": {"fast_window": 1, "slow_window": 2},
    }
    assert signed_in.post("/api/v1/research/runs", json=run).status_code == 422
    run["parameters"]["asset_costs"] = {
        "asset_type": "fx",
        "spread_bps": "1",
        "financing_daily_bps": "0",
    }
    assert signed_in.post("/api/v1/research/runs", json=run).status_code == 422
    run["parameters"]["asset_costs"]["spread_bps"] = "0"
    response = signed_in.post("/api/v1/research/runs", json=run)
    assert response.status_code == 201, response.text
    assert response.json()["engine_version"] == replay_engine.ASSET_ENGINE_VERSION
    assert any("USD" in assumption for assumption in response.json()["assumptions"])


def test_stock_daily_import_replay_uses_sessions_and_blocks_real_gaps(signed_in):
    dates = ("2026-07-02", "2026-07-06", "2026-07-07", "2026-07-08", "2026-07-09")
    context = stocks_context([(day + "T09:30", day + "T16:00") for day in dates])
    candles = [
        bar(dt(day + "T09:30"), dt(day + "T16:00"), price)
        for day, price in zip(dates, ("10", "8", "6", "10", "12"), strict=True)
    ]
    request = {
        "request_id": str(uuid4()),
        "name": "Stock holiday dataset",
        "instrument_id": STOCK,
        "interval": "1d",
        "source": "Synthetic stock test",
        "data_context": context.model_dump(mode="json"),
        "candles": candles,
        "reason": "Verify stock calendar research",
    }
    response = signed_in.post("/api/v1/research/datasets/import", json=request)
    assert response.status_code == 201, response.text
    dataset = response.json()
    run = {
        "request_id": str(uuid4()),
        "dataset_id": dataset["id"],
        "name": "Session-aware stock replay",
        "hypothesis": "Test regular-session evidence across an exchange holiday",
        "reason": "Contract verification",
        "parameters": {
            "fast_window": 1,
            "slow_window": 2,
            "asset_costs": {
                "asset_type": "equity",
                "spread_bps": "2",
                "commission_per_unit": "0.01",
                "minimum_commission": "1",
                "sell_tax_bps": "1",
            },
        },
    }
    response = signed_in.post("/api/v1/research/runs", json=run)
    assert response.status_code == 201, response.text
    assert response.json()["quote_currency"] == "USD"
    assert response.json()["result"]["metrics"]["total_fees"] != "0"
    request["request_id"] = str(uuid4())
    request["candles"] = candles[:2] + candles[3:]
    assert signed_in.post("/api/v1/research/datasets/import", json=request).status_code == 422


def test_bounded_stock_history_omits_only_oldest_truncated_prefix():
    context = stocks_context(
        [("2026-10-01T09:30", "2026-10-01T16:00"), ("2026-10-02T09:30", "2026-10-02T16:00")]
    )
    rows = [
        {"t": op.isoformat(), "o": "10", "h": "11", "l": "9", "c": "10", "v": "1"}
        for op, close in periods(context, "15m", dt("2026-10-01T12:00"), dt("2026-10-02T16:00"))
    ]
    result = data_providers.aggregate_stocks(
        rows, "1d", "15m", context, dt("2026-10-03T00:00"), truncated=True
    )
    assert len(result) == 1 and result[0]["volume"] == "26"
    with pytest.raises(ValueError, match="Missing constituent"):
        data_providers.aggregate_stocks(rows, "1d", "15m", context, dt("2026-10-03T00:00"))
    rows.pop(-3)
    with pytest.raises(ValueError, match="Missing constituent"):
        data_providers.aggregate_stocks(
            rows, "1d", "15m", context, dt("2026-10-03T00:00"), truncated=True
        )


def test_alpaca_accepts_more_than_four_small_pages(private_connections, monkeypatch):
    observed = dt("2026-10-03T12:00")
    monkeypatch.setattr(data_providers, "now", lambda: observed)
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/v2/calendar":
            return httpx.Response(
                200, json=[{"date": "2026-10-02", "open": "09:30", "close": "16:00"}]
            )
        page = int(request.url.params.get("page_token", "0"))
        op = dt("2026-10-02T15:45") - timedelta(minutes=15 * page)
        return httpx.Response(
            200,
            json={
                "bars": {
                    "AAPL": [{"t": op.isoformat(), "o": 10, "h": 11, "l": 9, "c": 10, "v": 10}]
                },
                "next_page_token": str(page + 1) if page < 5 else None,
            },
        )

    transport(monkeypatch, handle)
    candles, _, _, _ = data_providers.AlpacaStocks().collect(spec(STOCK), "15m")
    assert len(calls) == 7 and len(candles) == 6


def test_stock_requested_start_omits_only_known_out_of_range_prefix():
    context = stocks_context(
        [("2026-10-01T09:30", "2026-10-01T16:00"), ("2026-10-02T09:30", "2026-10-02T16:00")]
    )
    start = dt("2026-10-01T12:00")
    rows = [
        {"t": op.isoformat(), "o": "10", "h": "11", "l": "9", "c": "10", "v": "1"}
        for op, _ in periods(context, "15m", start, dt("2026-10-02T16:00"))
    ]
    result = data_providers.aggregate_stocks(
        rows, "1d", "15m", context, dt("2026-10-03T00:00"), bounded_start=start
    )
    assert len(result) == 1
    rows.pop(0)
    with pytest.raises(ValueError, match="Missing constituent"):
        data_providers.aggregate_stocks(
            rows, "1d", "15m", context, dt("2026-10-03T00:00"), bounded_start=start
        )
