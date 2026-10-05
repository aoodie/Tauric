"""Deterministic closed-bar rules; evaluation has no execution authority."""

from decimal import Decimal, localcontext

from . import market
from .models import now


def evaluate(feed, rules, position=None):
    def result(action, reason, **values):
        return {"action": action, "reasons": [reason], **values}

    stamp = now()
    duration = market.INTERVAL_SECONDS[rules.interval]
    if (
        feed.state != "fresh"
        or feed.gaps
        or not feed.snapshot_id
        or not feed.provider_time
        or not feed.received_at
        or not 0 <= (stamp - feed.provider_time).total_seconds() <= 60
        or not 0 <= (stamp - feed.received_at).total_seconds() <= 60
    ):
        return result("blocked", "Fresh, gap-free and correctly timed candle evidence is required.")
    if any(b.closed and b.close_time > feed.provider_time for b in feed.candles):
        return result("blocked", "A closed candle has a future timestamp.")
    bars = [b for b in feed.candles if b.closed and b.close_time <= feed.provider_time]
    if bars and not 0 <= (feed.provider_time - bars[-1].close_time).total_seconds() <= duration + 1:
        return result("blocked", "The latest closed candle is outdated.")
    return evaluate_closed_bars(
        bars,
        rules,
        position,
        data_context=feed.data_context.model_dump(mode="json")
        if feed.instrument.asset_type == "fx" and feed.data_context
        else None,
    )


def evaluate_closed_bars(bars, rules, position=None, *, data_context=None):
    def result(action, reason, **values):
        return {"action": action, "reasons": [reason], **values}

    duration = market.INTERVAL_SECONDS[rules.interval]
    count = max(rules.entry_lookback, rules.exit_lookback) + 1
    if len(bars) < count:
        return result("blocked", "Not enough closed candles for these rules.")
    bars = bars[-count:]
    if data_context:
        from .data_quality import verify

        try:
            checked = verify(
                [b.model_dump(mode="json") for b in bars],
                rules.interval,
                data_context,
                bars[-1].close_time,
                require_closed=True,
            )
            if checked.missing_bars:
                return result("blocked", "Missing expected trading candles.")
        except (ValueError, ArithmeticError):
            return result("blocked", "Asset calendar or price-side validation failed.")
    for i, bar in enumerate(bars):
        op, hi, lo, cl, volume = [
            Decimal(getattr(bar, k)) for k in ("open", "high", "low", "close", "volume")
        ]
        if (
            not bar.closed
            or not lo <= min(op, cl) <= max(op, cl) <= hi
            or lo <= 0
            or volume < 0
            or (
                not data_context
                and abs((bar.close_time - bar.open_time).total_seconds() - duration) > 0.01
            )
            or (
                not data_context
                and i
                and (bar.open_time - bars[i - 1].open_time).total_seconds() != duration
            )
        ):
            return result("blocked", "Candle prices, ordering or duration could not be verified.")
    last = bars[-1]
    evidence = {
        "bar_close_time": last.close_time.isoformat(),
        "bars": [b.model_dump(mode="json") for b in bars],
    }

    def executable(bar, field):
        if data_context and data_context.get("price_side") == "bid_ask":
            return Decimal(getattr(bar.bid, field))
        return Decimal(getattr(bar, field))

    with localcontext() as context:
        context.prec = 120
        if position:
            # Do not use the portion of an OHLC candle that predates this fill.
            if last.open_time < position.created_at:
                return result(
                    "hold", "Wait for a complete candle after the position opened.", **evidence
                )
            entry = Decimal(position.payload["price"])
            stop = Decimal(position.payload["stop_price"])
            if stop >= entry:
                return result("blocked", "The opening stop reference is invalid.", **evidence)
            target = entry + (entry - stop) * Decimal(rules.take_profit_r)
            channel = min(executable(b, "low") for b in bars[-rules.exit_lookback - 1 : -1])
            values = {
                **evidence,
                "position_id": position.id,
                "stop_price": format(stop, "f"),
                "target_price": format(target, "f"),
                "exit_channel": format(channel, "f"),
            }
            if executable(last, "low") <= stop:
                return result(
                    "exit",
                    "The closed candle touched the opening stop reference. Stop takes precedence if both levels were touched.",
                    exit_trigger="stop",
                    **values,
                )
            if executable(last, "high") >= target:
                return result(
                    "exit",
                    "The closed candle touched the configured profit target.",
                    exit_trigger="take_profit",
                    **values,
                )
            if executable(last, "close") < channel:
                return result(
                    "exit",
                    "Close fell below the prior closed-bar exit channel.",
                    exit_trigger="channel",
                    **values,
                )
            return result("hold", "No exit rule passed for the attributed position.", **values)
        prior = bars[-rules.entry_lookback - 1 : -1]
        high = max(Decimal(b.high) for b in prior)
        low = min(executable(b, "low") for b in prior)
        average = sum(Decimal(b.volume) for b in prior) / len(prior)
        if average <= 0:
            return result(
                "blocked",
                "Historical volume is unavailable; the volume filter cannot pass.",
                **evidence,
            )
        ratio = Decimal(last.volume) / average
        values = {
            **evidence,
            "prior_high": format(high, "f"),
            "volume_ratio": format(ratio.quantize(Decimal("0.00000001")), "f"),
        }
        if Decimal(last.close) <= high or ratio < Decimal(rules.minimum_volume_ratio):
            return result("hold", "The breakout and volume filters did not both pass.", **values)
        return result(
            "enter",
            "Close exceeded prior highs and provider tick activity met the configured ratio. Stop reference uses the lowest prior low."
            if data_context and data_context.get("volume_type") == "provider_ticks"
            else "Close exceeded prior highs and exchange-base volume met the configured ratio. Stop reference uses the lowest prior low.",
            stop_price=format(low.normalize(), "f"),
            **values,
        )
