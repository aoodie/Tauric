"""Reject malformed evidence; count absent trading bars without filling them."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from .market_calendars import SECONDS, periods
from .market_contracts import Candle, DataContext, DataQuality, PriceOHLC


def verify(candles, interval, context, as_of, *, require_closed=False):
    context = DataContext.model_validate(context)
    bars = [Candle.model_validate(b) for b in candles]
    if not bars or len(bars) > 5000:
        raise ValueError("Provide between 1 and 5,000 candles")
    first, last = bars[0].open_time, bars[-1].open_time
    expected = {}
    # Guard sparse imports against enormous minute grids before constructing them.
    for op, close in periods(context, interval, first, last):
        if len(expected) >= 200000:
            raise ValueError("Too many expected trading bars; narrow the data range")
        expected[op] = close
    previous = None
    for bar in bars:
        PriceOHLC.model_validate({k: getattr(bar, k) for k in ("open", "high", "low", "close")})
        if bar.open_time > as_of or (bar.closed and bar.close_time > as_of):
            raise ValueError("Future or incorrectly finalized candle")
        if require_closed and not bar.closed:
            raise ValueError("Research requires closed candles")
        if not bar.closed and bar.close_time < as_of - timedelta(seconds=SECONDS[interval]):
            raise ValueError("An old candle is still declared incomplete")
        if previous and (
            bar.open_time <= previous.open_time or bar.open_time < previous.close_time
        ):
            raise ValueError("Candles must be ordered, unique and non-overlapping")
        finish = expected.get(bar.open_time)
        if finish is None or not 0 <= (finish - bar.close_time).total_seconds() <= 0.001:
            raise ValueError("Candle does not match the asset calendar or requested interval")
        if context.price_side == "bid_ask":
            if not bar.bid or not bar.ask:
                raise ValueError("Bid/ask data requires both price sides on every candle")
            if any(
                Decimal(getattr(bar.bid, k)) > Decimal(getattr(bar.ask, k))
                for k in ("open", "high", "low", "close")
            ):
                raise ValueError("Crossed bid/ask candle")
            if any(
                not Decimal(getattr(bar.bid, k))
                <= Decimal(getattr(bar, k))
                <= Decimal(getattr(bar.ask, k))
                for k in ("open", "high", "low", "close")
            ):
                raise ValueError("Reference OHLC must lie between bid and ask")
        elif bar.bid or bar.ask:
            raise ValueError("Price sides disagree with declared data semantics")
        previous = bar
    missing = len(expected) - len(bars)
    warnings = list(context.warnings)
    if any(Decimal(b.volume) == 0 for b in bars):
        warnings.append(
            "Zero-volume/activity bars are present; volume-dependent screening requires separate validation."
        )
    return DataQuality(
        status="gaps" if missing else "verified", missing_bars=missing, warnings=warnings
    )


def freshness(candles, interval, context, as_of):
    """Detect missing recent bars, including whole trading days; closures are exempt."""
    last = datetime.fromisoformat(candles[-1]["open_time"]).astimezone(UTC)
    count = 0
    for op, close in periods(context, interval, last, as_of):
        if op > last and close <= as_of - timedelta(seconds=SECONDS[interval]):
            count += 1
            if count:
                return False
    return True
