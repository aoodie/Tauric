"""Bounded UTC bar grids; local session rules preserve DST and scheduled closures."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .market_contracts import DataContext
from .oanda_sessions import HOLIDAY_COVERAGE, RULES
from .oanda_sessions import sessions as oanda_sessions

SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
NY = ZoneInfo("America/New_York")


def default_context(spec):
    if spec.asset_type in {"commodity", "cfd"}:
        symbol = getattr(spec, "provider_symbol", "")
        known = symbol in RULES
        return DataContext(
            calendar_id="oanda_product" if known else "provider_observed",
            timezone="UTC",
            price_side="bid_ask",
            volume_type="provider_ticks",
            provider_symbol=symbol,
            calendar_source=f"OANDA UK regular sessions and published exceptions {HOLIDAY_COVERAGE[0]} through {HOLIDAY_COVERAGE[1]} (checked 2026-10-25)"
            if known
            else "Observed OANDA closed intraday bars; no inferred product holiday calendar",
            warnings=[
                "Unpublished or ambiguous holiday exceptions remain data gaps; calendars are not extrapolated."
                if known
                else "Product trading hours and holiday exceptions are not inferred from Forex sessions.",
                "Missing expected intraday bars remain gaps and block contiguous-signal evaluation.",
                "Provider tick counts are activity, not exchange volume; CFDs are not exchange futures.",
            ],
        )
    return DataContext(
        calendar_id=spec.calendar_id,
        timezone="UTC" if spec.asset_type == "crypto_spot" else "America/New_York",
        price_side=spec.price_side,
        volume_type=spec.volume_type,
        provider_symbol=getattr(spec, "provider_symbol", ""),
        calendar_source="UTC continuous"
        if spec.asset_type == "crypto_spot"
        else "OANDA New York weekly/maintenance rules",
        warnings=[]
        if spec.asset_type == "crypto_spot"
        else [
            "FX holiday exceptions are not supplied by the candle endpoint; unexpected absences remain data gaps.",
            "Provider tick counts are activity, not exchange-traded volume.",
            "Reference OHLC is midpoint; executable bid and ask bars are retained separately.",
        ],
    )


def product_sessions(symbol, start, end):
    yield from oanda_sessions(symbol, start, end)


def oanda_session_open(symbol, product_type, stamp):
    if product_type == "CURRENCY":
        if "TRY" in symbol.split("_"):
            return None
        return any(
            op <= stamp < close for op, close in oanda_sessions(symbol, stamp, stamp, currency=True)
        )
    if symbol in RULES:
        return any(op <= stamp < close for op, close in product_sessions(symbol, stamp, stamp))
    return None


def fx_sessions(start, end, symbol=""):
    if symbol:
        yield from oanda_sessions(symbol, start, end, currency=True)
        return
    day = start.astimezone(NY).date() - timedelta(days=1)
    last = end.astimezone(NY).date() + timedelta(days=1)
    while day <= last:
        # Sunday–Thursday 17:05 through the following day 16:59.
        if day.weekday() in (6, 0, 1, 2, 3):
            yield (
                datetime.combine(day, time(17, 5), NY).astimezone(UTC),
                datetime.combine(day + timedelta(days=1), time(16, 59), NY).astimezone(UTC),
            )
        day += timedelta(days=1)


def periods(context, interval, start, end):
    """Expected bar windows intersecting trading hours, inclusive opening bounds."""
    context = DataContext.model_validate(context)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    if end < start or (end - start).days > 1500:
        raise ValueError("Calendar range must be ordered and bounded to 1,500 days")
    step = timedelta(seconds=SECONDS[interval])
    if context.calendar_id in {"crypto_24x7", "provider_observed"}:
        stamp = start
        while stamp <= end:
            if stamp >= start:
                yield stamp, stamp + step
            stamp += step
        return
    if context.calendar_id == "us_equity":
        if start < context.coverage_start or end > context.coverage_end:
            raise ValueError("Calendar does not cover this data range")
        sessions = [(s.open.astimezone(UTC), s.close.astimezone(UTC)) for s in context.sessions]
    elif context.calendar_id == "oanda_product":
        if context.provider_symbol not in RULES:
            raise ValueError("The product has no verified regular session calendar")
        sessions = product_sessions(context.provider_symbol, start, end)
    else:
        sessions = fx_sessions(start, end, context.provider_symbol)
    emitted = None
    for opening, closing in sessions:
        if context.calendar_id in {"oanda_fx", "oanda_product"}:
            day = opening.astimezone(NY).date()
            anchor = datetime.combine(day, time(17), NY).astimezone(UTC)
            if anchor > opening:
                day -= timedelta(days=1)
                anchor = datetime.combine(day, time(17), NY).astimezone(UTC)
            # Daily bars end at the following NY alignment, not a fixed UTC duration.
            if interval == "1d":
                while anchor < closing:
                    day += timedelta(days=1)
                    finish = datetime.combine(day, time(17), NY).astimezone(UTC)
                    if start <= anchor <= end and (emitted is None or anchor > emitted):
                        yield anchor, finish
                        emitted = anchor
                    anchor = finish
                continue
            stamp = anchor
            while stamp < closing:
                if (
                    stamp + step > opening
                    and start <= stamp <= end
                    and (emitted is None or stamp > emitted)
                ):
                    yield stamp, stamp + step
                    emitted = stamp
                stamp += step
        elif interval == "1d":
            if start <= opening <= end:
                yield opening, closing
        else:
            stamp = opening
            while stamp < closing:
                if start <= stamp <= end:
                    yield stamp, min(stamp + step, closing)
                stamp += step


def session_open(context, stamp):
    context = DataContext.model_validate(context)
    if context.calendar_id == "crypto_24x7":
        return True
    if context.calendar_id == "oanda_fx":
        return any(
            op <= stamp < close for op, close in fx_sessions(stamp, stamp, context.provider_symbol)
        )
    if context.calendar_id == "oanda_product":
        if context.provider_symbol not in RULES:
            return None
        return any(
            op <= stamp < close
            for op, close in product_sessions(context.provider_symbol, stamp, stamp)
        )
    if (
        context.coverage_start is None
        or context.coverage_end is None
        or not context.coverage_start <= stamp <= context.coverage_end
    ):
        return None
    return any(s.open <= stamp < s.close for s in context.sessions)
