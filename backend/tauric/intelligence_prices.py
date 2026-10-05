"""Bounded daily browsing prices; never used as execution or native research input."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta

import httpx
from fastapi import HTTPException

from . import market
from .data_providers import OandaFX, connections, stamp
from .data_quality import freshness, verify
from .instrument_discovery import request_json
from .intelligence_catalog import latest, store
from .market_calendars import NY
from .market_contracts import Candle, DataContext, DataQuality, MarketSession, PriceOHLC
from .models import now


def key(spec):
    return "price:" + spec.provider + ":" + spec.provider_symbol


def stock_batch(specs, config):
    """One shared calendar and bounded paginated daily aggregates for a displayed page."""
    connection = config.alpaca
    headers = {
        "APCA-API-KEY-ID": connection.key.get_secret_value(),
        "APCA-API-SECRET-KEY": connection.secret.get_secret_value(),
    }
    if not all(headers.values()):
        raise HTTPException(
            503, "Configure the Alpaca data connection before loading stock prices."
        )
    received = now()
    asof = received - timedelta(minutes=connection.delay_minutes)
    start = asof - timedelta(days=65)
    host = "paper-api.alpaca.markets" if connection.environment == "paper" else "api.alpaca.markets"
    sessions = request_json(
        f"https://{host}/v2/calendar",
        headers,
        {
            "start": start.astimezone(NY).date().isoformat(),
            "end": received.astimezone(NY).date().isoformat(),
        },
    )
    if not isinstance(sessions, list) or len(sessions) > 100:
        raise ValueError("Invalid calendar")
    calendar = [
        MarketSession(
            open=datetime.combine(
                datetime.fromisoformat(s["date"]).date(), time.fromisoformat(s["open"]), NY
            ),
            close=datetime.combine(
                datetime.fromisoformat(s["date"]).date(), time.fromisoformat(s["close"]), NY
            ),
        )
        for s in sessions
    ]
    context = DataContext(
        calendar_id="us_equity",
        timezone="America/New_York",
        price_side="trades",
        volume_type="feed_shares",
        adjustment="split",
        feed=connection.feed,
        calendar_source="Alpaca /v2/calendar",
        coverage_start=datetime.combine(start.astimezone(NY).date(), time.min, NY),
        coverage_end=datetime.combine(
            received.astimezone(NY).date() + timedelta(days=1), time.min, NY
        ),
        sessions=calendar,
        warnings=[
            "Provider-defined daily aggregates for browsing; day boundaries use New York midnight. This is separate from regular-session chart and research evidence.",
            "Split-adjusted retrospective prices; dividends and historical corporate-action availability are not modeled.",
            f"{connection.feed.upper()} feed share volume is not consolidated market volume.",
        ],
    )
    symbols = {s.provider_symbol for s in specs}
    params = {
        "symbols": ",".join(sorted(symbols)),
        "timeframe": "1Day",
        "start": start.isoformat(),
        "end": asof.isoformat(),
        "limit": 10000,
        "sort": "asc",
        "adjustment": "split",
        "feed": connection.feed,
        "asof": received.date().isoformat(),
    }
    raw, seen = {s: [] for s in symbols}, set()
    for _ in range(4):
        response = request_json("https://data.alpaca.markets/v2/stocks/bars", headers, params)
        bars = response.get("bars")
        if (
            not isinstance(bars, dict)
            or set(bars) - symbols
            or any(not isinstance(rows, list) for rows in bars.values())
        ):
            raise ValueError("Wrong-symbol bars")
        if sum(len(rows) for rows in bars.values()) > 2000:
            raise ValueError("Unbounded price response")
        for symbol, rows in bars.items():
            raw[symbol].extend(rows)
            if len(raw[symbol]) > 100:
                raise ValueError("Unbounded symbol history")
        token = response.get("next_page_token")
        if not token:
            break
        if not isinstance(token, str) or token in seen:
            raise ValueError("Repeated price page")
        seen.add(token)
        params["page_token"] = token
    else:
        raise ValueError("Incomplete price pages")
    session_map = {s.open.astimezone(NY).date(): s for s in calendar}
    result = {}
    for spec in specs:
        candles = []
        for row in raw[spec.provider_symbol]:
            date = stamp(row["t"]).astimezone(NY).date()
            session = session_map.get(date)
            if not session:
                raise ValueError("Daily price date is outside the calendar")
            opening = datetime.combine(date, time.min, NY)
            closing = datetime.combine(date + timedelta(days=1), time.min, NY)
            # Complete the provider's whole local day before comparing its aggregate.
            if closing > asof:
                continue
            candles.append(
                Candle(
                    open_time=opening,
                    close_time=closing,
                    open=str(row["o"]),
                    high=str(row["h"]),
                    low=str(row["l"]),
                    close=str(row["c"]),
                    volume=str(row["v"]),
                    closed=True,
                ).model_dump(mode="json")
            )
        result[spec.id] = (candles, asof, received, context.model_dump(mode="json"))
    return result


def validate(candles, context, asof, daily_aggregate=False):
    if not daily_aggregate:
        return verify(candles, "1d", context, asof)
    context = DataContext.model_validate(context)
    bars = [Candle.model_validate(bar) for bar in candles]
    if not bars or len(bars) > 100:
        raise ValueError("No bounded daily prices")
    days = {s.open.astimezone(NY).date() for s in context.sessions}
    previous = None
    for bar in bars:
        day = bar.open_time.astimezone(NY).date()
        if (
            day not in days
            or bar.open_time != datetime.combine(day, time.min, NY)
            or bar.close_time != datetime.combine(day + timedelta(days=1), time.min, NY)
            or not bar.closed
            or bar.close_time > asof
            or (previous and bar.open_time <= previous)
        ):
            raise ValueError("Invalid daily price date")
        PriceOHLC.model_validate({k: getattr(bar, k) for k in ("open", "high", "low", "close")})
        previous = bar.open_time
    observed = {bar.open_time.astimezone(NY).date() for bar in bars}
    expected = {
        d
        for d in days
        if bars[0].open_time.astimezone(NY).date() <= d <= bars[-1].open_time.astimezone(NY).date()
    }
    missing = len(expected - observed)
    return DataQuality(
        status="gaps" if missing else "verified", missing_bars=missing, warnings=context.warnings
    )


def current(candles, context, asof, daily_aggregate=False):
    if not daily_aggregate:
        return freshness(candles, "1d", context, asof)
    last = stamp(candles[-1]["open_time"])
    return not any(
        last < datetime.combine(s.open.astimezone(NY).date(), time.min, NY)
        and datetime.combine(s.open.astimezone(NY).date() + timedelta(days=1), time.min, NY) <= asof
        for s in DataContext.model_validate(context).sessions
    )


def collect_page(db, owner, specs):
    cached, needed, results = [], [], []
    for spec in specs:
        previous = latest(db, owner, key(spec))
        if previous and now() - previous.created_at < timedelta(minutes=5):
            cached.append(previous.id)
            results.append({"instrument_id": spec.id, "status": previous.payload["status"]})
        else:
            needed.append(spec)
    if not needed:
        return results, cached
    config = connections(db)
    stocks = [s for s in needed if s.provider == "alpaca_stocks"]
    values = {}
    if stocks:
        try:
            values.update(stock_batch(stocks, config))
        except (HTTPException, httpx.HTTPError, ValueError, KeyError, TypeError, ArithmeticError):
            values.update({s.id: None for s in stocks})
    others = [s for s in needed if s.provider != "alpaca_stocks"]

    def collect(spec):
        try:
            # Independent read-only instances keep background scanner refresh locks separate.
            if spec.provider == "oanda_v20":
                return OandaFX().collect(spec, "1d", config=config)
            return market.BinanceSpot().collect(spec, "1d")
        except HTTPException:
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        for spec, value in zip(others, pool.map(collect, others), strict=True):
            values[spec.id] = value
    for spec in needed:
        value = values.get(spec.id)
        payload = {
            "label": spec.symbol + " daily prices",
            "url": "https://docs.alpaca.markets/us/reference/stockbars"
            if spec.provider == "alpaca_stocks"
            else "https://developer.oanda.com/rest-live-v20/instrument-ep/"
            if spec.provider == "oanda_v20"
            else "https://developers.binance.com/en/docs/binance-spot-api-docs/rest-api/market-data-endpoints",
            "status": "unavailable",
            "warning": "Daily prices are unavailable or could not be verified. Saved evidence remains available.",
            "symbol": spec.model_dump(mode="json"),
            "checked_at": now().isoformat(),
            "candles": [],
            "provider_time": None,
            "received_at": None,
            "data_context": None,
            "quality": None,
            "daily_aggregate": spec.provider == "alpaca_stocks",
        }
        try:
            if not value:
                raise ValueError("Unavailable prices")
            candles, provider_time, received_at = value[:3]
            context = value[3]
            quality = validate(candles, context, provider_time, payload["daily_aggregate"])
            if max(provider_time, received_at) > now() + timedelta(seconds=90):
                raise ValueError("Future price receipt")
            if not current(candles, context, provider_time, payload["daily_aggregate"]):
                quality.status = "outdated"
            payload.update(
                status="available",
                warning=None,
                candles=candles,
                provider_time=provider_time.isoformat(),
                received_at=received_at.isoformat(),
                data_context=context,
                quality=quality.model_dump(mode="json"),
            )
        except (ValueError, TypeError, ArithmeticError, KeyError):
            pass
        row = store(db, owner, key(spec), payload)
        cached.append(row.id)
        results.append({"instrument_id": spec.id, "status": payload["status"]})
    return results, cached
