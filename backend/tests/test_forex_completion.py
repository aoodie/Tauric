"""FX integration boundaries; synthetic providers and disposable storage only."""

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_market_adapters import FX, bar, spec
from test_paper import P, action, setup  # noqa: F401
from test_portfolio_chat import portfolios_ready as portfolios_ready
from test_qualification import prepare
from test_scanner import configured, scan
from test_strategy_replay import parameters, version

from tauric import (
    forex_connection,
    forex_macro,
    forex_paper,
    forex_sources,
    market,
    paper,
    replay_engine,
    scanner,
    strategy_rules,
)
from tauric.market_calendars import default_context, periods
from tauric.models import MarketConnectionVersion
from tauric.research_contracts import AssetCosts

pytest_plugins = ["test_chat"]
STAMP = datetime(2026, 9, 28, 14, 5, tzinfo=UTC)


def release(**changes):
    return {
        "Country": "United States",
        "CalendarId": "1",
        "Event": "CPI YoY",
        "Category": "Inflation Rate",
        "Date": "2026-09-28T12:30:00",
        "LastUpdate": "2026-09-28T12:31:00",
        "DateSpan": "0",
        "Actual": "3.2%",
        "Forecast": "3.0%",
        "TEForecast": "3.1%",
        "Previous": "3.3%",
        "Revised": "3.4%",
        "Unit": "%",
        "Reference": "Aug",
        "Source": "BLS",
        "SourceURL": "https://www.bls.gov/",
        **changes,
    }


def test_release_consensus_provider_forecast_and_surprise_remain_distinct():
    item = forex_sources.calendar([release()], "United States", STAMP)[0]
    assert item["forecast"] == "3.0%" and item["provider_forecast"] == "3.1%"
    assert item["surprise"]["value"] == "0.2" and item["surprise"]["unit"] == "percentage points"
    assert item["revised"] == "3.4%" and item["time"].endswith("+00:00")


@pytest.mark.parametrize(
    "changes",
    [{"Forecast": ""}, {"Forecast": "3K"}, {"Date": "2026-09-29T12:30:00"}, {"DateSpan": "1"}],
)
def test_missing_different_units_future_and_date_only_have_no_surprise(changes):
    item = forex_sources.calendar([release(**changes)], "United States", STAMP)[0]
    assert item["surprise"] is None
    if changes.get("DateSpan") == "1":
        assert item["time"] is None and item["actual"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"Country": "Japan"},
        {"Date": "2028-01-01T00:00:00"},
        {"LastUpdate": "2027-01-01T00:00:00"},
        {"CalendarId": None},
    ],
)
def test_release_country_window_update_and_identity_rejected(changes):
    with pytest.raises(ValueError):
        forex_sources.calendar([release(**changes)], "United States", STAMP)


def test_duplicate_release_rejected_and_unsafe_source_link_removed():
    with pytest.raises(ValueError):
        forex_sources.calendar([release(), release()], "United States", STAMP)
    assert (
        forex_sources.calendar([release(SourceURL="javascript:alert(1)")], "United States", STAMP)[
            0
        ]["url"]
        is None
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "1e13"])
def test_policy_benchmark_bad_numbers_rejected(value):
    row = {
        "Country": "United States",
        "Category": "Interest Rate",
        "Title": "Fed benchmark",
        "LatestValueDate": "2026-09-01T00:00:00",
        "LatestValue": value,
        "Unit": "%",
    }
    with pytest.raises(ValueError):
        forex_sources.rates([row], "United States", STAMP)


def test_financing_preserves_signed_decimal_rates_and_weekday_schedule():
    data = {
        "instruments": [
            {
                "name": "EUR_USD",
                "type": "CURRENCY",
                "financing": {
                    "longRate": "-0.04",
                    "shortRate": "0.012",
                    "financingDaysOfWeek": [{"dayOfWeek": "WEDNESDAY", "daysCharged": 3}],
                },
            }
        ]
    }
    result = forex_sources.financing(data, spec(FX))[0]
    assert result["long_rate_decimal"] == "-0.04" and result["schedule"][0]["days_charged"] == 3
    for mutate in ("name", "rate", "weekday"):
        bad = copy.deepcopy(data)
        if mutate == "name":
            bad["instruments"][0]["name"] = "GBP_USD"
        elif mutate == "rate":
            bad["instruments"][0]["financing"]["longRate"] = "NaN"
        else:
            bad["instruments"][0]["financing"]["financingDaysOfWeek"][0]["daysCharged"] = 99
        with pytest.raises(ValueError):
            forex_sources.financing(bad, spec(FX))


@pytest.mark.parametrize("mode", ["redirect", "oversized", "timeout"])
def test_collection_bounds_and_no_redirect_follow(mode):
    calls = []

    def handler(req):
        calls.append(req)
        if mode == "timeout":
            raise httpx.ReadTimeout("private-provider-body")
        return (
            httpx.Response(302, headers={"Location": "https://evil.example"})
            if mode == "redirect"
            else httpx.Response(200, content=b"x" * 1_000_001)
        )

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises((ValueError, httpx.HTTPError)):
            forex_sources.fetch(
                client, forex_sources.HOST + "/calendar", {"Authorization": "fixture-key"}
            )
    assert len(calls) == 1


def test_macro_connection_private_encrypted_revision_clear(signed_in, database):
    url = "/api/v1/forex-data/connection"
    body = {"expected_revision": 0, "key_action": "replace", "api_key": "fixture-macro-key"}
    result = signed_in.put(url, json=body)
    assert result.status_code == 200 and result.json()["configured"]
    assert body["api_key"] not in result.text
    with Session(database[0]) as db:
        row = db.scalar(
            select(MarketConnectionVersion).where(
                MarketConnectionVersion.provider == forex_connection.PROVIDER
            )
        )
        assert body["api_key"] not in row.encrypted_config
        assert forex_connection.saved(db) == ("fixture-macro-key", 1)
    assert signed_in.put(url, json=body).status_code == 409
    assert (
        signed_in.put(
            url, json={"expected_revision": 1, "key_action": "clear", "api_key": ""}
        ).json()["configured"]
        is False
    )
    assert body["api_key"] not in signed_in.get("/api/v1/audit").text


def test_macro_connection_owner_csrf_and_missing_credential(client):
    assert client.get("/api/v1/forex-data/connection").status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert (
        client.put(
            "/api/v1/forex-data/connection", json={"expected_revision": 0, "key_action": "clear"}
        ).status_code
        == 403
    )
    client.headers["X-CSRF-Token"] = client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    ).json()["csrf_token"]
    assert client.post("/api/v1/forex-data/test", json={"expected_revision": 0}).status_code == 422


@pytest.fixture
def fx_feed(monkeypatch):
    instrument = spec(FX)
    context = default_context(instrument)
    state = {"stamp": STAMP, "corrupt": False}

    def collect(instrument, interval, **kwargs):
        stamp = state["stamp"]
        grid = list(periods(context, interval, stamp - timedelta(days=1), stamp))[-20:]
        bars = [bar(op, close, "10", sides=True) for op, close in grid]
        for b in bars:
            b["closed"] = datetime.fromisoformat(b["close_time"]) <= stamp
        closed = [b for b in bars if b["closed"]]
        closed[-1].update(
            open="11",
            high="11",
            low="11",
            close="11",
            volume="50",
            bid=dict.fromkeys(["open", "high", "low", "close"], "10.99"),
            ask=dict.fromkeys(["open", "high", "low", "close"], "11.01"),
        )
        return bars, stamp, stamp, context.model_dump(mode="json")

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", collect)
    for module in (market, paper, scanner, strategy_rules):
        monkeypatch.setattr(module, "now", lambda: state["stamp"])
    return state


def test_fx_scanner_class_activity_and_no_orders(signed_in, fx_feed):
    profile, _ = configured(signed_in, universe=[FX])
    result, _ = scan(signed_in, profile)
    assert result["results"][0]["status"] == "matched", result
    candidate = signed_in.get("/api/v1/scanners").json()["candidates"][0]
    assert candidate["asset_class"] == "fx" and candidate["execution_authority"] is False
    assert any("tick activity" in reason for reason in candidate["reasons"])


@pytest.mark.parametrize("currency,instrument", [("USD", FX), ("JPY", "oanda:fx:USD_JPY")])
def test_fx_paper_quote_currency_bidask_and_reconciled_roundtrip(
    signed_in, database, fx_feed, currency, instrument
):
    # Patch the second registered pair with its own quote convention as required.
    acct = signed_in.post(
        "/api/v1/paper/accounts",
        json={
            **action(),
            "name": "FX disposable account",
            "currency": currency,
            "starting_balance": "10000",
            "policy": P,
        },
    ).json()
    request = {
        **action(),
        "account_id": acct["id"],
        "instrument_id": instrument,
        "portfolio": "FX portfolio",
        "capital_pot": "FX capital",
        "strategy": "FX strategy",
        "strategy_version": "v1",
        "quantity_step": "1",
        "capital_limit": "10000",
        "risk_percent": "1",
        "fx_financing_daily_bps": "0",
    }
    missing = {k: v for k, v in request.items() if k != "fx_financing_daily_bps"}
    assert signed_in.post("/api/v1/paper/deployments", json=missing).status_code == 422
    dep = signed_in.post("/api/v1/paper/deployments", json=request)
    assert dep.status_code == 201, dep.text
    assert signed_in.post(
        f"/api/v1/market/{instrument}/refresh", json={"interval": "1m"}
    ).status_code in (200, 404)
    # Refresh directly avoids encoding colons through a fixture's route selection.
    with Session(database[0]) as db:
        market.refresh(db, instrument, "1m")
    prop = signed_in.post(
        "/api/v1/paper/proposals",
        json={**action(), "deployment_id": dep.json()["id"], "stop_price": "9"},
    )
    assert prop.status_code == 201, prop.text
    assert prop.json()["payload"]["observed_price"] == "10.01"
    dec = signed_in.post(
        f"/api/v1/paper/proposals/{prop.json()['id']}/review", json={**action(), "outcome": "allow"}
    )
    plan = signed_in.post(f"/api/v1/paper/decisions/{dec.json()['id']}/plan", json=action())
    assert plan.status_code == 201, plan.text
    assert Decimal(plan.json()["payload"]["price"]) > Decimal("10.01")
    fill = signed_in.post(f"/api/v1/paper/plans/{plan.json()['id']}/execute", json=action())
    assert fill.status_code == 201, fill.text
    closed = signed_in.post(f"/api/v1/paper/positions/{fill.json()['id']}/close", json=action())
    assert closed.status_code == 201, closed.text
    assert closed.json()["payload"]["observed_price"] == "9.99"
    state = signed_in.get("/api/v1/paper").json()
    assert not state["positions"] and state["accounts"][0]["reconciled"]
    assert Decimal(state["accounts"][0]["cash"]) < Decimal("10000")


@pytest.mark.parametrize("rate", ["2", "-2"])
def test_financing_debit_credit_rollover_dst_and_tripling(rate):
    entry = SimpleNamespace(
        deployment_id="fixture",
        created_at=datetime(2026, 3, 6, 21, tzinfo=UTC),
        payload={"quantity": "100", "price": "10"},
    )
    dep = SimpleNamespace(payload={"fx_financing_daily_bps": rate, "fx_triple_rollover_weekday": 2})
    db = SimpleNamespace(get=lambda *_: dep)
    amount, events = forex_paper.closing_financing(db, entry, datetime(2026, 3, 12, 22, tzinfo=UTC))
    assert sum(e["days"] for e in events) == 7
    assert amount == Decimal(rate) * Decimal("0.7")
    assert any(e["at"].startswith("2026-03-11T21:00") and e["days"] == 3 for e in events)


def test_fx_breakout_weekend_replay_has_real_bidask_costs():
    context = default_context(spec(FX))
    grid = list(
        periods(context, "1d", datetime(2026, 3, 1, tzinfo=UTC), datetime(2026, 3, 20, tzinfo=UTC))
    )
    candles = [
        bar(op, close, "10" if i < 3 or i > 8 else "13", sides=True)
        for i, (op, close) in enumerate(grid)
    ]
    candles[3]["volume"] = "50"
    saved = version(
        rules={
            "engine": "closed_bar_breakout_v1",
            "interval": "1d",
            "entry_lookback": 2,
            "minimum_volume_ratio": "2",
            "exit_lookback": 2,
            "take_profit_r": "2",
            "signal_ttl_seconds": 300,
        }
    )
    p = parameters(
        saved, asset_costs=AssetCosts(asset_type="fx", spread_bps="0", financing_daily_bps="2")
    )
    result = replay_engine.simulate(candles, p, saved, data_context=context.model_dump(mode="json"))
    assert result.fills and result.financing_events
    buy = result.fills[0]
    assert Decimal(buy["price"]) > Decimal(buy["observed_price"])


def test_fx_qualification_frozen_context_explicit_costs_and_holdout(
    ready, portfolios_ready, database, monkeypatch
):
    body = prepare(ready, portfolios_ready[0])
    context = default_context(spec(FX))
    stamp = datetime(2026, 9, 1, tzinfo=UTC)
    grid = list(periods(context, "1m", stamp, stamp + timedelta(minutes=19)))[:14]
    candles = [
        bar(op, close, "10" if i % 7 < 2 else "13", sides=True)
        for i, (op, close) in enumerate(grid)
    ]
    for i in (2, 9):
        candles[i]["volume"] = "50"
    imported = ready.post(
        "/api/v1/research/datasets/import",
        json={
            **action(),
            "name": "FX qualification evidence",
            "instrument_id": FX,
            "interval": "1m",
            "source": "oanda_v20",
            "candles": candles,
            "data_context": context.model_dump(mode="json"),
        },
    )
    assert imported.status_code == 201, imported.text
    body["dataset_id"] = imported.json()["id"]
    body["parameters"]["asset_costs"] = {
        "asset_type": "fx",
        "spread_bps": "0",
        "financing_daily_bps": "2",
    }
    freeze = ready.post("/api/v1/qualification/protocols", json=body)
    assert freeze.status_code == 201, freeze.text
    evaluated = ready.post(
        f"/api/v1/qualification/protocols/{freeze.json()['id']}/evaluate", json=action()
    )
    assert evaluated.status_code == 201, evaluated.text
    assert evaluated.json()["frozen"]["dataset_context"]["quote_currency"] == "USD"
    assert evaluated.json()["frozen"]["execution_authority"] is False
    from tauric import analysis, native_fx
    from tauric.models import Owner

    collected = []

    def collect_before_admission(db, instrument):
        # An external data read cannot own the account admission/queue locks.
        with Session(database[0]) as other:
            assert other.scalar(select(Owner).with_for_update(nowait=True)) is not None
            from sqlalchemy import func

            assert other.scalar(select(func.pg_try_advisory_xact_lock(analysis.QUEUE_LOCK)))
        binding = {"fixture": "collected before admission", "instrument_id": instrument.id}
        collected.append(binding)
        return binding

    monkeypatch.setattr(native_fx, "prepare", collect_before_admission)
    monkeypatch.setattr(native_fx, "evidence", lambda *_: {})
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    url = f"/api/v1/qualification/protocols/{freeze.json()['id']}/assessment"
    request = {**action(), "profile": "local"}
    native = ready.post(url, json=request)
    assert native.status_code == 202, native.text
    assert native.json()["assessment_jobs"][0]["job"]["request"]["forex_context"] == collected[0]
    assert ready.post(url, json=request).status_code == 202 and len(collected) == 1


@pytest.mark.parametrize("charge", ["2", "-2"])
def test_settled_financing_reconciles_cash_and_realized_pnl(charge):
    from tauric.paper import checksum

    buy_payload = {"side": "buy", "quantity": "1", "price": "10", "fee": "0"}
    sell_payload = {
        "side": "sell",
        "quantity": "1",
        "price": "10",
        "fee": "0",
        "position_id": "entry",
        "financing": charge,
    }
    fills = [
        SimpleNamespace(
            id="entry", deployment_id="dep", payload=buy_payload, checksum=checksum(buy_payload)
        ),
        SimpleNamespace(
            id="exit", deployment_id="dep", payload=sell_payload, checksum=checksum(sell_payload)
        ),
    ]
    db = SimpleNamespace(scalars=lambda _: fills)
    cash, pnl, positions, _ = paper.ledger(
        db, SimpleNamespace(id="account", starting_balance="100")
    )
    assert cash == Decimal("100") - Decimal(charge) and pnl == -Decimal(charge) and not positions


def test_supplement_binding_tamper_wrong_currency_future_stale_and_wrong_host():
    from forex_supplement_fixture import fixture

    binding = fixture(spec(FX))
    assert forex_sources.validate(binding, spec(FX).model_dump())
    for change in ["checksum", "currency", "future", "stale", "host", "source_currency"]:
        broken = copy.deepcopy(binding)
        if change == "checksum":
            broken["checksum"] = "0" * 64
        elif change == "currency":
            broken["data"]["currencies"] = ["GBP", "USD"]
        elif change == "future":
            broken["data"]["fetched_at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
        elif change == "stale":
            broken["data"]["fetched_at"] = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
        elif change == "host":
            broken["data"]["sources"][0]["url"] = "https://evil.example/calendar"
        else:
            broken["data"]["sources"][0]["currency"] = "JPY"
        if change != "checksum":
            broken["checksum"] = forex_macro.digest(broken["data"])
        with pytest.raises(ValueError):
            forex_sources.validate(broken, spec(FX).model_dump())
