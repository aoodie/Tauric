"""Explicit research assumptions, quote-currency fees and NY rollover debits/credits."""

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

from .market_calendars import NY

D = Decimal


def stop_trigger_price(bar, field, costs):
    """A long FX stop triggers on bid, before slippage; other assets use reference OHLC."""
    observed = D(bar[field])
    if costs and costs.asset_type == "fx":
        if bar.get("bid") and bar.get("ask"):
            return D(bar["bid"][field])
        return observed * (1 - D(costs.spread_bps) / 20000)
    return observed


def execution_price(observed, side, bar, field, costs, slip):
    spread = D(costs.spread_bps) / 20000 if costs else D(0)
    price = observed
    if costs and costs.asset_type == "fx" and field == "stop":
        # The saved stop is an executable bid/ask threshold. Applying the spread
        # again would move the fill below the actual trigger for a long stop.
        spread = D(0)
    elif costs and costs.asset_type == "fx" and bar and bar.get("bid") and bar.get("ask"):
        name = "ask" if side == "buy" else "bid"
        price = D(bar[name][field])
        spread = D(0)
    return (
        price
        * (1 + spread if side == "buy" else 1 - spread)
        * (1 + slip if side == "buy" else 1 - slip)
    )


def commission(quantity, price, side, fee_rate, costs):
    fee = quantity * price * fee_rate
    if costs:
        fee = max(fee + quantity * D(costs.commission_per_unit), D(costs.minimum_commission))
        if side == "sell":
            fee += quantity * price * D(costs.sell_tax_bps) / 10000
    return fee


def rollover_times(start, end, costs):
    if not costs or costs.asset_type != "fx":
        return
    day = start.astimezone(NY).date()
    last = end.astimezone(NY).date()
    while day <= last:
        stamp = datetime.combine(day, time(17), NY).astimezone(UTC)
        if day.weekday() < 5 and start < stamp <= end:
            yield stamp, 3 if day.weekday() == costs.triple_rollover_weekday else 1
        day += timedelta(days=1)
