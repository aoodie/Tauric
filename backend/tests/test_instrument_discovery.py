"""Discovery boundary, persistent universes and provider revalidation; offline transports."""

import json
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_independent_scanners import standalone
from test_market_connections import change
from test_paper import setup as setup
from test_portfolio_chat import portfolios_ready as portfolios_ready

from tauric import instrument_discovery as discovery
from tauric.asset_capabilities import research_capabilities
from tauric.data_providers import AlpacaConnection, OandaConnection
from tauric.models import AnalysisJob, AuditEvent, TradingRecord
from tauric.portfolio_research_contracts import PortfolioResearchParameters

pytest_plugins = ["test_chat"]


def asset(symbol="NVDA", **changes):
    return {
        "symbol": symbol,
        "name": "NVIDIA Corporation" if symbol == "NVDA" else symbol + " listing",
        "exchange": "NASDAQ",
        "class": "us_equity",
        "status": "active",
        "tradable": True,
        **changes,
    }


def fx(symbol="EUR_GBP", **changes):
    return {"name": symbol, "displayName": symbol.replace("_", "/"), "type": "CURRENCY", **changes}


@pytest.fixture
def discovery_transport(monkeypatch, tmp_path):
    path = tmp_path / "market.json"
    path.write_text("{}")
    monkeypatch.setenv("TAURIC_MARKET_CONNECTIONS_FILE", str(path))
    from tauric.config import runtime_config

    runtime_config.cache_clear()
    discovery.CACHE.clear()
    calls = []
    rows = {
        "oanda": [fx(), fx("AUD_CAD"), fx("EUR_JPY"), fx("XAU_USD", type="METAL")],
        "alpaca": [
            asset(),
            asset("QQQ", exchange="ARCA"),
            asset("BLOCKED", tradable=False),
            asset("OTC", exchange="OTC"),
            asset("BRK.B"),
            asset("BTC/USD", **{"class": "crypto"}),
        ],
    }

    def read(url, headers, params):
        calls.append((url, params))
        if "oanda.com" in url:
            assert headers["Authorization"] == "Bearer fixture-oanda-token"
            values = rows["oanda"]
            if params.get("instruments"):
                values = [row for row in values if row["name"] == params["instruments"]]
            return {"instruments": values}
        assert headers["APCA-API-KEY-ID"] == "fixture-alpaca-key"
        assert headers["APCA-API-SECRET-KEY"] == "fixture-alpaca-secret"
        if url.endswith("/v2/assets"):
            return rows["alpaca"]
        return next((row for row in rows["alpaca"] if url.endswith("/" + row["symbol"])), {})

    monkeypatch.setattr(discovery, "request_json", read)
    yield rows, calls
    discovery.CACHE.clear()


def connect(client, provider):
    response = client.put(
        f"/api/v1/market-connections/{provider}",
        json=change(provider, **({"account_id": "101-001-123-001"} if provider == "oanda" else {})),
    )
    assert response.status_code == 200, response.text


def add(client, provider="alpaca", symbol="NVDA", asset_type="equity", revision=1):
    return client.post(
        "/api/v1/instruments",
        json={
            "provider": provider,
            "symbol": symbol,
            "asset_type": asset_type,
            "expected_connection_revision": revision,
        },
    )


def test_discovery_search_pagination_private_and_cached(signed_in, discovery_transport):
    connect(signed_in, "alpaca")
    first = signed_in.get("/api/v1/instrument-discovery/alpaca?limit=2").json()
    assert first["total"] == 5 and first["has_more"]
    second = signed_in.get("/api/v1/instrument-discovery/alpaca?limit=2&offset=2").json()
    assert not ({r["symbol"] for r in first["items"]} & {r["symbol"] for r in second["items"]})
    search = signed_in.get("/api/v1/instrument-discovery/alpaca?query=nvidia").json()
    assert [r["symbol"] for r in search["items"]] == ["NVDA"]
    assert len(discovery_transport[1]) == 1
    assert not any(
        secret in json.dumps(search) for secret in ("fixture-alpaca-key", "fixture-alpaca-secret")
    )
    assert signed_in.get("/api/v1/instrument-discovery/alpaca?offset=-1").status_code == 422
    assert signed_in.get("/api/v1/instrument-discovery/alpaca?limit=101").status_code == 422
    assert signed_in.get("/api/v1/instrument-discovery/binance").status_code == 422
    assert signed_in.get("/api/v1/instrument-discovery/alpaca?query=absent").json()["total"] == 0


def test_added_instruments_are_immutable_idempotent_and_share_scanner_universes(
    signed_in, database, discovery_transport
):
    connect(signed_in, "alpaca")
    result = add(signed_in)
    assert result.status_code == 200, result.text
    spec = result.json()
    assert spec["id"] == "alpaca:equity:NVDA" and spec["native_research_ready"]
    assert add(signed_in).json() == spec
    assert add(signed_in, asset_type="etf").status_code == 409
    assert (
        len(discovery_transport[1]) == 3
    )  # Every addition rechecks availability, including repeated requests.
    profile, _ = standalone(signed_in, universe=[spec["id"]], max_analyses_per_day=0)
    assert signed_in.get("/api/v1/scanners").json()["profiles"][0]["parameters"]["universe"] == [
        spec["id"]
    ]
    assert profile["strategy_version_id"] is None
    with Session(database[0]) as db:
        assert (
            spec["id"]
            in next(row for row in research_capabilities(db) if row["asset_class"] == "equity")[
                "instruments"
            ]
        )
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "instrument.registered")
            )
            == 1
        )
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0
        assert db.scalar(select(func.count()).select_from(TradingRecord)) == 0
        with pytest.raises(DBAPIError):
            db.execute(
                text("UPDATE instruments SET definition = '{}' WHERE id = :id"), {"id": spec["id"]}
            )
        db.rollback()
    observed = signed_in.get("/api/v1/instrument-discovery/alpaca?query=NVDA").json()["items"][0]
    assert observed["registered_id"] == spec["id"]


def test_fx_and_etf_classification_research_scope(signed_in, discovery_transport):
    connect(signed_in, "oanda")
    connect(signed_in, "alpaca")
    result = signed_in.get("/api/v1/instrument-discovery/oanda?query=EUR/GBP").json()
    assert result["items"][0]["symbol"] == "EUR_GBP"
    cross = add(signed_in, "oanda", "EUR_GBP", "fx").json()
    assert cross["native_research_ready"] and cross["quote_currency"] == "GBP"
    other = add(signed_in, "oanda", "AUD_CAD", "fx").json()
    assert not other["native_research_ready"] and "unavailable" in other["analysis_scope"]
    etf = add(signed_in, symbol="QQQ", asset_type="etf").json()
    assert etf["asset_type"] == "etf" and "fundamentals are omitted" in etf["analysis_scope"]
    assert add(signed_in, "oanda", "XAU_USD", "fx").status_code == 422
    assert add(signed_in, symbol="BLOCKED").status_code == 422
    assert add(signed_in, symbol="OTC").status_code == 422
    assert add(signed_in, "oanda", "EUR_GBP", "equity").status_code == 422
    assert add(signed_in, "alpaca", "NVDA", "fx").status_code == 422
    assert add(signed_in, symbol="BRK.B").json()["native_symbol"] == "BRK-B"


def test_connection_changes_and_unavailable_assets_do_not_register(signed_in, discovery_transport):
    assert signed_in.get("/api/v1/instrument-discovery/oanda").status_code == 503
    connect(signed_in, "alpaca")
    assert signed_in.get("/api/v1/instrument-discovery/alpaca?query=NVDA").status_code == 200
    discovery_transport[0]["alpaca"][0]["tradable"] = False
    assert add(signed_in).status_code == 422
    assert "alpaca:equity:NVDA" not in [
        r["id"] for r in signed_in.get("/api/v1/instruments").json()
    ]
    assert (
        signed_in.put(
            "/api/v1/market-connections/alpaca",
            json=change(
                "alpaca", expected_revision=1, credential_action="clear", key="", secret=""
            ),
        ).status_code
        == 200
    )
    assert add(signed_in).status_code == 409
    assert signed_in.get("/api/v1/instrument-discovery/alpaca").status_code == 503


def test_authentication_csrf_and_identifier_validation(client, signed_in, discovery_transport):
    client.headers.pop("X-CSRF-Token")
    assert add(client).status_code == 403
    client.cookies.clear()
    assert client.get("/api/v1/instrument-discovery/alpaca").status_code == 401
    assert add(client).status_code == 401


def test_portfolio_research_saves_dynamic_universe(
    signed_in, portfolios_ready, discovery_transport
):
    connect(signed_in, "alpaca")
    spec = add(signed_in).json()
    portfolio = portfolios_ready[0]
    response = signed_in.put(
        f"/api/v1/portfolios/{portfolio['id']}/research",
        json={
            "request_id": str(uuid4()),
            "expected_revision": 0,
            "parameters": PortfolioResearchParameters(
                asset_classes=["equity"], universe=[spec["id"]]
            ).model_dump(mode="json"),
            "reason": "Save research universe with newly discovered listing",
        },
    )
    assert response.status_code == 200, response.text
    saved = signed_in.get(f"/api/v1/portfolios/{portfolio['id']}/research").json()
    assert saved["parameters"]["universe"] == [spec["id"]]
    assert (
        spec["id"]
        in next(row for row in saved["asset_capabilities"] if row["asset_class"] == "equity")[
            "instruments"
        ]
    )


@pytest.mark.parametrize(
    "payload", [{}, "bad", [None], [asset(name=None)], [asset(), asset()], [asset(symbol="x" * 31)]]
)
def test_malformed_catalog_rejected(payload):
    with pytest.raises(HTTPException) as raised:
        discovery.normalize("alpaca", payload)
    assert raised.value.status_code == 502


@pytest.mark.parametrize("status", [301, 401, 403, 404, 429, 500])
def test_provider_failures_are_sanitized(monkeypatch, status):
    original_client = httpx.Client

    def respond(request):
        assert request.method == "GET"
        return httpx.Response(
            status,
            text="private provider body fixture-secret",
            headers={"Location": "https://attacker.invalid/"},
        )

    monkeypatch.setattr(
        discovery.httpx,
        "Client",
        lambda **options: original_client(**options, transport=httpx.MockTransport(respond)),
    )
    with pytest.raises(HTTPException) as raised:
        discovery.request_json("https://paper-api.alpaca.markets/v2/assets", {}, {})
    assert "fixture-secret" not in raised.value.detail
    assert raised.value.status_code == (
        503 if status in (401, 403) else 429 if status == 429 else 502
    )


def test_response_bound_and_catalog_credential_isolation(monkeypatch):
    original_client = httpx.Client
    monkeypatch.setattr(discovery, "MAX_BYTES", 100)
    monkeypatch.setattr(
        discovery.httpx,
        "Client",
        lambda **options: original_client(
            **options,
            transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 101)),
        ),
    )
    with pytest.raises(HTTPException):
        discovery.request_json("https://paper-api.alpaca.markets/v2/assets", {}, {})
    assert discovery.endpoint("alpaca", AlpacaConnection(key="k", secret="s", environment="live"))[
        0
    ].startswith("https://api.alpaca.markets/")
    assert discovery.endpoint(
        "oanda", OandaConnection(token="k", account_id="123", environment="practice")
    )[0].startswith("https://api-fxpractice.oanda.com/")
    calls = []
    monkeypatch.setattr(discovery, "fetch", lambda *args: calls.append(args) or [])
    discovery.CACHE.clear()
    first = AlpacaConnection(key="first", secret="s")
    discovery.catalog("alpaca", first, 0)
    discovery.catalog("alpaca", first, 0)
    discovery.catalog("alpaca", AlpacaConnection(key="changed", secret="s"), 0)
    assert len(calls) == 2
    assert "changed" not in repr(discovery.CACHE)
    discovery.CACHE.clear()


def test_short_provider_names_are_valid():
    from tauric.instrument_catalog import INSTRUMENTS
    from tauric.market_contracts import InstrumentView

    assert discovery.normalize("alpaca", [asset("GE", name="GE")])[0].name == "GE"
    assert InstrumentView.model_validate({**INSTRUMENTS[-1], "name": "GE"}).name == "GE"
