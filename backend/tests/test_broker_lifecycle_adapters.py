"""Exact broker trade lineage and execution activity fixtures; no real broker calls."""

from copy import deepcopy
from decimal import Decimal

import httpx
import pytest
from test_broker_adapters import alpaca_order, make_broker

from tauric.broker_adapters import BrokerError
from tauric.broker_lifecycle_adapters import (
    alpaca_children,
    alpaca_fills,
    oanda_close_trade,
    oanda_trade,
)


def trade(**extra):
    return {
        "id": "100",
        "instrument": "EUR_USD",
        "initialUnits": "10",
        "currentUnits": "6",
        "price": "1.12345",
        "state": "OPEN",
        "realizedPL": "1.23",
        "unrealizedPL": "2.34",
        "financing": "-0.02",
        "clientExtensions": {"id": "tauric_fixture"},
        "closingTransactionIDs": ["101"],
        "stopLossOrder": {"id": "90", "tradeID": "100", "state": "PENDING", "price": "1.05000"},
        **extra,
    }


def transaction(**extra):
    return {
        "id": "101",
        "type": "ORDER_FILL",
        "instrument": "EUR_USD",
        "orderID": "91",
        "price": "1.13000",
        "time": "2026-10-04T21:01:02.123456789Z",
        "tradeReduced": {
            "tradeID": "100",
            "units": "4",
            "realizedPL": "1.23",
            "financing": "-0.01",
        },
        **extra,
    }


def test_oanda_partial_exit_exact_home_pnl_and_targeted_close():
    current = trade()
    mutations = []

    def handler(request):
        if request.method == "PUT":
            assert request.url.path.endswith("/trades/100/close")
            assert request.content == b'{"units":"6"}'
            mutations.append(request.url.path)
            current.update(
                currentUnits="0",
                state="CLOSED",
                closingTransactionIDs=["101", "102"],
                realizedPL="2.50",
            )
            current.pop("unrealizedPL")
            return httpx.Response(200, json={"orderFillTransaction": {"id": "102"}})
        if "/trades/" in request.url.path:
            return httpx.Response(200, json={"trade": current})
        if request.url.path.endswith("/transactions/102"):
            return httpx.Response(
                200,
                json={
                    "transaction": transaction(
                        id="102",
                        orderID="92",
                        tradeReduced=None,
                        tradesClosed=[
                            {
                                "tradeID": "100",
                                "units": "6",
                                "realizedPL": "1.27",
                                "financing": "-0.01",
                            }
                        ],
                    )
                },
            )
        return httpx.Response(200, json={"transaction": transaction()})

    with make_broker("oanda", handler) as broker:
        partial = oanda_trade(broker, "100")
        assert partial.remaining_quantity == "6" and partial.exit_fills[0].quantity == "4"
        assert (
            partial.exit_fills[0].realized_pnl_home == "1.23" and partial.financing_home == "-0.02"
        )
        result = oanda_close_trade(broker, "100", "6")
        assert result.status == "CLOSED" and result.realized_pnl_home == "2.50"
        assert result.unrealized_pnl_home == "0"
        assert len(result.exit_fills) == 2 and len(mutations) == 1
        with pytest.raises(BrokerError, match="remaining"):
            oanda_close_trade(broker, "100", "1")
        assert len(mutations) == 1


@pytest.mark.parametrize(
    "change",
    [
        "identity",
        "allocation",
        "missing",
        "duplicate",
        "side",
        "protection",
        "closed_units",
        "open_pnl",
    ],
)
def test_oanda_invalid_trade_evidence_rejected(change):
    value, fill = trade(), transaction()
    if change == "identity":
        fill["instrument"] = "GBP_USD"
    elif change == "allocation":
        fill["tradeReduced"]["tradeID"] = "unrelated"
    elif change == "missing":
        value["currentUnits"] = "5"
    elif change == "duplicate":
        value["closingTransactionIDs"] = ["101", "101"]
    elif change == "side":
        value["currentUnits"] = "-6"
    elif change == "closed_units":
        value["state"] = "CLOSED"
    elif change == "open_pnl":
        value.pop("unrealizedPL")
    else:
        value["stopLossOrder"]["tradeID"] = "unrelated"
    with make_broker(
        "oanda",
        lambda request: httpx.Response(
            200, json={"trade": value} if "/trades/" in request.url.path else {"transaction": fill}
        ),
    ) as broker:
        with pytest.raises(BrokerError, match="verified"):
            oanda_trade(broker, "100")


def test_oanda_protection_fill_attributed_to_actual_protective_order():
    value = trade(currentUnits="0", state="CLOSED")
    value["stopLossOrder"]["state"] = "FILLED"
    fill = transaction(
        orderID="90", tradeReduced={"tradeID": "100", "units": "10", "realizedPL": "-0.5"}
    )
    with make_broker(
        "oanda",
        lambda request: httpx.Response(
            200, json={"trade": value} if "/trades/" in request.url.path else {"transaction": fill}
        ),
    ) as broker:
        result = oanda_trade(broker, "100")
        assert (
            result.protections[0].filled_quantity == "10"
            and result.protections[0].status == "filled"
        )


def activity(index, **extra):
    return {
        "id": f"activity-{index}",
        "activity_type": "FILL",
        "order_id": "fixture-order",
        "symbol": "AAPL",
        "side": "buy",
        "qty": "0.01",
        "price": "150.000001",
        "transaction_time": "2026-10-04T14:05:00Z",
        **extra,
    }


def test_alpaca_fill_pages_exact_quantities_and_price_not_order_average():
    calls = []

    def handler(request):
        assert request.url.params["order_id"] == "fixture-order"
        assert (
            request.url.params["activity_types"] == "FILL"
            and request.url.params["direction"] == "asc"
        )
        calls.append(dict(request.url.params))
        start, end = (100, 150) if request.url.params.get("page_token") else (0, 100)
        return httpx.Response(200, json=[activity(i) for i in range(start, end)])

    with make_broker("alpaca", handler) as broker:
        order = broker.view_order(
            alpaca_order(status="partially_filled", filled_qty="1.5", filled_avg_price="149")
        )
        fills = alpaca_fills(broker, order)
        assert len(fills) == 150 and calls[1]["page_token"] == "activity-99"
        assert sum(
            (Decimal(row.quantity) * Decimal(row.price) for row in fills), Decimal(0)
        ) == Decimal("225.00000150")


@pytest.mark.parametrize("change", ["order", "symbol", "side", "duplicate", "quantity"])
def test_alpaca_fill_identity_and_complete_coverage_required(change):
    rows = [activity(0, qty="2")]
    if change == "order":
        rows[0]["order_id"] = "unrelated"
    if change == "symbol":
        rows[0]["symbol"] = "MSFT"
    if change == "side":
        rows[0]["side"] = "sell"
    if change == "duplicate":
        rows = [activity(0, qty="1"), activity(0, qty="1")]
    if change == "quantity":
        rows[0]["qty"] = "1"
    with make_broker("alpaca", lambda request: httpx.Response(200, json=rows)) as broker:
        order = broker.view_order(
            alpaca_order(status="filled", filled_qty="2", filled_avg_price="150")
        )
        with pytest.raises(BrokerError):
            alpaca_fills(broker, order)


def test_alpaca_children_cannot_borrow_another_entry_or_duplicate_order():
    child = alpaca_order(
        id="protect", client_order_id="protect", side="sell", type="stop", stop_price="140"
    )
    raw = alpaca_order(status="filled", filled_qty="2", filled_avg_price="150", legs=[child])
    with make_broker("alpaca", lambda request: httpx.Response(200, json=raw)) as broker:
        parent = broker.view_order(raw)
        assert alpaca_children(broker, parent)[0][1].kind == "stop_loss"
        raw["legs"].append(deepcopy(child))
        with pytest.raises(BrokerError, match="duplicate"):
            alpaca_children(broker, parent)
        raw["legs"] = [child]
        raw["client_order_id"] = "different-parent"
        with pytest.raises(BrokerError, match="identity"):
            alpaca_children(broker, parent)
