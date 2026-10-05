"""Broker facts for protected positions and actual executions; no simulated prices."""

from decimal import Decimal

from .broker_adapters import BrokerError, amount, identifier, symbol, timestamp
from .broker_contracts import BrokerExecutionFill, BrokerProtectionView, BrokerTradeView
from .models import now


def oanda_trade(broker, trade_id):
    trade_id = identifier(trade_id)
    try:
        row = broker.request("GET", broker.path("/trades/" + trade_id), missing=True)["trade"]
        if row["id"] != trade_id:
            raise ValueError("Trade identity mismatch")
        initial, remaining = Decimal(row["initialUnits"]), Decimal(row["currentUnits"])
        if initial == 0 or (remaining and (initial > 0) != (remaining > 0)):
            raise ValueError("Trade side mismatch")
        if row["state"] == "CLOSED" and remaining != 0:
            raise ValueError("Closed trade has remaining units")
        protections = []
        for key, kind in [
            ("stopLossOrder", "stop_loss"),
            ("takeProfitOrder", "take_profit"),
            ("trailingStopLossOrder", "trailing_stop"),
        ]:
            if row.get(key):
                protection = row[key]
                if protection.get("tradeID") != trade_id:
                    raise ValueError("Protection trade mismatch")
                protections.append(
                    BrokerProtectionView(
                        broker_order_id=identifier(protection["id"]),
                        kind=kind,
                        status=protection["state"].lower(),
                        filled_quantity="0",
                        price=protection.get("price"),
                    )
                )
        exits = []
        transaction_ids = row.get("closingTransactionIDs", [])
        if (
            not isinstance(transaction_ids, list)
            or len(transaction_ids) > 1000
            or len(set(transaction_ids)) != len(transaction_ids)
        ):
            raise ValueError("Invalid closing transactions")
        for transaction_id in transaction_ids:
            transaction_id = identifier(transaction_id)
            transaction = broker.request("GET", broker.path("/transactions/" + transaction_id))[
                "transaction"
            ]
            if (
                transaction["id"] != transaction_id
                or transaction["type"] != "ORDER_FILL"
                or transaction["instrument"] != row["instrument"]
            ):
                raise ValueError("Closing transaction mismatch")
            closed = transaction.get("tradesClosed", []) + (
                [transaction["tradeReduced"]] if transaction.get("tradeReduced") else []
            )
            pieces = [piece for piece in closed if piece["tradeID"] == trade_id]
            if len(pieces) != 1:
                raise ValueError("Trade close allocation mismatch")
            piece = pieces[0]
            exits.append(
                BrokerExecutionFill(
                    fill_id=transaction_id + ":" + trade_id,
                    broker_order_id=identifier(transaction["orderID"]),
                    symbol=row["instrument"],
                    side="sell" if initial > 0 else "buy",
                    quantity=amount(abs(Decimal(piece["units"]))),
                    price=amount(transaction["price"]),
                    occurred_at=timestamp(transaction["time"]),
                    realized_pnl_home=amount(piece["realizedPL"]),
                    financing_home=amount(piece["financing"])
                    if piece.get("financing") is not None
                    else None,
                )
            )
        for protection in protections:
            protection.filled_quantity = amount(
                sum(
                    (
                        Decimal(fill.quantity)
                        for fill in exits
                        if fill.broker_order_id == protection.broker_order_id
                    ),
                    Decimal(0),
                )
            )
        return BrokerTradeView(
            trade_id=trade_id,
            client_trade_id=row.get("clientExtensions", {}).get("id"),
            symbol=row["instrument"],
            side="long" if initial > 0 else "short",
            initial_quantity=amount(abs(initial)),
            remaining_quantity=amount(abs(remaining)),
            entry_price=amount(row["price"]),
            status=row["state"],
            realized_pnl_home=amount(row["realizedPL"]),
            # OANDA omits unrealizedPL after a complete close. There is no open
            # portion then; active trades still require the broker's amount.
            unrealized_pnl_home=amount(
                row.get("unrealizedPL", "0") if row["state"] == "CLOSED" else row["unrealizedPL"]
            ),
            financing_home=amount(row["financing"]),
            protections=protections,
            exit_fills=exits,
            observed_at=now(),
        )
    except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
        raise BrokerError("The OANDA trade and exit evidence could not be verified.") from None


def oanda_close_trade(broker, trade_id, quantity):
    trade = oanda_trade(broker, trade_id)
    if Decimal(quantity) <= 0 or Decimal(quantity) > Decimal(trade.remaining_quantity):
        raise BrokerError("The requested close exceeds the remaining attributed trade.")
    broker.request(
        "PUT", broker.path("/trades/" + identifier(trade_id) + "/close"), body={"units": quantity}
    )
    return oanda_trade(broker, trade_id)


def alpaca_order_id(broker, order_id):
    order_id = identifier(order_id)
    row = broker.request("GET", "/v2/orders/" + order_id, params={"nested": "true"}, missing=True)
    if not isinstance(row, dict) or row.get("id") != order_id:
        raise BrokerError("The Alpaca order identity could not be verified.")
    return broker.view_order(row), row


def alpaca_children(broker, entry):
    parent, raw = alpaca_order_id(broker, entry.broker_order_id)
    if (
        parent.client_order_id != entry.client_order_id
        or parent.symbol != entry.symbol
        or parent.side != entry.side
    ):
        raise BrokerError("The broker parent order changed identity.")
    legs = raw.get("legs") or []
    if not isinstance(legs, list) or len(legs) > 4:
        raise BrokerError("The broker protective order structure is unsupported.")
    result = []
    for row in legs:
        order = broker.view_order(row)
        if order.symbol != parent.symbol or order.side == parent.side:
            raise BrokerError("The broker protective order does not match its parent.")
        kind = (
            "stop_loss"
            if row.get("type") in ("stop", "stop_limit")
            else "take_profit"
            if row.get("type") == "limit"
            else "other"
        )
        result.append(
            (
                order,
                BrokerProtectionView(
                    broker_order_id=order.broker_order_id,
                    kind=kind,
                    status=order.status,
                    filled_quantity=order.filled_quantity,
                    price=row.get("stop_price") or row.get("limit_price"),
                ),
            )
        )
    if len({order.broker_order_id for order, _ in result}) != len(result):
        raise BrokerError("The broker returned duplicate protective orders.")
    return result


def alpaca_fills(broker, order):
    expected_symbol = symbol(order.symbol, "alpaca")
    fills, seen, token = [], set(), None
    for _ in range(100):
        params = {
            "activity_types": "FILL",
            "order_id": identifier(order.broker_order_id),
            "page_size": 100,
            "direction": "asc",
        }
        if token:
            params["page_token"] = token
        rows = broker.request("GET", "/v2/account/activities", params=params)
        if not isinstance(rows, list) or len(rows) > 100:
            raise BrokerError("The Alpaca execution activity response is invalid.")
        try:
            for row in rows:
                if (
                    row["activity_type"] != "FILL"
                    or row["order_id"] != order.broker_order_id
                    or row["symbol"] != expected_symbol
                    or row["side"] != order.side
                    or row["id"] in seen
                ):
                    raise ValueError("Execution identity mismatch")
                fills.append(
                    BrokerExecutionFill(
                        fill_id=row["id"],
                        broker_order_id=row["order_id"],
                        symbol=row["symbol"],
                        side=row["side"],
                        quantity=amount(row["qty"]),
                        price=amount(row["price"]),
                        occurred_at=timestamp(row["transaction_time"]),
                    )
                )
                seen.add(row["id"])
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError(
                "The Alpaca execution activity evidence could not be verified."
            ) from None
        if len(rows) < 100:
            break
        token = rows[-1]["id"]
    else:
        raise BrokerError(
            "The Alpaca execution history exceeds this bounded collection; reconciliation remains incomplete."
        )
    total = sum((Decimal(fill.quantity) for fill in fills), Decimal(0))
    if total != Decimal(order.filled_quantity):
        raise BrokerError(
            "The Alpaca execution activities do not yet reconcile to the order fill quantity."
        )
    return fills
