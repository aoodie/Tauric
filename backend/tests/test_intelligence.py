"""Source lineage, close comparisons, ownership and no-model discovery."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import intelligence, intelligence_sources, market
from tauric.data_providers import AlpacaConnection, Connections
from tauric.intelligence_models import IntelligenceReceipt
from tauric.intelligence_sources import parse_news
from tauric.market_calendars import default_context, periods
from tauric.market_contracts import InstrumentView
from tauric.models import AnalysisJob, Instrument, MarketSnapshot, Owner, now

BTC = "binance:spot:BTCUSDT"
STOCK = "alpaca:equity:AAPL"
FX = "oanda:fx:EUR_USD"


def article(stamp=None, **changes):
    stamp = stamp or now() - timedelta(hours=1)
    return {
        "id": 42,
        "headline": "<b>Company reports results</b>",
        "summary": "Reported results; causal attribution unknown.",
        "url": "https://www.benzinga.com/news/results",
        "symbols": ["AAPL"],
        "created_at": stamp.isoformat(),
        "updated_at": stamp.isoformat(),
        "source": "Benzinga",
        **changes,
    }


@pytest.fixture
def feeds(monkeypatch):
    def collect(spec, interval, **kwargs):
        assert interval == "1d"
        stamp = now()
        context = None
        if spec.asset_type in {"equity", "etf"}:
            from tauric.market_calendars import NY
            from tauric.market_contracts import DataContext, MarketSession

            beginning = (
                (stamp - timedelta(days=45))
                .astimezone(NY)
                .replace(hour=0, minute=0, second=0, microsecond=0)
            )
            context = DataContext(
                calendar_id="us_equity",
                timezone="America/New_York",
                price_side="trades",
                volume_type="feed_shares",
                feed="iex",
                adjustment="split",
                coverage_start=beginning,
                coverage_end=stamp + timedelta(days=1),
            )
            context.sessions = [
                MarketSession(
                    open=(beginning + timedelta(days=i)).replace(hour=9, minute=30),
                    close=(beginning + timedelta(days=i)).replace(hour=16),
                )
                for i in range(47)
                if (beginning + timedelta(days=i)).weekday() < 5
                and (beginning + timedelta(days=i)).replace(hour=16) <= context.coverage_end
            ]
        context = context or default_context(spec)
        grid = list(periods(context, interval, stamp - timedelta(days=40), stamp))
        bars = []
        for n, (opening, closing) in enumerate(grid):
            value = str(Decimal(100 + n)) if closing <= stamp else "900000"
            bar = {
                "open_time": opening.isoformat(),
                "close_time": closing.isoformat(),
                "open": value,
                "high": value,
                "low": value,
                "close": value,
                "volume": "20" if n == len(grid) - 2 else "10",
                "closed": closing <= stamp,
            }
            if spec.asset_type == "fx":
                bar["bid"] = dict.fromkeys(
                    ("open", "high", "low", "close"), str(Decimal(value) - Decimal("0.01"))
                )
                bar["ask"] = dict.fromkeys(
                    ("open", "high", "low", "close"), str(Decimal(value) + Decimal("0.01"))
                )
            bars.append(bar)
        return bars, stamp, stamp, context.model_dump(mode="json")

    for adapter in market.PROVIDERS.values():
        monkeypatch.setattr(adapter, "collect", collect)
    original = httpx.Client
    state = {"calls": [], "status": 200, "data": {"news": [article()]}}

    def handle(request):
        assert request.url.host == "data.alpaca.markets"
        state["calls"].append(str(request.url))
        return httpx.Response(state["status"], json=state["data"])

    monkeypatch.setattr(
        intelligence_sources.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    monkeypatch.setattr(
        intelligence_sources,
        "connections",
        lambda db: Connections(alpaca=AlpacaConnection(key="fixture-key", secret="fixture-secret")),
    )

    def policy(currencies):
        stamp = now().isoformat()
        return {
            "data": {
                "sources": [
                    {
                        "currency": c,
                        "kind": k,
                        "label": "Fixture central bank",
                        "url": "https://www.federalreserve.gov/",
                        "checked_at": stamp,
                        "status": "available",
                        "warning": None,
                        "items": [
                            {
                                "title": "Policy update",
                                "url": "https://www.federalreserve.gov/" + c,
                                "published_at": stamp,
                            }
                        ]
                        if k == "news"
                        else [
                            {
                                "date": (now() + timedelta(days=7)).date().isoformat(),
                                "timezone": "America/New_York",
                            }
                        ],
                    }
                    for c in currencies
                    for k in ("news", "calendar")
                ]
            }
        }

    monkeypatch.setattr(intelligence.forex_macro, "collect_currencies", policy)
    return state


def refresh(client, ids, sources=True):
    response = client.post(
        "/api/v1/intelligence/refresh", json={"instrument_ids": ids, "sources": sources}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_read_is_offline_and_empty_means_unknown(signed_in, feeds):
    value = signed_in.get("/api/v1/intelligence").json()
    assert value["total"] == 9
    assert all(m["changes"]["1d"] is None for m in value["moves"])
    assert not value["items"] and not value["candidates"] and not feeds["calls"]
    assert signed_in.get("/api/v1/intelligence?market=commodities").json()["total"] == 0
    assert signed_in.get("/api/v1/intelligence?market=rates").json()["total"] == 0


def test_completed_daily_comparisons_and_forming_bar_exclusion(signed_in, feeds):
    refresh(signed_in, [BTC, STOCK, FX])
    value = signed_in.get("/api/v1/intelligence").json()
    for move in [m for m in value["moves"] if m["instrument"]["id"] in {BTC, STOCK, FX}]:
        assert Decimal(move["price"]) < 200
        assert all(Decimal(move["changes"][h]) > 0 for h in ("1d", "5d", "1m"))
        assert move["snapshot_id"] and move["observed_at"] and move["volume_type"]
    assert len(feeds["calls"]) == 1
    assert any(i["kind"] == "policy_meeting" and i["date_only"] for i in value["items"])
    assert all(i["receipt_id"] for i in value["items"])


def test_refresh_deduplicates_sources_and_makes_no_model_job(database, signed_in, feeds):
    refresh(signed_in, [STOCK, STOCK])
    refresh(signed_in, [STOCK])
    assert len(feeds["calls"]) == 1
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(IntelligenceReceipt)) == 1
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


def test_partial_provider_failure_retains_other_observations(signed_in, feeds, monkeypatch):
    from fastapi import HTTPException

    def fail(*args, **kwargs):
        raise HTTPException(502, "The Forex feed is unavailable.")

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", fail)
    result = refresh(signed_in, [FX, STOCK], sources=False)
    assert result["results"][0]["status"] == "unavailable"
    value = signed_in.get("/api/v1/intelligence?market=stocks").json()
    assert next(m for m in value["moves"] if m["instrument"]["id"] == STOCK)["price"]


def test_news_failure_is_redacted_and_not_no_events(signed_in, feeds):
    feeds["status"] = 403
    feeds["data"] = {"secret": "fixture-secret"}
    refresh(signed_in, [STOCK])
    response = signed_in.get("/api/v1/intelligence?market=stocks")
    assert "fixture-secret" not in response.text
    assert response.json()["sources"][0]["status"] == "unavailable"
    assert not response.json()["items"]


def test_watchlist_conflicts_persistence_filters_and_idempotence(database, signed_in):
    path = "/api/v1/intelligence/watchlist"
    first = {"expected_revision": 0, "instrument_ids": [STOCK]}
    assert signed_in.put(path, json=first).json()["revision"] == 1
    assert signed_in.put(path, json=first).json()["revision"] == 1
    assert (
        signed_in.put(path, json={"expected_revision": 0, "instrument_ids": [BTC]}).status_code
        == 409
    )
    assert (
        signed_in.put(
            path, json={"expected_revision": 1, "instrument_ids": ["missing"]}
        ).status_code
        == 422
    )
    value = signed_in.get("/api/v1/intelligence?watched=true").json()
    assert value["total"] == 1 and value["moves"][0]["instrument"]["id"] == STOCK
    with Session(database[0]) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(IntelligenceReceipt)
                .where(IntelligenceReceipt.source_key == "market_watchlist")
            )
            == 1
        )


def test_filter_pagination_and_bounds(signed_in):
    assert signed_in.get("/api/v1/intelligence?market=stocks&query=apple").json()["total"] == 1
    assert signed_in.get("/api/v1/intelligence?limit=2&offset=2").json()["offset"] == 2
    assert len(signed_in.get("/api/v1/intelligence?limit=2").json()["moves"]) == 2
    assert signed_in.get("/api/v1/intelligence?limit=1000").status_code == 422
    assert signed_in.get("/api/v1/intelligence?market=unknown").status_code == 422
    assert (
        signed_in.post(
            "/api/v1/intelligence/refresh", json={"instrument_ids": [BTC] * 25}
        ).status_code
        == 422
    )


def test_auth_and_csrf(client, signed_in):
    signed_in.headers.pop("X-CSRF-Token")
    assert (
        signed_in.put(
            "/api/v1/intelligence/watchlist", json={"expected_revision": 0, "instrument_ids": []}
        ).status_code
        == 403
    )
    signed_in.delete("/api/v1/session")  # Also requires CSRF; clear cookie explicitly.
    client.cookies.clear()
    assert client.get("/api/v1/intelligence").status_code == 401


def test_source_receipt_ownership_integrity_and_immutable_storage(database, signed_in, feeds):
    receipt_id = refresh(signed_in, [STOCK])["receipt_ids"][0]
    response = signed_in.get("/api/v1/intelligence/receipts/" + receipt_id)
    assert response.status_code == 200 and response.json()["checksum"]
    with Session(database[0]) as db:
        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE intelligence_receipts SET checksum='changed' WHERE id=:id"),
                {"id": receipt_id},
            )
            db.commit()
        db.rollback()
    from types import SimpleNamespace

    from tauric.app import app
    from tauric.auth import authenticate

    overrides = dict(app.dependency_overrides)
    try:
        app.dependency_overrides[authenticate] = lambda: SimpleNamespace(id="foreign-owner")
        assert signed_in.get("/api/v1/intelligence/receipts/" + receipt_id).status_code == 404
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)


def test_gaps_do_not_rank_incomplete_series(database, signed_in, feeds):
    refresh(signed_in, [BTC], sources=False)
    with Session(database[1]) as db:
        row = db.scalar(select(MarketSnapshot).where(MarketSnapshot.instrument_id == BTC))
        payload = {
            **row.data,
            "candles": row.data["candles"][:-5] + row.data["candles"][-4:],
            "quality": {"status": "gaps", "missing_bars": 1, "warnings": []},
        }
        db.add(
            MarketSnapshot(
                instrument_id=BTC,
                interval="1d",
                provider_time=row.provider_time,
                received_at=now(),
                data=payload,
                checksum=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            )
        )
        db.commit()
    value = signed_in.get("/api/v1/intelligence?market=crypto").json()
    move = next(m for m in value["moves"] if m["instrument"]["id"] == BTC)
    assert move["state"] == "unverified" and move["price"] and move["changes"]["1d"] is None


def test_old_gaps_do_not_hide_verified_recent_changes(database, signed_in, feeds):
    refresh(signed_in, [BTC], sources=False)
    with Session(database[1]) as db:
        row = db.scalar(select(MarketSnapshot).where(MarketSnapshot.instrument_id == BTC))
        payload = {
            **row.data,
            "candles": row.data["candles"][:2] + row.data["candles"][3:],
            "quality": {"status": "gaps", "missing_bars": 1, "warnings": []},
        }
        db.add(
            MarketSnapshot(
                instrument_id=BTC,
                interval="1d",
                provider_time=row.provider_time,
                received_at=now(),
                data=payload,
                checksum=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
            )
        )
        db.commit()
    result = signed_in.get("/api/v1/intelligence?market=crypto").json()
    move = next(m for m in result["moves"] if m["instrument"]["id"] == BTC)
    assert move["state"] == "saved" and move["changes"]["1m"] is not None
    assert "outside the verified comparison window" in move["warning"]


def test_metadata_validation_rejects_unsafe_or_future_sources(database):
    stamp = now()
    with Session(database[0]) as db:
        spec = InstrumentView.model_validate(db.get(Instrument, STOCK).definition)
    good = parse_news({"news": [article(stamp)]}, [spec], stamp)
    assert good[0]["title"] == "Company reports results"
    for change in (
        {"url": "javascript:alert(1)"},
        {"url": "https://127.0.0.1/private"},
        {"url": "https://x:y@example.com/"},
        {"created_at": (stamp + timedelta(days=1)).isoformat()},
        {"symbols": ["MSFT"]},
    ):
        with pytest.raises(ValueError):
            parse_news({"news": [article(stamp, **change)]}, [spec], stamp)


def test_future_receipts_do_not_display_or_suppress_a_refresh(database, signed_in, feeds):
    refresh(signed_in, [STOCK])
    with Session(database[1]) as db:
        row = db.scalar(select(IntelligenceReceipt))
        db.add(
            IntelligenceReceipt(
                owner_id=row.owner_id,
                source_key=row.source_key,
                payload=row.payload,
                checksum=row.checksum,
                created_at=now() + timedelta(days=1),
            )
        )
        price = db.scalar(select(MarketSnapshot).where(MarketSnapshot.instrument_id == STOCK))
        db.add(
            MarketSnapshot(
                instrument_id=STOCK,
                interval="1d",
                provider_time=now() + timedelta(days=1),
                received_at=now(),
                data=price.data,
                checksum=price.checksum,
            )
        )
        db.commit()
        owner = db.get(Owner, row.owner_id)
        assert intelligence.recent(db, owner, row.source_key) is None
    value = signed_in.get("/api/v1/intelligence?market=stocks").json()
    assert value["sources"][0]["status"] == "unavailable" and value["items"] == []
    result = next(m for m in value["moves"] if m["instrument"]["id"] == STOCK)
    assert result["state"] == "unverified" and result["price"] is None
