"""Market-data-only setup detection. No positions, research or execution imports."""

from datetime import timedelta
from decimal import Decimal, localcontext

from . import market

DETECTOR = "closed_bar_breakout_v1"


def prefilter(feed, parameters, stamp):
    """Cheap observable filters; unavailable configured evidence cannot pass."""
    if (
        feed.state != "fresh"
        or feed.gaps
        or not feed.snapshot_id
        or not feed.provider_time
        or not feed.received_at
        or not 0 <= (stamp - feed.received_at).total_seconds() <= parameters.max_data_age_seconds
        or not 0 <= (stamp - feed.provider_time).total_seconds() <= parameters.max_data_age_seconds
    ):
        return "STALE_DATA"
    if parameters.require_active_session and feed.session_state != "open":
        return "SESSION_INACTIVE"
    bars = [b for b in feed.candles if b.closed and b.close_time <= feed.provider_time]
    if len(bars) < parameters.lookback + 1:
        return "INSUFFICIENT_BARS"
    last = bars[-1]
    price = Decimal(last.close)
    if price <= 0:
        return "INVALID_PRICE"
    if parameters.minimum_price and price < Decimal(parameters.minimum_price):
        return "PRICE_RANGE"
    if parameters.maximum_price and price > Decimal(parameters.maximum_price):
        return "PRICE_RANGE"
    if parameters.minimum_volume and Decimal(last.volume) < Decimal(parameters.minimum_volume):
        return "LOW_VOLUME"
    volatility = (Decimal(last.high) - Decimal(last.low)) / price * 100
    if volatility < Decimal(str(parameters.minimum_volatility_percent)):
        return "LOW_VOLATILITY"
    if parameters.maximum_spread_bps is not None:
        if not last.bid or not last.ask:
            return "SPREAD_UNAVAILABLE"
        spread = (Decimal(last.ask.close) - Decimal(last.bid.close)) / price * 10000
        if spread < 0 or spread > Decimal(str(parameters.maximum_spread_bps)):
            return "WIDE_SPREAD"
    return None


def setup_score(features, minimum_volume_ratio):
    """0.5 base + 0.25 setup distance quality + 0.25 relative activity quality.

    A 1% move beyond the family reference and twice its minimum activity saturate each term.
    FX activity remains provider ticks, stocks selected-feed shares, crypto base volume.
    This score measures the detector evidence, never expected profit or trade permission.
    """
    with localcontext() as ctx:
        ctx.prec = 120
        strength = min(
            Decimal(1),
            max(
                Decimal(0),
                Decimal(
                    features.get("setup_strength_percent", features.get("breakout_percent", "0"))
                ),
            ),
        )
        activity = min(
            Decimal(1),
            max(
                Decimal(0), Decimal(features["volume_ratio"]) / (2 * Decimal(minimum_volume_ratio))
            ),
        )
        return float(
            (Decimal("0.5") + Decimal("0.25") * strength + Decimal("0.25") * activity).quantize(
                Decimal("0.000001")
            )
        )


def _breakout(feed, parameters):
    if feed.instrument.asset_type in {"equity", "etf"}:
        from .stock_execution import detect as detect_stocks

        return detect_stocks(feed, parameters)
    window, failure = _window(feed, parameters)
    if window is None:
        return None, failure
    with localcontext() as ctx:
        ctx.prec = 120
        prior_high = max(Decimal(b.high) for b in window[:-1])
        average = sum(Decimal(b.volume) for b in window[:-1]) / parameters.lookback
        if average <= 0:
            return None, "Historical volume is unavailable; the volume filter cannot pass."
        close = Decimal(window[-1].close)
        ratio = Decimal(window[-1].volume) / average
        if close <= prior_high or ratio < Decimal(parameters.minimum_volume_ratio):
            return None, "The closed-bar breakout and volume filters did not both pass."
        features = {
            "close": format(close, "f"),
            "prior_high": format(prior_high, "f"),
            "volume_ratio": format(ratio.quantize(Decimal("0.00000001")), "f"),
            "breakout_percent": format(
                ((close / prior_high - 1) * 100).quantize(Decimal("0.00000001")), "f"
            ),
        }
    evidence = {
        "detector_id": DETECTOR,
        "instrument_id": feed.instrument.id,
        "provider": feed.source,
        "interval": feed.interval,
        "volume_type": feed.instrument.volume_type,
        "lookback": parameters.lookback,
        "minimum_volume_ratio": parameters.minimum_volume_ratio,
        "bars": [v.model_dump(mode="json") for v in window],
    }
    return {
        "evidence": evidence,
        "features": features,
        "bar_close_time": window[-1].close_time.isoformat(),
        "reasons": [
            "Close exceeded every high in the configured prior closed-bar window.",
            "Provider tick activity met the configured ratio; this is not exchange volume."
            if feed.instrument.asset_type == "fx"
            else "Exchange-base volume met the configured ratio to that window.",
        ],
    }, None


def _window(feed, parameters):
    """New families retain the existing asset-specific closed-bar quality checks."""
    if feed.instrument.asset_type in {"equity", "etf"}:
        from fastapi import HTTPException

        from .stock_execution import closed_window

        try:
            return closed_window(feed, parameters.lookback + 1), None
        except HTTPException as failure:
            return None, str(failure.detail)
    if feed.state != "fresh" or feed.gaps or not feed.snapshot_id or not feed.provider_time:
        return None, "Fresh, gap-free candle evidence is required."
    bars = [b for b in feed.candles if b.closed and b.close_time <= feed.provider_time]
    if len(bars) < parameters.lookback + 1:
        return None, "Not enough closed candles for the configured lookback."
    window = bars[-parameters.lookback - 1 :]
    if feed.instrument.asset_type == "fx":
        from .data_quality import verify
        from .market_calendars import periods

        try:
            quality = verify(
                [b.model_dump(mode="json") for b in window],
                feed.interval,
                feed.data_context.model_dump(mode="json"),
                feed.provider_time,
                require_closed=True,
            )
            if quality.missing_bars or any(
                op > window[-1].open_time and close <= feed.provider_time - timedelta(seconds=1)
                for op, close in periods(
                    feed.data_context.model_dump(mode="json"),
                    feed.interval,
                    window[-1].open_time,
                    feed.provider_time,
                )
            ):
                return None, "The latest expected trading candle is missing."
        except (ValueError, ArithmeticError):
            return None, "Forex calendar or price-side checks failed."
    elif (feed.provider_time - window[-1].close_time).total_seconds() > market.INTERVAL_SECONDS[
        feed.interval
    ] + 1:
        return None, "The last closed candle is outdated."
    return window, None


def detect(feed, parameters):
    if parameters.detector_id == DETECTOR:
        return _breakout(feed, parameters)
    window, failure = _window(feed, parameters)
    if window is None:
        return None, failure
    with localcontext() as ctx:
        ctx.prec = 120
        prior = window[:-1]
        average = sum(Decimal(b.volume) for b in prior) / len(prior)
        if average <= 0:
            return None, "Historical activity is unavailable."
        close = Decimal(window[-1].close)
        ratio = Decimal(window[-1].volume) / average
        if ratio < Decimal(parameters.minimum_volume_ratio):
            return None, "The closed-bar activity filter did not pass."
        features = {
            "close": format(close, "f"),
            "volume_ratio": format(ratio.quantize(Decimal("0.00000001")), "f"),
        }
        if parameters.detector_id == "closed_bar_breakdown_v1":
            reference = min(Decimal(b.low) for b in prior)
            if close >= reference or reference <= 0:
                return None, "The closed candle did not break below the prior lows."
            direction = "short"
            strength = (1 - close / reference) * 100
            features["prior_low"] = format(reference, "f")
            reason = "Close fell below every low in the configured prior closed-bar window."
        else:
            if len(prior) < 4:
                return None, "Trend pullbacks require four prior closed candles."
            split = len(prior) // 2
            early = sum(Decimal(b.close) for b in prior[:split]) / split
            recent = sum(Decimal(b.close) for b in prior[split:]) / (len(prior) - split)
            reference = sum(Decimal(b.close) for b in prior) / len(prior)
            previous = Decimal(prior[-1].close)
            if reference <= 0:
                return None, "The trend reference price is invalid."
            if recent > early and previous <= reference < close:
                direction = "long"
            elif recent < early and previous >= reference > close:
                direction = "short"
            else:
                return None, "No closed-bar trend pullback and recovery passed."
            strength = abs(close / reference - 1) * 100
            features.update(
                reference_price=format(reference, "f"),
                early_mean=format(early, "f"),
                recent_mean=format(recent, "f"),
                previous_close=format(previous, "f"),
            )
            reason = "A completed candle crossed back through the prior mean after a pullback, in the direction of the observed trend."
        features["setup_strength_percent"] = format(strength.quantize(Decimal("0.00000001")), "f")
    evidence = {
        "detector_id": parameters.detector_id,
        "instrument_id": feed.instrument.id,
        "provider": feed.source,
        "interval": feed.interval,
        "volume_type": feed.instrument.volume_type,
        "lookback": parameters.lookback,
        "minimum_volume_ratio": parameters.minimum_volume_ratio,
        "direction": direction,
        "bars": [b.model_dump(mode="json") for b in window],
    }
    if feed.instrument.asset_type in {"equity", "etf"}:
        # Bound calendar evidence to the actual window rather than the moving provider response.
        context = feed.data_context.model_dump(mode="json")
        sessions = [
            s
            for s in feed.data_context.sessions
            if s.close > window[0].open_time and s.open <= window[-1].open_time
        ]
        context.update(
            sessions=[s.model_dump(mode="json") for s in sessions],
            coverage_start=sessions[0].open.isoformat(),
            coverage_end=sessions[-1].close.isoformat(),
        )
        evidence["data_context"] = context
    elif feed.instrument.asset_type == "fx":
        # FX uses provider weekly session rules rather than an explicit stock calendar.
        evidence["data_context"] = feed.data_context.model_dump(mode="json")
    activity = {
        "provider_ticks": "Provider tick activity",
        "feed_shares": "Selected-feed share volume",
        "exchange_base": "Exchange-base volume",
    }[feed.instrument.volume_type]
    return {
        "evidence": evidence,
        "features": features,
        "direction": direction,
        "bar_close_time": window[-1].close_time.isoformat(),
        "reasons": [
            reason,
            activity + " met the configured ratio; source volume semantics are preserved.",
        ],
    }, None
