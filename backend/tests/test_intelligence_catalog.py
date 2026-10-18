"""Provider-wide browsing stays independent of research and execution enrollment."""

from datetime import datetime, time, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_intelligence import feeds as feeds
from test_intelligence import refresh

from tauric import intelligence_catalog as catalogs
from tauric import intelligence_prices as prices
from tauric import market
from tauric.data_providers import AlpacaConnection, Connections, OandaFX
from tauric.intelligence_models import IntelligenceReceipt, IntelligenceWatchlist
from tauric.market_calendars import NY
from tauric.models import AnalysisJob, Instrument, MarketSnapshot, Owner, now


@pytest.fixture
def wide_markets(monkeypatch, feeds):
    rows = {
        "fx": [
            {"symbol": s, "name": s.replace("_", "/")}
            for s in ("EUR_USD", "GBP_USD", "USD_JPY", "AUD_CAD", "NZD_USD")
        ],
        "stocks": [
            {"symbol": s, "name": "Apple" if s == "AAPL" else s + " listing"}
            for s in ["AAPL", "MSFT", "SPY", "NVDA", "QQQ"] + [f"TEST{i:02}" for i in range(30)]
        ],
        "crypto": [
            {
                "symbol": s + "USDT",
                "name": s + " / USDT",
                "base_currency": s,
                "quote_currency": "USDT",
            }
            for s in ["BTC", "ETH", "SOL", "ADA", "XRP"]
        ],
    }
    monkeypatch.setattr(catalogs, "fetch", lambda family, db: rows[family])
    monkeypatch.setattr(prices, "connections", lambda db: Connections())
    monkeypatch.setattr(
        OandaFX,
        "collect",
        lambda self, spec, interval, **kw: market.PROVIDERS["oanda_v20"].collect(spec, interval),
    )
    monkeypatch.setattr(
        market.BinanceSpot,
        "collect",
        lambda self, spec, interval: market.PROVIDERS["binance_spot_public"].collect(
            spec, interval
        )[:3],
    )

    def batch(specs, config):
        result = {}
        for spec in specs:
            original = spec.model_copy(update={"asset_type": "equity"})
            bars, stamp, received, context = market.PROVIDERS["alpaca_stocks"].collect(
                original, "1d"
            )
            daily = []
            for bar in bars:
                day = datetime.fromisoformat(bar["open_time"]).astimezone(NY).date()
                closing = datetime.combine(day + timedelta(days=1), time.min, NY)
                if closing <= stamp:
                    daily.append(
                        {
                            **bar,
                            "open_time": datetime.combine(day, time.min, NY).isoformat(),
                            "close_time": closing.isoformat(),
                            "closed": True,
                        }
                    )
            result[spec.id] = (daily, stamp, received, context)
        return result

    monkeypatch.setattr(prices, "stock_batch", batch)
    return rows


def sync(client):
    response = client.post("/api/v1/intelligence/catalogs/refresh")
    assert response.status_code == 200, response.text
    return response.json()


def test_browse_beyond_starters_without_research_registration(database, signed_in, wide_markets):
    status = sync(signed_in)
    assert [s["total"] for s in status] == [5, 35, 5]
    first = signed_in.get("/api/v1/intelligence").json()
    assert first["total"] == 45 and len(first["moves"]) == 24
    second = signed_in.get("/api/v1/intelligence?offset=24").json()
    assert len(second["moves"]) == 21
    assert not (
        {m["instrument"]["id"] for m in first["moves"]}
        & {m["instrument"]["id"] for m in second["moves"]}
    )
    for family, symbol in [("stocks", "NVDA"), ("fx", "AUD/CAD"), ("crypto", "ADA/USDT")]:
        value = signed_in.get(
            "/api/v1/intelligence", params={"market": family, "query": symbol}
        ).json()
        spec = value["moves"][0]["instrument"]
        assert value["total"] == 1 and spec["symbol"] == symbol
        assert not spec["registered"] and not spec["native_research_ready"]
        assert signed_in.get("/api/v1/intelligence/reports/" + spec["id"]).json() == []
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(Instrument)) == 9
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


def test_catalog_watchlist_prices_cache_and_owner_evidence(database, signed_in, wide_markets):
    sync(signed_in)
    keys = ["catalog:alpaca:NVDA", "catalog:oanda:AUD_CAD", "catalog:binance:ADAUSDT"]
    saved = signed_in.put(
        "/api/v1/intelligence/watchlist", json={"expected_revision": 0, "instrument_ids": keys}
    )
    assert saved.status_code == 200
    result = refresh(signed_in, keys, sources=False)
    assert all(r["status"] == "available" for r in result["results"])
    assert refresh(signed_in, keys, sources=False)["receipt_ids"] == result["receipt_ids"]
    value = signed_in.get("/api/v1/intelligence?watched=true").json()
    assert value["total"] == 3
    for move in value["moves"]:
        assert move["price"] and move["changes"]["1m"] and move["daily_bars"]
        assert move["price_receipt_id"] and move["snapshot_id"] is None
        receipt = signed_in.get("/api/v1/intelligence/receipts/" + move["price_receipt_id"]).json()
        assert receipt["source"]["symbol"]["id"] == move["instrument"]["id"]
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(Instrument)) == 9
        assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 0
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0
        other = SimpleNamespace(id="other-catalog-owner")
        assert catalogs.universe(db, other)[1][0].status == "missing"
        assert catalogs.latest(db, other, "price:alpaca_stocks:NVDA") is None
        db.rollback()


def test_catalog_cache_partial_outage_retains_last_verified_market(
    signed_in, wide_markets, monkeypatch
):
    sync(signed_in)
    calls = []

    def fail(family, db):
        calls.append(family)
        raise HTTPException(503, "Provider is unavailable.")

    monkeypatch.setattr(catalogs, "fetch", fail)
    assert all(s["status"] == "available" for s in sync(signed_in))
    assert not calls
    checked = now() + timedelta(minutes=10)
    monkeypatch.setattr(catalogs, "now", lambda: checked)
    status = sync(signed_in)
    assert calls == ["fx", "stocks", "crypto"]
    assert all(s["status"] == "stale" and s["total"] > 0 for s in status)
    assert signed_in.get("/api/v1/intelligence?market=stocks&query=NVDA").json()["total"] == 1


def test_legacy_watchlist_retained_and_catalog_alias_follows_explicit_registration(
    database, signed_in, wide_markets
):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        for revision in range(1, 4):
            db.add(
                IntelligenceWatchlist(
                    owner_id=owner.id, revision=revision, instrument_ids=["alpaca:equity:AAPL"]
                )
            )
            db.flush()
        db.commit()
    sync(signed_in)
    key = "catalog:binance:ADAUSDT"
    assert signed_in.get("/api/v1/intelligence").json()["watchlist"]["revision"] == 3
    assert (
        signed_in.put(
            "/api/v1/intelligence/watchlist", json={"expected_revision": 3, "instrument_ids": [key]}
        ).status_code
        == 200
    )
    path = "/api/v1/intelligence/register"
    assert (
        signed_in.post(
            path, json={"instrument_id": "catalog:alpaca:QQQ", "asset_type": "fx"}
        ).status_code
        == 422
    )
    response = signed_in.post(path, json={"instrument_id": key, "asset_type": "crypto_spot"})
    assert response.status_code == 200, response.text
    assert response.json()["id"] == "binance:spot:ADAUSDT"
    assert signed_in.get("/api/v1/intelligence?watched=true").json()["watchlist"][
        "instrument_ids"
    ] == ["binance:spot:ADAUSDT"]
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(Instrument)) == 10
        assert db.scalar(select(func.count()).select_from(IntelligenceWatchlist)) == 3
        assert (
            db.scalar(
                select(func.count())
                .select_from(IntelligenceReceipt)
                .where(IntelligenceReceipt.source_key == "market_watchlist")
            )
            == 1
        )
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


def test_stock_batch_shared_requests_pagination_and_whole_day_boundaries(monkeypatch):
    stamp = datetime(2026, 10, 2, 18, tzinfo=NY)
    monkeypatch.setattr(prices, "now", lambda: stamp)
    calls = []

    def response(url, headers, params):
        calls.append((url, dict(params)))
        if url.endswith("calendar"):
            return [
                {"date": "2026-10-01", "open": "09:30", "close": "16:00"},
                {"date": "2026-10-02", "open": "09:30", "close": "16:00"},
            ]
        row = {"t": "2026-10-01T04:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 123}
        if "page_token" not in params:
            return {"bars": {"NVDA": [row]}, "next_page_token": "page-two"}
        return {
            "bars": {"QQQ": [row, {**row, "t": "2026-10-02T04:00:00Z"}]},
            "next_page_token": None,
        }

    monkeypatch.setattr(prices, "request_json", response)
    specs = [SimpleNamespace(id=s, provider_symbol=s) for s in ["NVDA", "QQQ"]]
    config = Connections(alpaca=AlpacaConnection(key="fake-key", secret="fake-secret"))
    value = prices.stock_batch(specs, config)
    assert len(calls) == 3 and calls[1][1]["symbols"] == "NVDA,QQQ"
    assert calls[2][1]["page_token"] == "page-two"
    for bars, asof, _received, context in value.values():
        assert len(bars) == 1  # Today's after-hours aggregate is still forming.
        assert datetime.fromisoformat(bars[0]["close_time"]).hour == 0
        assert prices.validate(bars, context, asof, True).status == "verified"
        with pytest.raises(ValueError):
            prices.validate([{**bars[0], "close_time": stamp.isoformat()}], context, asof, True)
    monkeypatch.setattr(
        prices,
        "request_json",
        lambda url, headers, params: (
            response(url, headers, params)
            if url.endswith("calendar")
            else {"bars": {"UNREQUESTED": []}}
        ),
    )
    with pytest.raises(ValueError, match="Wrong-symbol"):
        prices.stock_batch(specs, config)
