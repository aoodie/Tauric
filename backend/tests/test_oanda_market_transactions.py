"""Actual OANDA market orders settle through transactions, not Order resources."""

from copy import deepcopy

import httpx
import pytest
from test_broker_adapters import make_broker, route

from tauric.broker_adapters import BrokerError
from tauric.broker_contracts import BrokerOrderRequest


def records():
    create = dict(
        id="42",
        accountID="101-001-123-001",
        batchID="42",
        type="MARKET_ORDER",
        instrument="EUR_USD",
        units="2",
        clientExtensions={"id": "tauric_fixture"},
    )
    fill = dict(
        id="43",
        accountID="101-001-123-001",
        batchID="42",
        type="ORDER_FILL",
        orderID="42",
        instrument="EUR_USD",
        units="2",
        price="1.12610",
        tradeOpened={"tradeID": "43", "units": "2", "clientExtensions": {"id": "tauric_fixture"}},
    )
    return create, fill


def test_immediate_market_fill_uses_response_transactions_without_order_lookup():
    create, fill = records()
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(
                201, json={"orderCreateTransaction": create, "orderFillTransaction": fill}
            )
        return route("oanda", request)

    with make_broker("oanda", handler) as broker:
        result = broker.submit(
            BrokerOrderRequest(
                client_order_id="tauric_fixture",
                symbol="EUR_USD",
                side="buy",
                quantity="2",
                time_in_force="fok",
            )
        )
    assert (
        result.status == "filled"
        and result.filled_quantity == "2"
        and result.average_fill_price == "1.12610"
    )
    assert result.trade_ids == ["43"]
    assert not any("/orders/" in path for _, path in calls)
    assert sum(method == "POST" for method, _ in calls) == 1


def transaction_handler(calls, created, terminal, *, latest="43"):
    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/summary"):
            return httpx.Response(
                200, json={"account": {"id": "101-001-123-001", "lastTransactionID": latest}}
            )
        if "/orders/" in request.url.path:
            return httpx.Response(404, json={"errorCode": "NO_SUCH_ORDER"})
        if request.url.path.endswith("/transactions/42"):
            return httpx.Response(200, json={"transaction": created})
        if request.url.path.endswith("/transactions/idrange"):
            return httpx.Response(200, json={"transactions": [created, *terminal]})
        raise AssertionError((request.method, request.url.path))

    return handler


def test_lost_response_recovery_correlates_exact_client_creation_and_fill_without_post():
    create, fill = records()
    calls = []
    with make_broker("oanda", transaction_handler(calls, create, [fill])) as broker:
        result = broker.order("tauric_fixture")
    assert (
        result.status == "filled" and result.broker_order_id == "42" and result.trade_ids == ["43"]
    )
    assert all(method == "GET" for method, _, _ in calls)


def test_saved_creation_id_recovers_an_old_fill_outside_recent_window():
    create, fill = records()
    calls = []
    with make_broker("oanda", transaction_handler(calls, create, [fill], latest="2044")) as broker:
        broker.known_market_order_ids = {"tauric_fixture": "42"}
        assert broker.order("tauric_fixture").status == "filled"
    assert next(params for _, path, params in calls if path.endswith("/idrange")) == {
        "from": "42",
        "to": "1041",
    }
    assert not any("/orders/" in path for _, path, _ in calls)


@pytest.mark.parametrize(
    "failure",
    [
        "account",
        "batch",
        "quantity",
        "allocation",
        "duplicate",
        "client",
        "opened_client",
        "instrument",
    ],
)
def test_mismatched_transaction_evidence_cannot_claim_a_fill(failure):
    create, fill = records()
    rows = [fill]
    if failure == "account":
        fill["accountID"] = "another-account"
    elif failure == "batch":
        fill["batchID"] = "999"
    elif failure == "quantity":
        fill["units"] = "3"
    elif failure == "allocation":
        fill["tradeOpened"]["units"] = "1"
    elif failure == "duplicate":
        rows.append(deepcopy(fill))
    elif failure == "client":
        create["clientExtensions"]["id"] = "other-client"
    elif failure == "opened_client":
        fill["tradeOpened"]["clientExtensions"]["id"] = "other-client"
    else:
        fill["instrument"] = "GBP_USD"
    with make_broker(
        "oanda", lambda request: pytest.fail("Parser performed network I/O")
    ) as broker:
        with pytest.raises(BrokerError):
            broker.transaction_view(create, rows, "tauric_fixture")


def test_matched_market_cancellation_and_rejection_have_no_fill():
    create, _ = records()
    cancel = dict(
        id="43",
        accountID=create["accountID"],
        batchID="42",
        type="ORDER_CANCEL",
        orderID="42",
        reason="FOK_ORDER_IMMEDIATE_PARTIAL_FILL",
    )
    with make_broker("oanda", lambda request: pytest.fail("Unexpected network")) as broker:
        result = broker.transaction_view(create, [cancel], "tauric_fixture")
        assert result.status == "cancelled" and result.filled_quantity == "0"
        create["type"] = "MARKET_ORDER_REJECT"
        assert broker.transaction_view(create, [], "tauric_fixture").status == "rejected"
