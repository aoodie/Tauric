"""Fixed-host demo broker adapters. No retries or real-money endpoint exists here.

The platform must durably commit the intent/client ID before calling submit.
Unknown submission/cancellation outcomes require reconciliation, never re-submit.
"""

import hashlib
import json
import re
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from .broker_contracts import (
    BrokerAccountView,
    BrokerInstrumentView,
    BrokerOrderView,
    BrokerPositionView,
    BrokerQuote,
)
from .models import now


class BrokerError(Exception):
    def __init__(self, message, *, unknown=False, not_found=False, not_submitted=False):
        super().__init__(message)
        self.unknown = unknown
        self.not_found = not_found
        self.not_submitted = not_submitted


def amount(value, *, nonnegative=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing broker amount")
    result = Decimal(str(value))
    if not result.is_finite() or (nonnegative and result < 0):
        raise ValueError("Invalid broker amount")
    return format(result, "f")


def timestamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Broker time must have timezone")
    return result.astimezone(UTC)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", value):
        raise BrokerError("The broker identifier is invalid.")
    return value


def symbol(value, provider):
    pattern = r"[A-Z]{3}_[A-Z]{3}" if provider == "oanda" else r"[A-Z][A-Z0-9.-]{0,31}"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise BrokerError("This broker instrument symbol is unsupported.")
    return value


class DemoBroker:
    provider = ""
    base_url = ""
    before_mutation = None

    def __init__(self, config, *, transport=None):
        self.config = config
        self.guard()
        self.client = httpx.Client(
            timeout=httpx.Timeout(15, connect=5),
            follow_redirects=False,
            transport=transport,
            headers=self.headers(),
        )

    def guard(self):
        raise NotImplementedError

    def headers(self):
        raise NotImplementedError

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def request(self, method, path, *, params=None, body=None, missing=False):
        self.guard()
        if not path.startswith("/") or "://" in path or ".." in path or "?" in path:
            raise BrokerError("The broker endpoint is invalid.")
        mutation = method != "GET"
        if mutation and self.before_mutation:
            try:
                self.before_mutation()
            except Exception:
                raise BrokerError(
                    "Fresh execution permission expired or changed before the broker request; nothing was submitted.",
                    not_submitted=True,
                ) from None
        try:
            response = self.client.request(method, self.base_url + path, params=params, json=body)
        except httpx.HTTPError:
            raise BrokerError(
                "Broker outcome is unknown; reconcile before another action."
                if mutation
                else "The demo broker is unavailable.",
                unknown=mutation,
            ) from None
        if response.status_code == 404 and missing:
            raise BrokerError("The broker order was not found.", not_found=True)
        if response.status_code in (401, 403):
            raise BrokerError("Check the demo broker credentials and account permissions.")
        if response.status_code == 429:
            raise BrokerError("The demo broker rate limit was reached.", unknown=mutation)
        if response.status_code >= 500 or 300 <= response.status_code < 400:
            raise BrokerError(
                "Broker outcome is unknown; reconcile before another action."
                if mutation
                else "The demo broker is unavailable.",
                unknown=mutation,
            )
        if response.status_code >= 400:
            # Duplicate client IDs are not proof that the previous order failed.
            raise BrokerError(
                "The demo broker rejected the action.",
                unknown=mutation and response.status_code == 409,
            )
        if response.status_code == 204:
            return {}
        try:
            parsed = json.loads(response.text, parse_float=Decimal)
            if not isinstance(parsed, (dict, list)):
                raise ValueError("Invalid broker response structure")
            return parsed
        except (ValueError, TypeError):
            raise BrokerError(
                "The broker response could not be verified.", unknown=mutation
            ) from None

    def validate(self, order):
        spec = self.instrument(order.symbol)
        quantity = Decimal(order.quantity)
        step = Decimal(spec.quantity_step)
        if not spec.tradable or quantity < Decimal(spec.minimum_quantity) or quantity % step:
            raise BrokerError("The broker instrument is unavailable or the quantity is invalid.")
        if spec.maximum_quantity and quantity > Decimal(spec.maximum_quantity):
            raise BrokerError("The quantity exceeds the broker instrument limit.")
        for value in (order.limit_price, order.stop_price, order.stop_loss, order.take_profit):
            if value and Decimal(value) % Decimal(spec.price_step):
                raise BrokerError("The order price does not match the broker price increment.")
        if not self.account().trading_enabled:
            raise BrokerError("The demo account is not enabled for trading.")
        return spec


class OandaPractice(DemoBroker):
    provider = "oanda"
    base_url = "https://api-fxpractice.oanda.com"

    def guard(self):
        if self.config.environment != "practice":
            raise BrokerError("OANDA execution requires the practice environment.")
        if not self.config.token.get_secret_value() or not self.config.account_id:
            raise BrokerError("Save an OANDA practice token and account ID first.")
        if not re.fullmatch(r"[0-9]+(?:-[0-9]+)*", self.config.account_id):
            raise BrokerError("The OANDA account identifier is invalid.")

    def headers(self):
        return {"Authorization": "Bearer " + self.config.token.get_secret_value()}

    def path(self, suffix):
        return "/v3/accounts/" + self.config.account_id + suffix

    def account(self):
        try:
            data = self.request("GET", self.path("/summary"))["account"]
            if data["id"] != self.config.account_id:
                raise ValueError("Account mismatch")
            # MT4-linked accounts cannot use the client correlation extension.
            return BrokerAccountView(
                provider=self.provider,
                environment="practice",
                account_fingerprint=hashlib.sha256(data["id"].encode()).hexdigest(),
                currency=data["currency"],
                cash=amount(data["balance"]),
                equity=amount(data["NAV"]),
                buying_power=amount(data["marginAvailable"]),
                trading_enabled=not bool(data.get("mt4AccountID")),
                open_positions=int(data["openPositionCount"]),
                pending_orders=int(data["pendingOrderCount"]),
                observed_at=now(),
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError("The OANDA account response could not be verified.") from None

    def instrument(self, name):
        name = symbol(name, self.provider)
        try:
            rows = self.request("GET", self.path("/instruments"), params={"instruments": name})[
                "instruments"
            ]
            row = next(r for r in rows if r["name"] == name)
            if row["type"] != "CURRENCY":
                raise ValueError("Only currency pairs are supported")
            return BrokerInstrumentView(
                symbol=name,
                tradable=True,
                quantity_step=format(Decimal(10) ** -int(row["tradeUnitsPrecision"]), "f"),
                minimum_quantity=amount(row["minimumTradeSize"], nonnegative=True),
                maximum_quantity=amount(row["maximumOrderUnits"], nonnegative=True),
                price_step=format(Decimal(10) ** -int(row["displayPrecision"]), "f"),
                margin_rate=amount(row["marginRate"], nonnegative=True),
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError, StopIteration):
            raise BrokerError("The OANDA instrument response could not be verified.") from None

    def quote(self, name):
        name = symbol(name, self.provider)
        try:
            data = self.request(
                "GET",
                self.path("/pricing"),
                params={"instruments": name, "includeHomeConversions": "true"},
            )
            row = next(r for r in data["prices"] if r["instrument"] == name)
            conversion = next(
                (r for r in data.get("homeConversions", []) if r["currency"] == name[-3:]), None
            )
            return BrokerQuote(
                symbol=name,
                bid=amount(max(Decimal(r["price"]) for r in row["bids"])),
                ask=amount(min(Decimal(r["price"]) for r in row["asks"])),
                observed_at=timestamp(row["time"]),
                tradable=row["status"] == "tradeable",
                quote_to_home_positive=amount(conversion["accountGain"]) if conversion else None,
                quote_to_home_negative=amount(conversion["accountLoss"]) if conversion else None,
                quote_to_home_position=amount(conversion["positionValue"])
                if conversion and "positionValue" in conversion
                else None,
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError, StopIteration):
            raise BrokerError("The OANDA quote response could not be verified.") from None

    def positions(self):
        try:
            data = self.request("GET", self.path("/openPositions"))["positions"]
            result = []
            for row in data:
                for side in ("long", "short"):
                    leg = row[side]
                    if Decimal(leg["units"]):
                        result.append(
                            BrokerPositionView(
                                symbol=row["instrument"],
                                side=side,
                                quantity=amount(abs(Decimal(leg["units"]))),
                                average_entry_price=amount(leg["averagePrice"]),
                                unrealized_pnl=amount(leg["unrealizedPL"]),
                            )
                        )
            return result
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError("The OANDA position response could not be verified.") from None

    def submit(self, order):
        self.validate(order)
        if order.time_in_force == "day":
            raise BrokerError("OANDA day orders are not supported by this route.")
        if order.order_type == "market" and order.time_in_force not in ("fok", "gtc"):
            raise BrokerError("OANDA market orders require fill-or-kill.")
        if order.order_type != "market" and order.time_in_force != "gtc":
            raise BrokerError("OANDA pending orders require good-till-cancelled.")
        data = {
            "instrument": order.symbol,
            "units": order.quantity if order.side == "buy" else "-" + order.quantity,
            "type": order.order_type.upper(),
            "timeInForce": "FOK" if order.order_type == "market" else "GTC",
            "positionFill": "OPEN_ONLY" if order.position_effect == "open" else "REDUCE_ONLY",
            "clientExtensions": {"id": order.client_order_id},
            "tradeClientExtensions": {"id": order.client_order_id},
        }
        if order.limit_price or order.stop_price:
            data["price"] = order.limit_price or order.stop_price
        if order.stop_loss:
            data["stopLossOnFill"] = {"price": order.stop_loss, "timeInForce": "GTC"}
        if order.take_profit:
            data["takeProfitOnFill"] = {"price": order.take_profit, "timeInForce": "GTC"}
        response = self.request("POST", self.path("/orders"), body={"order": data})
        created = response.get("orderCreateTransaction")
        if not created:
            raise BrokerError("OANDA submission outcome requires reconciliation.", unknown=True)
        if response.get("orderFillTransaction") or response.get("orderCancelTransaction"):
            return self.transaction_view(
                created,
                [
                    value
                    for key, value in response.items()
                    if key in {"orderFillTransaction", "orderCancelTransaction"}
                ],
                order.client_order_id,
            )
        # Retrieve the broker order plus its actual fill transaction; no invented fills.
        try:
            return self.order(order.client_order_id)
        except BrokerError:
            raise BrokerError(
                "OANDA submission outcome requires reconciliation.", unknown=True
            ) from None

    def order(self, client_id):
        client_id = identifier(client_id)
        known = getattr(self, "known_market_order_ids", {}).get(client_id)
        if known:
            return self.transaction_order(client_id, known)
        try:
            return self.order_resource(client_id)
        except BrokerError as error:
            if not error.not_found:
                raise
            return self.transaction_order(client_id)

    def transaction_order(self, client_id, known_id=None):
        """Filled market orders are transactions, not retrievable Order resources.

        A lost response is correlated by its exact unique client ID. Once known,
        the immutable creation ID bounds future reads even after more activity.
        Absence never grants authority to repeat the original submission.
        """
        try:
            account = self.request("GET", self.path("/summary"))["account"]
            if account["id"] != self.config.account_id:
                raise ValueError("Account mismatch")
            latest = int(account["lastTransactionID"])
            if known_id:
                created = self.request("GET", self.path("/transactions/" + identifier(known_id)))[
                    "transaction"
                ]
                start = int(created["id"])
                finish = min(latest, start + 999)
            else:
                finish = latest
                start = max(1, finish - 999)
            if not 1 <= start <= finish:
                raise ValueError("Invalid transaction window")
            rows = self.request(
                "GET",
                self.path("/transactions/idrange"),
                params={"from": str(start), "to": str(finish)},
            )["transactions"]
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError("Unbounded transaction response")
            if not known_id:
                matches = [
                    r
                    for r in rows
                    if r.get("type") in {"MARKET_ORDER", "MARKET_ORDER_REJECT"}
                    and r.get("clientExtensions", {}).get("id") == client_id
                ]
                if not matches:
                    raise BrokerError(
                        "The broker order was not found; no submission is repeated.", not_found=True
                    )
                if len(matches) != 1:
                    raise ValueError("Ambiguous client transaction")
                created = matches[0]
            return self.transaction_view(created, rows, client_id)
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError(
                "The OANDA market-order transaction evidence could not be verified."
            ) from None

    def transaction_view(self, created, rows, client_id):
        try:
            order_id = identifier(created["id"])
            units = Decimal(created["units"])
            if (
                created["type"] not in {"MARKET_ORDER", "MARKET_ORDER_REJECT"}
                or created["accountID"] != self.config.account_id
                or created.get("clientExtensions", {}).get("id") != client_id
                or not units.is_finite()
                or units == 0
            ):
                raise ValueError("Creation binding mismatch")
            name = symbol(created["instrument"], self.provider)
            fills = [
                r for r in rows if r.get("type") == "ORDER_FILL" and r.get("orderID") == order_id
            ]
            cancellations = [
                r for r in rows if r.get("type") == "ORDER_CANCEL" and r.get("orderID") == order_id
            ]
            if len(fills) > 1 or len(cancellations) > 1 or fills and cancellations:
                raise ValueError("Ambiguous market terminal state")
            if created["type"] == "MARKET_ORDER_REJECT" and (fills or cancellations):
                raise ValueError("Rejected creation has an inconsistent terminal event")
            fill = fills[0] if fills else None
            terminal = fill or (cancellations[0] if cancellations else None)
            if terminal and (
                terminal["accountID"] != self.config.account_id
                or terminal["batchID"] != created["batchID"]
            ):
                raise ValueError("Terminal transaction binding mismatch")
            pieces = []
            if fill:
                filled = Decimal(fill["units"])
                pieces = (
                    ([fill["tradeOpened"]] if fill.get("tradeOpened") else [])
                    + ([fill["tradeReduced"]] if fill.get("tradeReduced") else [])
                    + fill.get("tradesClosed", [])
                )
                if (
                    fill["instrument"] != name
                    or filled != units
                    or not pieces
                    or sum((abs(Decimal(p["units"])) for p in pieces), Decimal(0)) != abs(filled)
                    or len({p["tradeID"] for p in pieces}) != len(pieces)
                ):
                    raise ValueError("Actual FOK fill does not match creation")
                if any(
                    p.get("clientExtensions", {}).get("id", client_id) != client_id
                    for p in pieces
                    if p is fill.get("tradeOpened")
                ):
                    raise ValueError("Opened trade client mismatch")
            status = (
                "rejected"
                if created["type"] == "MARKET_ORDER_REJECT"
                else "filled"
                if fill
                else "cancelled"
                if cancellations
                else "unknown"
            )
            return BrokerOrderView(
                provider=self.provider,
                broker_order_id=order_id,
                client_order_id=client_id,
                symbol=name,
                side="buy" if units > 0 else "sell",
                requested_quantity=amount(abs(units)),
                filled_quantity=amount(abs(Decimal(fill["units"]))) if fill else "0",
                average_fill_price=amount(fill["price"]) if fill else None,
                status=status,
                broker_status=fill["type"]
                if fill
                else (terminal or created).get("reason", created["type"]),
                observed_at=now(),
                trade_ids=[identifier(p["tradeID"]) for p in pieces],
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError(
                "The OANDA market-order transaction evidence could not be verified."
            ) from None

    def order_resource(self, client_id):
        client_id = identifier(client_id)
        try:
            row = self.request("GET", self.path("/orders/@" + client_id), missing=True)["order"]
            fill = None
            if row["state"] == "FILLED":
                fill = self.request(
                    "GET", self.path("/transactions/" + identifier(row["fillingTransactionID"]))
                )["transaction"]
                if fill["type"] != "ORDER_FILL" or fill["orderID"] != row["id"]:
                    raise ValueError("Fill mismatch")
            units = Decimal(row.get("units", "0"))
            status = {"PENDING": "pending", "FILLED": "filled", "CANCELLED": "cancelled"}.get(
                row["state"], "unknown"
            )
            return BrokerOrderView(
                provider=self.provider,
                broker_order_id=identifier(row["id"]),
                client_order_id=row.get("clientExtensions", {}).get("id"),
                symbol=row.get("instrument"),
                side="buy" if units > 0 else "sell" if units < 0 else None,
                requested_quantity=amount(abs(units)) if units else None,
                filled_quantity=amount(abs(Decimal(fill["units"]))) if fill else "0",
                average_fill_price=amount(fill["price"]) if fill else None,
                status=status,
                broker_status=row["state"],
                observed_at=now(),
                trade_ids=(
                    [fill["tradeOpened"]["tradeID"]] if fill and fill.get("tradeOpened") else []
                )
                + ([fill["tradeReduced"]["tradeID"]] if fill and fill.get("tradeReduced") else [])
                + [r["tradeID"] for r in fill.get("tradesClosed", [])]
                if fill
                else [],
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError("The OANDA order response could not be verified.") from None

    def cancel(self, client_id):
        self.request("PUT", self.path("/orders/@" + identifier(client_id) + "/cancel"))
        return self.order(client_id)


class AlpacaPaper(DemoBroker):
    provider = "alpaca"
    base_url = "https://paper-api.alpaca.markets"

    def guard(self):
        if self.config.environment != "paper":
            raise BrokerError("Alpaca execution requires the paper environment.")
        if not self.config.key.get_secret_value() or not self.config.secret.get_secret_value():
            raise BrokerError("Save the Alpaca paper key and secret first.")

    def headers(self):
        return {
            "APCA-API-KEY-ID": self.config.key.get_secret_value(),
            "APCA-API-SECRET-KEY": self.config.secret.get_secret_value(),
        }

    def account(self):
        try:
            row = self.request("GET", "/v2/account")
            return BrokerAccountView(
                provider=self.provider,
                environment="paper",
                account_fingerprint=hashlib.sha256(row["id"].encode()).hexdigest(),
                currency=row["currency"],
                cash=amount(row["cash"]),
                equity=amount(row["equity"]),
                buying_power=amount(row["buying_power"]),
                observed_at=now(),
                trading_enabled=row["status"] == "ACTIVE"
                and not row["trading_blocked"]
                and not row["account_blocked"]
                and not row.get("trade_suspended_by_user", False),
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError("The Alpaca account response could not be verified.") from None

    def instrument(self, name):
        name = symbol(name, self.provider)
        try:
            row = self.request("GET", "/v2/assets/" + name)
            if row["symbol"] != name or row["class"] != "us_equity":
                raise ValueError("Asset mismatch")
            return BrokerInstrumentView(
                symbol=name,
                tradable=row["status"] == "active" and bool(row["tradable"]),
                quantity_step="1",
                minimum_quantity="1",
                price_step="0.01",
                fractional=bool(row.get("fractionable", False)),
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError("The Alpaca asset response could not be verified.") from None

    def positions(self):
        try:
            rows = self.request("GET", "/v2/positions")
            return [
                BrokerPositionView(
                    symbol=row["symbol"],
                    side=row["side"],
                    quantity=amount(abs(Decimal(row["qty"]))),
                    average_entry_price=amount(row["avg_entry_price"]),
                    unrealized_pnl=amount(row["unrealized_pl"]),
                    market_value=amount(row["market_value"]),
                )
                for row in rows
            ]
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError("The Alpaca position response could not be verified.") from None

    def quote(self, name):
        name = symbol(name, self.provider)
        self.guard()
        try:
            response = self.client.get(
                "https://data.alpaca.markets/v2/stocks/" + name + "/quotes/latest",
                params={"feed": self.config.feed},
            )
            if response.status_code != 200 or len(response.content) > 100_000:
                raise BrokerError(
                    "The selected Alpaca quote feed is unavailable; check its entitlement."
                )
            raw = json.loads(response.text, parse_float=Decimal)
            if raw["symbol"] != name:
                raise ValueError("Quote symbol mismatch")
            row = raw["quote"]
            sizes = [Decimal(str(row[key])) for key in ("bs", "as")]
            if any(not size.is_finite() or size < 0 for size in sizes):
                raise ValueError("Invalid quote sizes")
            clock = self.request("GET", "/v2/clock")
            clock_time = timestamp(clock["timestamp"])
            if (
                type(clock["is_open"]) is not bool
                or not 0 <= (now() - clock_time).total_seconds() <= 60
            ):
                raise ValueError("Invalid broker clock")
            return BrokerQuote(
                symbol=name,
                bid=amount(row["bp"]),
                ask=amount(row["ap"]),
                observed_at=timestamp(row["t"]),
                tradable=clock["is_open"] and all(size > 0 for size in sizes),
                feed=self.config.feed,
            )
        except httpx.HTTPError:
            raise BrokerError("The selected Alpaca quote feed is unavailable.") from None
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError(
                "The Alpaca quote and clock evidence could not be verified."
            ) from None

    def submit(self, order):
        self.validate(order)
        if order.time_in_force not in ("day", "gtc"):
            raise BrokerError("This Alpaca route supports day or good-till-cancelled orders.")
        if order.position_effect == "reduce":
            rows = [r for r in self.positions() if r.symbol == order.symbol]
            if (
                len(rows) != 1
                or (rows[0].side == "long") != (order.side == "sell")
                or Decimal(order.quantity) > Decimal(rows[0].quantity)
            ):
                raise BrokerError("The reducing order exceeds the current broker position.")
        data = {
            "client_order_id": order.client_order_id,
            "symbol": order.symbol,
            "qty": order.quantity,
            "side": order.side,
            "type": order.order_type,
            "time_in_force": order.time_in_force,
            "extended_hours": False,
        }
        if order.limit_price:
            data["limit_price"] = order.limit_price
        if order.stop_price:
            data["stop_price"] = order.stop_price
        if order.stop_loss or order.take_profit:
            data["order_class"] = "bracket" if order.stop_loss and order.take_profit else "oto"
            if order.stop_loss:
                data["stop_loss"] = {"stop_price": order.stop_loss}
            if order.take_profit:
                data["take_profit"] = {"limit_price": order.take_profit}
        row = self.request("POST", "/v2/orders", body=data)
        try:
            return self.view_order(row, expected_client_id=order.client_order_id)
        except BrokerError:
            raise BrokerError(
                "Alpaca submission outcome requires reconciliation.", unknown=True
            ) from None

    def view_order(self, row, *, expected_client_id=None):
        try:
            if expected_client_id and row["client_order_id"] != expected_client_id:
                raise ValueError("Client order mismatch")
            status = {
                "new": "pending",
                "accepted": "pending",
                "pending_new": "pending",
                "accepted_for_bidding": "pending",
                "partially_filled": "partially_filled",
                "filled": "filled",
                "canceled": "cancelled",
                "rejected": "rejected",
                "expired": "expired",
                "done_for_day": "pending",
                "pending_cancel": "pending",
                "pending_replace": "pending",
                "held": "pending",
            }.get(row["status"], "unknown")
            if status == "pending" and Decimal(row["filled_qty"]) > 0:
                status = "partially_filled"
            return BrokerOrderView(
                provider=self.provider,
                broker_order_id=identifier(row["id"]),
                client_order_id=row["client_order_id"],
                symbol=row["symbol"],
                side=row["side"],
                requested_quantity=amount(row["qty"], nonnegative=True),
                filled_quantity=amount(row["filled_qty"], nonnegative=True),
                average_fill_price=amount(row["filled_avg_price"])
                if row.get("filled_avg_price")
                else None,
                status=status,
                broker_status=row["status"],
                observed_at=now(),
                child_order_ids=[identifier(leg["id"]) for leg in row.get("legs") or []],
            )
        except (KeyError, ValueError, TypeError, AttributeError, ArithmeticError):
            raise BrokerError("The Alpaca order response could not be verified.") from None

    def order(self, client_id):
        row = self.request(
            "GET",
            "/v2/orders:by_client_order_id",
            params={"client_order_id": identifier(client_id)},
            missing=True,
        )
        return self.view_order(row, expected_client_id=client_id)

    def cancel(self, client_id):
        order = self.order(client_id)
        self.request("DELETE", "/v2/orders/" + identifier(order.broker_order_id))
        return self.order(client_id)


def adapter(provider, config, **kwargs):
    if provider not in ("oanda", "alpaca"):
        raise BrokerError("This demo broker is unsupported.")
    return (OandaPractice if provider == "oanda" else AlpacaPaper)(config, **kwargs)
