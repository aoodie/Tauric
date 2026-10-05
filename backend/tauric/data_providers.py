"""Read-only OANDA/Alpaca candle adapters. No account or order methods."""

import json
import threading
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import Field, SecretStr

from .config import runtime_config
from .market_calendars import NY, default_context, periods, session_open
from .market_contracts import Candle, DataContext, DataProviderView, MarketSession, PriceOHLC
from .models import now
from .schemas import Contract


class OandaConnection(Contract):
    token: SecretStr = SecretStr("")
    account_id: str = Field(default="", max_length=128, pattern=r"^$|^[0-9]+(?:-[0-9]+)*$")
    environment: Literal["practice", "live"] = "practice"


class AlpacaConnection(Contract):
    key: SecretStr = SecretStr("")
    secret: SecretStr = SecretStr("")
    environment: Literal["paper", "live"] = "paper"
    feed: Literal["iex", "sip"] = "iex"
    delay_minutes: int = Field(default=15, ge=0, le=15, strict=True)


class Connections(Contract):
    oanda: OandaConnection = Field(default_factory=OandaConnection)
    alpaca: AlpacaConnection = Field(default_factory=AlpacaConnection)


def host_connections():
    path = Path(runtime_config().market_connections_file)
    if not path.exists():
        return Connections()
    try:
        if path.stat().st_size > 16384:
            raise ValueError("Oversized connection file")
        return Connections.model_validate_json(path.read_text())
    except (OSError, ValueError):
        # Validation errors and HTTP responses can include secrets: never echo them.
        raise HTTPException(503, "The private market-data connection file is invalid.") from None


def connections(db=None):
    if db is None:
        return host_connections()
    from .market_connections import load_connections

    return load_connections(db)


def provider_status(db=None):
    config = connections(db)
    return [
        DataProviderView(
            id="binance_spot_public",
            label="Binance public spot",
            configured=True,
            detail="Public candles; connected observations are verified per snapshot.",
        ),
        DataProviderView(
            id="oanda_v20",
            label="OANDA FX",
            configured=bool(config.oanda.token.get_secret_value()),
            detail="Read-only midpoint/bid/ask candles; New York alignment; provider tick counts.",
        ),
        DataProviderView(
            id="alpaca_stocks",
            label="Alpaca stocks / ETFs",
            configured=bool(
                config.alpaca.key.get_secret_value() and config.alpaca.secret.get_secret_value()
            ),
            detail=f"{config.alpaca.feed.upper()} feed; {config.alpaca.delay_minutes} minute delay; split-adjusted regular-session aggregates.",
        ),
    ]


def stamp(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Provider timestamps must include a timezone")
    return result.astimezone(UTC)


def request(client, url, *, params=None, headers=None):
    response = client.get(url, params=params, headers=headers)
    response.raise_for_status()
    # Parsing via Decimal preserves provider JSON numeric precision.
    return json.loads(response.text, parse_float=Decimal)


class ReadOnlyProvider:
    def __init__(self):
        self.lock = threading.Lock()

    def collect(self, spec, interval, *, config=None):
        config = config if config is not None else connections()
        if interval not in spec.supported_intervals:
            raise HTTPException(422, "This provider does not support the selected interval.")
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, "A provider refresh is already running. Try again shortly.")
        try:
            with httpx.Client(
                timeout=runtime_config().market_timeout_seconds, follow_redirects=False
            ) as client:
                return self.fetch(client, spec, interval, config)
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if code == 429:
                raise HTTPException(
                    429,
                    "Market data is rate limited. Try again later.",
                    headers={"Retry-After": "60"},
                ) from None
            if code in (401, 403):
                raise HTTPException(
                    503, "Check the market-data credentials and feed entitlement."
                ) from None
            if code in (400, 404) and self.id == "oanda_v20" and config.oanda.account_id:
                raise HTTPException(
                    503, "Check the OANDA account ID, environment and token."
                ) from None
            raise HTTPException(502, "The market-data provider rejected the request.") from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError, ArithmeticError):
            raise HTTPException(
                502, "The provider data could not be verified. Saved data remains available."
            ) from None
        finally:
            self.lock.release()


class OandaFX(ReadOnlyProvider):
    id = "oanda_v20"

    def fetch(self, client, spec, interval, config):
        token = config.oanda.token.get_secret_value()
        if not token:
            raise HTTPException(503, "Configure the private OANDA data token before refreshing FX.")
        host = (
            "api-fxpractice.oanda.com"
            if config.oanda.environment == "practice"
            else "api-fxtrade.oanda.com"
        )
        if config.oanda.account_id:
            available = request(
                client,
                f"https://{host}/v3/accounts/{config.oanda.account_id}/instruments",
                headers={"Authorization": "Bearer " + token},
                params={"instruments": spec.provider_symbol},
            )
            instruments = available.get("instruments")
            if (
                not isinstance(instruments, list)
                or len(instruments) != 1
                or not isinstance(instruments[0], dict)
                or instruments[0].get("name") != spec.provider_symbol
            ):
                raise HTTPException(
                    503, "This instrument is unavailable for the selected OANDA account."
                )
        payload = request(
            client,
            f"https://{host}/v3/instruments/{spec.provider_symbol}/candles",
            headers={"Authorization": "Bearer " + token},
            params={
                "price": "MBA",
                "granularity": {
                    "1m": "M1",
                    "5m": "M5",
                    "15m": "M15",
                    "1h": "H1",
                    "4h": "H4",
                    "1d": "D",
                }[interval],
                "count": 300,
                "smooth": "false",
                "dailyAlignment": 17,
                "alignmentTimezone": "America/New_York",
                "weeklyAlignment": "Friday",
            },
        )
        received = now()
        if (
            payload["instrument"] != spec.provider_symbol
            or payload["granularity"]
            != {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D"}[interval]
        ):
            raise ValueError("Unexpected instrument/granularity")
        rows = payload["candles"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 300:
            raise ValueError("Unbounded or empty provider candles")
        context = default_context(spec)
        candles = []
        for row in rows:
            opening = stamp(row["time"])
            expected = list(periods(context, interval, opening, opening))
            if (
                not expected
                or type(row["complete"]) is not bool
                or type(row["volume"]) is not int
                or row["volume"] < 0
            ):
                raise ValueError("Invalid FX candle")
            sides = {
                name: {
                    key: row[source][short]
                    for key, short in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))
                }
                for name, source in (("mid", "mid"), ("bid", "bid"), ("ask", "ask"))
            }
            candles.append(
                Candle(
                    open_time=opening,
                    close_time=expected[0][1],
                    **sides["mid"],
                    bid=sides["bid"],
                    ask=sides["ask"],
                    volume=str(row["volume"]),
                    closed=row["complete"],
                ).model_dump(mode="json")
            )
        # OANDA has no candle response server clock; record receipt as the as-of clock explicitly.
        context.warnings.append(
            "FX as-of time is the host receipt clock; the candle response has no server clock."
        )
        return candles, received, received, context.model_dump(mode="json")


class AlpacaStocks(ReadOnlyProvider):
    id = "alpaca_stocks"

    def fetch(self, client, spec, interval, config):
        connection = config.alpaca
        if not connection.key.get_secret_value() or not connection.secret.get_secret_value():
            raise HTTPException(
                503, "Configure the private Alpaca data keys before refreshing stocks."
            )
        headers = {
            "APCA-API-KEY-ID": connection.key.get_secret_value(),
            "APCA-API-SECRET-KEY": connection.secret.get_secret_value(),
        }
        received = now()
        as_of = received - timedelta(minutes=connection.delay_minutes)
        start = as_of - timedelta(days=500 if interval == "1d" else 30)
        host = (
            "paper-api.alpaca.markets"
            if connection.environment == "paper"
            else "api.alpaca.markets"
        )
        sessions = request(
            client,
            f"https://{host}/v2/calendar",
            headers=headers,
            params={
                "start": start.astimezone(NY).date().isoformat(),
                "end": received.astimezone(NY).date().isoformat(),
            },
        )
        if not isinstance(sessions, list) or len(sessions) > 600:
            raise ValueError("Invalid stock calendar")
        calendar = []
        for row in sessions:
            day = datetime.fromisoformat(row["date"]).date()
            calendar.append(
                MarketSession(
                    open=datetime.combine(day, time.fromisoformat(row["open"]), NY),
                    close=datetime.combine(day, time.fromisoformat(row["close"]), NY),
                )
            )
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
                "Split-adjusted retrospective prices; dividends, delisted-universe bias and historical corporate-action availability are not modeled.",
                f"{connection.feed.upper()} share volume describes the selected feed; it is not a liquidity guarantee.",
                f"Data as-of clock is the host request time minus {connection.delay_minutes} minutes.",
            ],
        )
        base_interval = "15m" if interval in ("1h", "4h", "1d") else interval
        params = {
            "symbols": spec.provider_symbol,
            "timeframe": {"1m": "1Min", "5m": "5Min", "15m": "15Min"}[base_interval],
            "start": start.isoformat(),
            "end": as_of.isoformat(),
            "limit": 10000,
            "sort": "desc",
            "adjustment": "split",
            "feed": connection.feed,
            "asof": received.date().isoformat(),
        }
        rows, seen = [], set()
        # The provider may return fewer rows than the requested page limit.
        # Bound both calls and retained observations, rather than requiring the
        # entire 500-day history to fit in four responses.
        truncated = False
        for _ in range(16):
            payload = request(
                client, "https://data.alpaca.markets/v2/stocks/bars", headers=headers, params=params
            )
            batch = payload["bars"].get(spec.provider_symbol, [])
            if (
                not isinstance(batch, list)
                or len(batch) > 10000
                or set(payload["bars"]) - {spec.provider_symbol}
            ):
                raise ValueError("Unbounded or wrong-symbol stock response")
            if len(rows) + len(batch) > 10000:
                batch = batch[: 10000 - len(rows)]
                truncated = True
            rows.extend(batch)
            token = payload.get("next_page_token")
            if not token or len(rows) >= 10000:
                truncated = truncated or bool(token)
                break
            if not isinstance(token, str) or token in seen:
                raise ValueError("Repeated pagination token")
            seen.add(token)
            params["page_token"] = token
        else:
            truncated = True
        if any(
            stamp(right["t"]) >= stamp(left["t"])
            for left, right in zip(rows, rows[1:], strict=False)
        ):
            raise ValueError("Stock response must follow requested descending ordering")
        if truncated:
            context.warnings.append(
                "History is bounded by pagination; the oldest partial aggregate is omitted."
            )
        candles = aggregate_stocks(
            rows, interval, base_interval, context, as_of, truncated=truncated, bounded_start=start
        )
        if not candles:
            raise ValueError("No regular-session stock bars")
        return candles[-300:], as_of, now(), context.model_dump(mode="json")


def aggregate_stocks(
    rows, interval, base_interval, context, as_of, *, truncated=False, bounded_start=None
):
    """Build session-anchored hours/days from 15m bars; never fill absent bars."""
    normalized, seen = {}, set()
    base_grid = {
        op: close
        for op, close in periods(
            context, base_interval, context.coverage_start, context.coverage_end
        )
    }
    for row in rows:
        opening = stamp(row["t"])
        if opening in seen or opening > as_of:
            raise ValueError("Duplicate or future stock candle")
        seen.add(opening)
        closing = base_grid.get(opening)
        if closing is None:
            if session_open(context, opening):
                raise ValueError("Misaligned regular-session stock bar")
            continue  # Extended-session bars are deliberately excluded.
        candle = Candle(
            open_time=opening,
            close_time=closing,
            open=str(row["o"]),
            high=str(row["h"]),
            low=str(row["l"]),
            close=str(row["c"]),
            volume=str(row["v"]),
            closed=closing <= as_of,
        )
        PriceOHLC.model_validate(
            {key: getattr(candle, key) for key in ("open", "high", "low", "close")}
        )
        normalized[opening] = candle.model_dump(mode="json")
    if interval == base_interval:
        return [normalized[op] for op in sorted(normalized)]
    if not normalized:
        return []
    candles = []
    for opening, closing in periods(context, interval, context.coverage_start, max(normalized)):
        constituents = [op for op in base_grid if opening <= op < closing]
        if not constituents or any(op not in normalized for op in constituents):
            if (
                (truncated or bounded_start is not None)
                and opening <= min(normalized) < closing
                and all(
                    op in normalized
                    for op in constituents
                    if op >= (min(normalized) if truncated else bounded_start)
                )
            ):
                # Only an explicitly truncated oldest prefix may be omitted.
                # Missing observations inside the retained window still fail.
                continue
            # A partial current bar is not reported as finalized; gaps in historical
            # constituents reject the aggregate instead of fabricating price/volume.
            if closing <= as_of and any(op in normalized for op in constituents):
                raise ValueError("Missing constituent in a historical stock aggregate")
            continue
        group = [normalized[op] for op in constituents]
        candles.append(
            Candle(
                open_time=opening,
                close_time=closing,
                open=group[0]["open"],
                close=group[-1]["close"],
                high=str(max(Decimal(b["high"]) for b in group)),
                low=str(min(Decimal(b["low"]) for b in group)),
                volume=str(sum(Decimal(b["volume"]) for b in group)),
                closed=closing <= as_of,
            ).model_dump(mode="json")
        )
    return candles
