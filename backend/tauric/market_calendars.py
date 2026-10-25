"""Bounded UTC bar grids; local session rules preserve DST and scheduled closures."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .market_contracts import DataContext

SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
NY = ZoneInfo("America/New_York")
# Published OANDA UK regular sessions, checked 25 October 2026:
# https://www.oanda.com/uk-en/trading/hours-of-operation/
# Unknown products and holiday exceptions retain conservative gap checking.
METALS = {"XAU_USD", "XAG_USD"}
US_PRODUCTS = {
    "XPT_USD",
    "XPD_USD",
    "WTICO_USD",
    "NATGAS_USD",
    "XCU_USD",
    "SPX500_USD",
    "NAS100_USD",
    "US30_USD",
    "US2000_USD",
    "JP225_USD",
    "USB02Y_USD",
    "USB05Y_USD",
    "USB10Y_USD",
    "USB30Y_USD",
}


def default_context(spec):
    if spec.asset_type in {"commodity", "cfd"}:
        symbol = getattr(spec, "provider_symbol", "")
        known = symbol in METALS | US_PRODUCTS
        return DataContext(
            calendar_id="oanda_product" if known else "provider_observed",
            timezone="UTC",
            price_side="bid_ask",
            volume_type="provider_ticks",
            provider_symbol=symbol,
            calendar_source="OANDA published regular product sessions; holiday exceptions unverified"
            if known
            else "Observed OANDA closed intraday bars; no inferred product holiday calendar",
            warnings=[
                "Product holiday exceptions are unverified; unexpected absences remain data gaps."
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
    if symbol not in METALS | US_PRODUCTS:
        return
    day = start.astimezone(NY).date() - timedelta(days=1)
    last = end.astimezone(NY).date() + timedelta(days=1)
    while day <= last:
        if day.weekday() in (6, 0, 1, 2, 3):
            yield (
                datetime.combine(day, time(18, 5 if symbol in METALS else 1), NY).astimezone(UTC),
                datetime.combine(day + timedelta(days=1), time(16, 59), NY).astimezone(UTC),
            )
        day += timedelta(days=1)


def oanda_session_open(symbol, product_type, stamp):
    if product_type == "CURRENCY":
        if "TRY" in symbol.split("_"):
            return None
        if "NZD" in symbol.split("_"):
            local = stamp.astimezone(ZoneInfo("Pacific/Auckland")).time().replace(tzinfo=None)
            if time(6, 59) <= local < time(7, 5):
                return False
        return any(op <= stamp < close for op, close in fx_sessions(stamp, stamp))
    if symbol in METALS | US_PRODUCTS:
        return any(op <= stamp < close for op, close in product_sessions(symbol, stamp, stamp))
    return None


def fx_sessions(start, end):
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
        if context.provider_symbol not in METALS | US_PRODUCTS:
            raise ValueError("The product has no verified regular session calendar")
        sessions = product_sessions(context.provider_symbol, start, end)
    else:
        sessions = fx_sessions(start, end)
    for opening, closing in sessions:
        if context.calendar_id in {"oanda_fx", "oanda_product"}:
            day = opening.astimezone(NY).date()
            anchor = datetime.combine(day, time(17), NY).astimezone(UTC)
            # Daily bars end at the following NY alignment, not a fixed UTC duration.
            if interval == "1d":
                finish = datetime.combine(day + timedelta(days=1), time(17), NY).astimezone(UTC)
                if start <= anchor <= end:
                    yield anchor, finish
                continue
            stamp = anchor
            while stamp < closing:
                if stamp + step > opening and start <= stamp <= end:
                    yield stamp, stamp + step
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
        return any(op <= stamp < close for op, close in fx_sessions(stamp, stamp))
    if context.calendar_id == "oanda_product":
        if context.provider_symbol not in METALS | US_PRODUCTS:
            return None
        return any(
            op <= stamp < close
            for op, close in product_sessions(context.provider_symbol, stamp, stamp)
        )
    if not context.coverage_start <= stamp <= context.coverage_end:
        return None
    return any(s.open <= stamp < s.close for s in context.sessions)
