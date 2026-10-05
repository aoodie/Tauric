"""Stock-specific paper guards and calendar-aware, research-only screening.

Prices are observed feed trades, not NBBO or broker fills. This bounded route
uses whole shares, regular sessions and unlevered USD cash; it places no orders.
"""

from decimal import Decimal, localcontext

from fastapi import HTTPException

from .data_quality import verify
from .market_calendars import periods, session_open
from .models import now

D = Decimal
ASSETS = {"equity", "etf"}


def supported(spec):
    return (
        spec.asset_type in ASSETS
        and spec.provider == "alpaca_stocks"
        and spec.quote_currency == "USD"
        and spec.calendar_id == "us_equity"
        and spec.volume_type == "feed_shares"
    )


def fail(message, code=409):
    raise HTTPException(code, message)


def deployment(spec, currency, quantity_step, costs):
    if not supported(spec) or currency != spec.quote_currency:
        fail("Stock paper deployments require a registered US stock/ETF and USD cash account.", 422)
    step = D(quantity_step)
    if not step.is_finite() or step < 1 or step != step.to_integral_value():
        fail("This stock paper route uses whole-share quantity steps of at least one.", 422)
    if not costs or costs.asset_type != spec.asset_type or costs.financing_daily_bps is not None:
        fail("Enter explicit stock/ETF spread and commission assumptions for this deployment.", 422)


def checked_feed(feed, *, stamp=None, require_open=False, max_age_seconds=60):
    stamp = stamp or now()
    context = feed.data_context
    if (
        not supported(feed.instrument)
        or feed.source != "alpaca_stocks"
        or not context
        or context.calendar_id != "us_equity"
        or context.timezone != "America/New_York"
        or context.price_side != "trades"
        or context.volume_type != "feed_shares"
        or context.adjustment not in ("raw", "split")
        or context.feed not in ("iex", "sip")
        or not feed.snapshot_id
        or not feed.checksum
        or feed.state != "fresh"
        or feed.gaps
        or not feed.quality
        or feed.quality.status != "verified"
        or feed.quality.missing_bars
        or not feed.candles
        or not feed.provider_time
        or not feed.received_at
        or not 0 <= (stamp - feed.received_at).total_seconds() <= min(60, max_age_seconds)
        or not 0 <= (stamp - feed.provider_time).total_seconds() <= max_age_seconds
    ):
        fail("Fresh stock feed, calendar and complete candle evidence are required.")
    try:
        quality = verify(
            [b.model_dump(mode="json") for b in feed.candles],
            feed.interval,
            context,
            feed.provider_time,
        )
    except (ValueError, TypeError, ArithmeticError):
        fail("Stock candle prices, calendar periods or source semantics failed verification.")
    if quality.missing_bars:
        fail("Stock evidence has missing trading bars; prices are never filled forward.")
    if session_open(context, stamp) is None:
        fail("The stock calendar does not cover the current time.")
    if require_open and not session_open(context, stamp):
        fail("Stock paper fills require an open regular trading session.")
    return context


def quote(feed, max_age_seconds, *, stamp=None):
    stamp = stamp or now()
    checked_feed(feed, stamp=stamp, require_open=True, max_age_seconds=max_age_seconds)
    if feed.interval != "1m":
        fail("Stock paper fills require a one-minute observed trade series.")
    last = feed.candles[-1]
    if not 0 <= (stamp - last.open_time).total_seconds() <= max_age_seconds + 60:
        fail("The last observed stock trade bar is too old for a paper fill.")
    if not session_open(feed.data_context, last.open_time) or D(last.volume) <= 0:
        fail("A positive-volume regular-session stock trade observation is required.")
    price = D(last.close)
    if price <= 0:
        fail("The observed stock price is invalid.")
    return price


def closed_window(feed, count, *, stamp=None, require_open=False):
    # Research screening may use an explicitly delayed feed; execution may not.
    checked_feed(
        feed, stamp=stamp, require_open=require_open, max_age_seconds=60 if require_open else 960
    )
    bars = [b for b in feed.candles if b.closed and b.close_time <= feed.provider_time]
    if len(bars) < count:
        fail("Not enough completed stock session bars for the configured rules.")
    latest = bars[-1]
    # A forming bar may be present; it never substitutes for a missing complete bar.
    for opening, closing in periods(
        feed.data_context, feed.interval, latest.open_time, feed.provider_time
    ):
        if opening > latest.open_time and closing <= feed.provider_time:
            fail("The most recent completed stock session bar is missing.")
    return bars[-count:]


def detect(feed, parameters):
    try:
        bars = closed_window(feed, parameters.lookback + 1)
    except HTTPException as error:
        return None, str(error.detail)
    with localcontext() as ctx:
        ctx.prec = 120
        high = max(D(b.high) for b in bars[:-1])
        average = sum(D(b.volume) for b in bars[:-1]) / parameters.lookback
        if average <= 0:
            return None, "Prior selected-feed share volume is unavailable."
        close = D(bars[-1].close)
        ratio = D(bars[-1].volume) / average
        if close <= high or ratio < D(parameters.minimum_volume_ratio):
            return (
                None,
                "The closed stock breakout and selected-feed volume filters did not both pass.",
            )
        features = {
            "close": format(close, "f"),
            "prior_high": format(high, "f"),
            "volume_ratio": format(ratio.quantize(D("0.00000001")), "f"),
            "breakout_percent": format(((close / high - 1) * 100).quantize(D("0.00000001")), "f"),
        }
    sessions = [
        s
        for s in feed.data_context.sessions
        if s.close > bars[0].open_time and s.open <= bars[-1].open_time
    ]
    evidence_context = feed.data_context.model_dump(mode="json")
    evidence_context.update(
        sessions=[s.model_dump(mode="json") for s in sessions],
        coverage_start=sessions[0].open.isoformat(),
        coverage_end=sessions[-1].close.isoformat(),
    )
    return {
        "provider_time": feed.provider_time.isoformat(),
        "received_at": feed.received_at.isoformat(),
        "features": features,
        "bar_close_time": bars[-1].close_time.isoformat(),
        "evidence": {
            "detector_id": parameters.detector_id,
            "instrument_id": feed.instrument.id,
            "provider": feed.source,
            "interval": feed.interval,
            "volume_type": "feed_shares",
            "lookback": parameters.lookback,
            "minimum_volume_ratio": parameters.minimum_volume_ratio,
            "data_context": evidence_context,
            "execution_authority": False,
            "bars": [b.model_dump(mode="json") for b in bars],
        },
        "reasons": [
            "A completed regular-session close exceeded the prior session-bar highs.",
            f"Share volume on the selected {feed.data_context.feed.upper()} feed met the configured ratio; it is not necessarily consolidated market volume.",
        ],
    }, None


def evaluate(feed, rules, position=None):
    """Pinned session bars supply timing; current paper quotes supply fill prices."""
    try:
        bars = closed_window(
            feed, max(rules.entry_lookback, rules.exit_lookback) + 1, require_open=True
        )
    except HTTPException as error:
        return {"action": "blocked", "reasons": [str(error.detail)]}
    last = bars[-1]
    evidence = {
        "bar_close_time": last.close_time.isoformat(),
        "bars": [b.model_dump(mode="json") for b in bars],
        "data_context": feed.data_context.model_dump(mode="json"),
    }

    def result(action, reason, **values):
        return {"action": action, "reasons": [reason], **evidence, **values}

    with localcontext() as ctx:
        ctx.prec = 120
        if position:
            if last.open_time < position.created_at:
                return result(
                    "hold", "Wait for a complete stock session bar after this position opened."
                )
            entry, stop = D(position.payload["price"]), D(position.payload["stop_price"])
            if stop >= entry:
                return result("blocked", "The stock position's opening stop reference is invalid.")
            target = entry + (entry - stop) * D(rules.take_profit_r)
            channel = min(D(b.low) for b in bars[-rules.exit_lookback - 1 : -1])
            values = {
                "position_id": position.id,
                "stop_price": format(stop, "f"),
                "target_price": format(target, "f"),
                "exit_channel": format(channel, "f"),
            }
            if D(last.low) <= stop:
                return result(
                    "exit",
                    "The closed stock bar touched its stop reference; stop takes precedence over target.",
                    exit_trigger="stop",
                    **values,
                )
            if D(last.high) >= target:
                return result(
                    "exit",
                    "The closed stock bar touched its profit target.",
                    exit_trigger="take_profit",
                    **values,
                )
            if D(last.close) < channel:
                return result(
                    "exit",
                    "Close fell below the prior stock session-bar exit channel.",
                    exit_trigger="channel",
                    **values,
                )
            return result("hold", "No attributed stock exit rule passed.", **values)
        prior = bars[-rules.entry_lookback - 1 : -1]
        high, low = max(D(b.high) for b in prior), min(D(b.low) for b in prior)
        average = sum(D(b.volume) for b in prior) / len(prior)
        if average <= 0:
            return result("blocked", "Prior selected-feed share volume is unavailable.")
        ratio = D(last.volume) / average
        values = {
            "prior_high": format(high, "f"),
            "volume_ratio": format(ratio.quantize(D("0.00000001")), "f"),
        }
        if D(last.close) <= high or ratio < D(rules.minimum_volume_ratio):
            return result(
                "hold", "Stock breakout and selected-feed share volume did not both pass.", **values
            )
        return result(
            "enter",
            "A completed stock session bar passed the breakout and selected-feed share-volume rules.",
            stop_price=format(low.normalize(), "f"),
            **values,
        )
