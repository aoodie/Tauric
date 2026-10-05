"""Broker behavior fixtures: no real credentials, funds or provider requests."""

import json
from decimal import Decimal

import httpx
import pytest
from pydantic import ValidationError

from tauric.broker_adapters import AlpacaPaper, BrokerError, OandaPractice
from tauric.broker_contracts import BrokerOrderRequest
from tauric.data_providers import AlpacaConnection, OandaConnection


def oanda_account():
    return {
        "account": {
            "id": "101-001-123-001",
            "currency": "GBP",
            "balance": "1000.12345",
            "NAV": "1000.12345",
            "marginAvailable": "900",
            "openPositionCount": 0,
            "pendingOrderCount": 0,
        }
    }


def alpaca_account():
    return {
        "id": "fixture-account",
        "currency": "USD",
        "cash": "1000.12",
        "equity": "1100.34",
        "buying_power": "2000.56",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
    }


def alpaca_order(**extra):
    return {
        "id": "fixture-order",
        "client_order_id": "tauric_fixture",
        "symbol": "AAPL",
        "qty": "2",
        "side": "buy",
        "filled_qty": "0",
        "filled_avg_price": None,
        "status": "accepted",
        **extra,
    }


def oanda_order(**extra):
    return {
        "order": {
            "id": "42",
            "clientExtensions": {"id": "tauric_fixture"},
            "instrument": "EUR_USD",
            "units": "2",
            "state": "PENDING",
            **extra,
        }
    }


def make_broker(provider, handler):
    config = (
        OandaConnection(token="secret-token", account_id="101-001-123-001")
        if provider == "oanda"
        else AlpacaConnection(key="secret-key", secret="secret-secret")
    )
    return (OandaPractice if provider == "oanda" else AlpacaPaper)(
        config, transport=httpx.MockTransport(handler)
    )


def route(provider, request, *, post_response=None):
    assert request.url.host == (
        "api-fxpractice.oanda.com" if provider == "oanda" else "paper-api.alpaca.markets"
    )
    if request.url.path.endswith("/summary"):
        return httpx.Response(200, json=oanda_account())
    if request.url.path == "/v2/account":
        return httpx.Response(200, json=alpaca_account())
    if request.url.path.endswith("/instruments"):
        return httpx.Response(
            200,
            json={
                "instruments": [
                    {
                        "name": "EUR_USD",
                        "type": "CURRENCY",
                        "tradeUnitsPrecision": 0,
                        "displayPrecision": 5,
                        "minimumTradeSize": "1",
                        "maximumOrderUnits": "1000000",
                        "marginRate": "0.03",
                    }
                ]
            },
        )
    if request.url.path == "/v2/assets/AAPL":
        return httpx.Response(
            200,
            json={
                "symbol": "AAPL",
                "class": "us_equity",
                "status": "active",
                "tradable": True,
                "fractionable": True,
            },
        )
    if request.method == "POST":
        return post_response or httpx.Response(
            201,
            json=alpaca_order()
            if provider == "alpaca"
            else {"orderCreateTransaction": {"id": "42"}},
        )
    if "/orders/" in request.url.path or request.url.path == "/v2/orders:by_client_order_id":
        return httpx.Response(200, json=alpaca_order() if provider == "alpaca" else oanda_order())
    raise AssertionError(f"Unexpected fixture request: {request.method} {request.url.path}")


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
def test_live_environments_rejected_before_any_network(provider):
    transport = httpx.MockTransport(
        lambda request: pytest.fail("Real-money config reached transport")
    )
    with pytest.raises(BrokerError, match="environment"):
        if provider == "oanda":
            OandaPractice(
                OandaConnection(token="secret-token", account_id="1", environment="live"),
                transport=transport,
            )
        else:
            AlpacaPaper(
                AlpacaConnection(key="secret-key", secret="secret-secret", environment="live"),
                transport=transport,
            )


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
def test_account_redaction_exact_money_and_guard_after_config_change(provider):
    with make_broker(provider, lambda request: route(provider, request)) as broker:
        result = broker.account()
        assert result.trading_enabled and len(result.account_fingerprint) == 64
        assert result.currency == ("GBP" if provider == "oanda" else "USD")
        assert result.cash == ("1000.12345" if provider == "oanda" else "1000.12")
        assert all(
            secret not in result.model_dump_json()
            for secret in [
                "secret-token",
                "secret-key",
                "secret-secret",
                "fixture-account",
                "101-001-123-001",
            ]
        )
        broker.config.environment = "live"
        with pytest.raises(BrokerError, match="environment"):
            broker.account()


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
def test_submission_attaches_stable_client_id_and_protection(provider):
    sent = []

    def handler(request):
        if request.method == "POST":
            sent.append(json.loads(request.content))
        return route(provider, request)

    with make_broker(provider, handler) as broker:
        result = broker.submit(
            BrokerOrderRequest(
                client_order_id="tauric_fixture",
                symbol="EUR_USD" if provider == "oanda" else "AAPL",
                side="buy",
                quantity="2",
                stop_loss="1.05000" if provider == "oanda" else "150.00",
                take_profit="1.20000" if provider == "oanda" else "200.00",
            )
        )
    assert len(sent) == 1 and result.status == "pending"
    if provider == "oanda":
        assert sent[0]["order"]["positionFill"] == "OPEN_ONLY"
        assert sent[0]["order"]["timeInForce"] == "FOK"
        assert sent[0]["order"]["clientExtensions"]["id"] == "tauric_fixture"
        assert sent[0]["order"]["stopLossOnFill"]["price"] == "1.05000"
    else:
        assert sent[0]["order_class"] == "bracket"
        assert sent[0]["client_order_id"] == "tauric_fixture"
        assert not sent[0]["extended_hours"]


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
@pytest.mark.parametrize("failure", ["timeout", "500", "redirect", "malformed"])
def test_unknown_submission_never_retries_or_leaks_credentials(provider, failure):
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("secret-token secret-key secret-secret", request=request)
            if failure == "malformed":
                return httpx.Response(201, text="secret-token invalid JSON")
            return httpx.Response(
                500 if failure == "500" else 307,
                text="secret-token",
                headers={"location": "https://api.alpaca.markets/v2/orders"},
            )
        return route(provider, request)

    with make_broker(provider, handler) as broker:
        with pytest.raises(BrokerError) as caught:
            broker.submit(
                BrokerOrderRequest(
                    client_order_id="tauric_fixture",
                    symbol="EUR_USD" if provider == "oanda" else "AAPL",
                    side="buy",
                    quantity="2",
                )
            )
    assert caught.value.unknown and len(posts) == 1
    assert "secret-" not in str(caught.value)


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
def test_invalid_quantity_or_price_never_posts(provider):
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
        return route(provider, request)

    with make_broker(provider, handler) as broker:
        for quantity, price in [
            ("0.5", None),
            ("2", "1.000001" if provider == "oanda" else "150.001"),
        ]:
            with pytest.raises(BrokerError):
                broker.submit(
                    BrokerOrderRequest(
                        client_order_id="tauric_fixture",
                        symbol="EUR_USD" if provider == "oanda" else "AAPL",
                        side="buy",
                        quantity=quantity,
                        order_type="limit" if price else "market",
                        limit_price=price,
                    )
                )
    assert posts == []


def test_alpaca_partial_fill_and_protective_children_retained():
    with make_broker(
        "alpaca",
        lambda request: httpx.Response(
            200,
            json=alpaca_order(
                status="partially_filled",
                filled_qty="1",
                filled_avg_price="150.123456",
                legs=[{"id": "protective-stop"}],
            ),
        ),
    ) as broker:
        result = broker.order("tauric_fixture")
    assert result.status == "partially_filled" and result.filled_quantity == "1"
    assert result.average_fill_price == "150.123456" and result.child_order_ids == [
        "protective-stop"
    ]


def test_oanda_filled_order_uses_verified_actual_fill_transaction():
    def handler(request):
        if "/transactions/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "transaction": {
                        "type": "ORDER_FILL",
                        "orderID": "42",
                        "units": "2",
                        "price": "1.12345",
                        "tradeOpened": {"tradeID": "43"},
                    }
                },
            )
        return httpx.Response(200, json=oanda_order(state="FILLED", fillingTransactionID="43"))

    with make_broker("oanda", handler) as broker:
        result = broker.order("tauric_fixture")
    assert result.status == "filled" and result.trade_ids == ["43"]
    assert result.average_fill_price == "1.12345" and result.filled_quantity == "2"


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
def test_lookup_not_found_is_distinct_from_unavailable(provider):
    def missing(request):
        if provider == "oanda" and request.url.path.endswith("/summary"):
            return httpx.Response(
                200, json={"account": {"id": "101-001-123-001", "lastTransactionID": "42"}}
            )
        if provider == "oanda" and request.url.path.endswith("/transactions/idrange"):
            return httpx.Response(200, json={"transactions": []})
        return httpx.Response(404, text="secret-token")

    with make_broker(provider, missing) as broker:
        with pytest.raises(BrokerError) as caught:
            broker.order("tauric_fixture")
        assert caught.value.not_found and not caught.value.unknown


@pytest.mark.parametrize(
    "bad", ["https://evil.example", "../orders", "AAPL?x=secret", "EUR_USD/close"]
)
def test_path_injection_rejected_without_network(bad):
    with make_broker(
        "alpaca", lambda request: pytest.fail("Invalid symbol reached network")
    ) as broker:
        with pytest.raises(BrokerError):
            broker.instrument(bad)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "1e3"])
def test_non_plain_or_nonpositive_order_amount_rejected(value):
    with pytest.raises(ValidationError):
        BrokerOrderRequest(
            client_order_id="tauric_fixture", symbol="AAPL", side="buy", quantity=value
        )


def test_reducing_alpaca_order_cannot_exceed_existing_inventory():
    def handler(request):
        if request.url.path == "/v2/positions":
            return httpx.Response(
                200,
                json=[
                    {
                        "symbol": "AAPL",
                        "side": "long",
                        "qty": "1",
                        "avg_entry_price": "100",
                        "unrealized_pl": "0",
                        "market_value": "100",
                    }
                ],
            )
        if request.method == "POST":
            pytest.fail("Oversized reducing order submitted")
        return route("alpaca", request)

    with make_broker("alpaca", handler) as broker:
        with pytest.raises(BrokerError, match="exceeds"):
            broker.submit(
                BrokerOrderRequest(
                    client_order_id="tauric_fixture",
                    symbol="AAPL",
                    side="sell",
                    quantity="2",
                    position_effect="reduce",
                )
            )


def test_oanda_home_currency_conversions_and_fresh_bid_ask():
    with make_broker(
        "oanda",
        lambda request: httpx.Response(
            200,
            json={
                "prices": [
                    {
                        "instrument": "EUR_USD",
                        "time": "2026-10-04T12:00:00Z",
                        "status": "tradeable",
                        "bids": [{"price": "1.10"}],
                        "asks": [{"price": "1.11"}],
                    }
                ],
                "homeConversions": [
                    {"currency": "USD", "accountGain": "0.75", "accountLoss": "0.76"}
                ],
            },
        ),
    ) as broker:
        result = broker.quote("EUR_USD")
    assert Decimal(result.quote_to_home_negative) == Decimal("0.76")
    assert result.bid == "1.10" and result.ask == "1.11"


@pytest.mark.parametrize("quantity,price", [("0", None), ("1", None), ("3", "150"), ("2", "NaN")])
def test_filled_alpaca_response_requires_complete_quantity_and_actual_price(quantity, price):
    with make_broker(
        "alpaca",
        lambda request: httpx.Response(
            200, json=alpaca_order(status="filled", filled_qty=quantity, filled_avg_price=price)
        ),
    ) as broker:
        with pytest.raises(BrokerError, match="verified"):
            broker.order("tauric_fixture")


@pytest.mark.parametrize("provider", ["oanda", "alpaca"])
def test_last_effect_guard_stops_order_before_http_mutation(provider):
    effects = []

    def handler(request):
        if request.method != "GET":
            effects.append(request)
        return route(provider, request)

    broker = make_broker(provider, handler)

    def revoked():
        raise RuntimeError("Exact entry permission expired during preparation")

    broker.before_mutation = revoked
    with broker, pytest.raises(BrokerError) as failure:
        broker.submit(
            BrokerOrderRequest(
                client_order_id="tauric_fixture",
                symbol="EUR_USD" if provider == "oanda" else "AAPL",
                side="buy",
                quantity="2",
            )
        )
    assert failure.value.not_submitted and not failure.value.unknown
    assert effects == []
