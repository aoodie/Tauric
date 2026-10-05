"""Fixed paper/market-data hosts and executable quote facts, offline transport."""

from datetime import timedelta

import httpx
import pytest
from test_broker_adapters import make_broker

from tauric.broker_adapters import BrokerError
from tauric.models import now


def handler(*, quote_changes=None, clock_changes=None, status=200, calls=None):
    stamp = now().isoformat()

    def respond(request):
        if calls is not None:
            calls.append(request)
        if request.url.host == "data.alpaca.markets":
            assert request.url.path == "/v2/stocks/AAPL/quotes/latest"
            assert request.url.params["feed"] == "iex"
            return httpx.Response(
                status,
                json={
                    "symbol": "AAPL",
                    "quote": {"bp": 150.01, "ap": 150.02, "bs": 10, "as": 20, "t": stamp},
                    **(quote_changes or {}),
                },
                headers={"Location": "https://api.alpaca.markets/v2/orders"},
            )
        assert request.url.host == "paper-api.alpaca.markets" and request.url.path == "/v2/clock"
        return httpx.Response(
            200, json={"timestamp": stamp, "is_open": True, **(clock_changes or {})}
        )

    return respond


def test_selected_feed_and_paper_clock_with_exact_prices():
    calls = []
    with make_broker("alpaca", handler(calls=calls)) as broker:
        result = broker.quote("AAPL")
    assert result.bid == "150.01" and result.ask == "150.02" and result.tradable
    assert result.feed == "iex" and len(calls) == 2
    assert all(request.method == "GET" for request in calls)


@pytest.mark.parametrize("status", [307, 401, 403, 429, 500])
def test_unavailable_feed_or_redirect_never_follows_or_mutates(status):
    calls = []
    with make_broker("alpaca", handler(status=status, calls=calls)) as broker:
        with pytest.raises(BrokerError, match="feed"):
            broker.quote("AAPL")
    assert len(calls) == 1


@pytest.mark.parametrize("clock_kind", ["invalid", "past", "future"])
def test_unverified_clock_is_rejected(clock_kind):
    # Construct relative times at execution, not collection; a full suite can
    # take longer than the clock freshness window.
    clock = (
        {"is_open": "true"}
        if clock_kind == "invalid"
        else {
            "timestamp": (
                now() + timedelta(seconds=-90 if clock_kind == "past" else 60)
            ).isoformat()
        }
    )
    with make_broker("alpaca", handler(clock_changes=clock)) as broker:
        with pytest.raises(BrokerError, match="verified"):
            broker.quote("AAPL")


@pytest.mark.parametrize(
    "changes",
    [
        {"symbol": "MSFT"},
        {"quote": {"bp": 2, "ap": 1, "bs": 1, "as": 1, "t": now().isoformat()}},
        {"quote": {"bp": 0, "ap": 1, "bs": 1, "as": 1, "t": now().isoformat()}},
        {"quote": {"bp": 1, "ap": 2, "bs": -1, "as": 1, "t": now().isoformat()}},
    ],
)
def test_invalid_quote_is_rejected(changes):
    with make_broker("alpaca", handler(quote_changes=changes)) as broker:
        with pytest.raises(BrokerError, match="verified"):
            broker.quote("AAPL")


@pytest.mark.parametrize("zero_size", [True, False])
def test_closed_market_or_zero_displayed_size_is_not_tradable(zero_size):
    quote = (
        {"quote": {"bp": 1, "ap": 2, "bs": 0, "as": 1, "t": now().isoformat()}} if zero_size else {}
    )
    with make_broker(
        "alpaca", handler(quote_changes=quote, clock_changes={"is_open": zero_size})
    ) as broker:
        assert not broker.quote("AAPL").tradable


def test_live_reconfiguration_stops_quote_before_network():
    with make_broker(
        "alpaca", lambda request: pytest.fail("Live config reached transport")
    ) as broker:
        broker.config.environment = "live"
        with pytest.raises(BrokerError, match="environment"):
            broker.quote("AAPL")
