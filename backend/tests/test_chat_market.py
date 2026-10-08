"""Chat reads verified observations without expanding research or trading authority."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from test_assistant_management import invocation as invocation
from test_chat import ready as ready

from tauric import chat_market
from tauric.models import now


def response(interval="15m"):
    duration = 900 if interval == "15m" else 3600
    opening = now().replace(second=0, microsecond=0) - timedelta(seconds=duration * 2)
    sides = {
        "mid": {"o": "2600", "h": "2604", "l": "2598", "c": "2602"},
        "bid": {"o": "2599", "h": "2603", "l": "2597", "c": "2601"},
        "ask": {"o": "2601", "h": "2605", "l": "2599", "c": "2603"},
    }
    return {
        "instrument": "XAU_USD",
        "granularity": "M15" if interval == "15m" else "H1",
        "candles": [{"time": opening.isoformat(), "complete": True, "volume": 20, **sides}],
    }


@pytest.fixture
def provider(monkeypatch):
    config = SimpleNamespace(environment="practice")
    monkeypatch.setattr(
        chat_market,
        "endpoint",
        lambda *args: ("https://api-fxpractice.oanda.com/v3/accounts/private/instruments", {}),
    )
    data = {
        "catalog": {"instruments": [{"name": "XAU_USD", "type": "METAL"}]},
        "candles": response(),
        "calls": [],
    }

    def request(url, headers, params):
        data["calls"].append((url, params))
        return data["catalog"] if url.endswith("/instruments") else data["candles"]

    monkeypatch.setattr(chat_market, "request_json", request)
    return config, data


def test_gold_observation_has_closed_bars_and_no_native_authority(provider):
    config, data = provider
    observed = chat_market.gold("15m", config)
    assert observed["symbol"] == "XAU/USD" and observed["asset_type"] == "metal"
    assert observed["candles"][0]["closed"] is True
    assert (
        observed["native_research_supported"] is False and observed["execution_eligible"] is False
    )
    assert observed["provider_time"] is None and observed["warnings"]
    assert data["calls"][1][0] == "https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/candles"
    assert data["calls"][1][1]["price"] == "MBA"
    assert data["calls"][1][1]["count"] == 61


@pytest.mark.parametrize(
    "catalog",
    [
        {"instruments": []},
        {"instruments": [{}]},
        {"instruments": ["malformed"]},
        {"instruments": [{"name": "XAU_USD", "type": "CURRENCY"}]},
    ],
)
def test_gold_requires_actual_metal_entitlement(provider, catalog):
    config, data = provider
    data["catalog"] = catalog
    with pytest.raises(HTTPException) as rejected:
        chat_market.gold("15m", config)
    assert rejected.value.status_code == 503 and len(data["calls"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"volume": -1},
        {"complete": "true"},
        {"time": (now() + timedelta(days=1)).isoformat()},
        {"mid": {"o": "2600", "h": "2500", "l": "2598", "c": "2602"}},
    ],
)
def test_gold_rejects_unverifiable_candles(provider, change):
    config, data = provider
    data["candles"]["candles"][0].update(change)
    with pytest.raises(HTTPException) as rejected:
        chat_market.gold("15m", config)
    assert rejected.value.status_code == 502


def test_no_daily_gold_substitution(provider):
    config, data = provider
    with pytest.raises(HTTPException):
        chat_market.gold("1d", config)
    assert data["calls"] == []


def test_market_tool_requires_owner_and_fresh_chat(ready, invocation, monkeypatch):
    body = {**invocation, "symbol": "XAU/USD", "intervals": ["15m", "1h"]}
    path = "/internal/openclaw/market-observations"
    headers = {"Authorization": "Bearer fixture-private-" + "x" * 50}
    assert ready.post(path, json=body).status_code == 401
    assert (
        ready.post(path, headers=headers, json={**body, "sender_is_owner": False}).status_code
        == 403
    )
    assert (
        ready.post(path, headers=headers, json={**body, "session_key": "unbound"}).status_code
        == 403
    )
    monkeypatch.setattr(chat_market, "stored_connection", lambda *args: (None, 1, "settings"))
    monkeypatch.setattr(
        chat_market,
        "gold",
        lambda interval, config: {
            "symbol": "XAU/USD",
            "interval": interval,
            "native_research_supported": False,
        },
    )
    result = ready.post(path, headers=headers, json=body)
    assert result.status_code == 200, result.text
    assert (
        result.json()["orders_submitted"] is False
        and result.json()["native_report_created"] is False
    )
    assert [v["interval"] for v in result.json()["observations"]] == ["15m", "1h"]
