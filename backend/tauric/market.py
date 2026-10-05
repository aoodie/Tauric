"""Read-only provider registry and immutable, receipt-stamped chart snapshots."""

import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select

from .config import runtime_config
from .data_providers import AlpacaStocks, OandaFX, ReadOnlyProvider, connections
from .data_quality import freshness, verify
from .market_calendars import default_context, session_open
from .market_contracts import Candle, InstrumentView, MarketView
from .models import Instrument, MarketConnectionVersion, MarketSnapshot, identifier, now

INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def instrument(db, instrument_id):
    record = db.get(Instrument, instrument_id)
    if not record:
        raise HTTPException(404, "This instrument is not in the provider registry.")
    return InstrumentView.model_validate(record.definition)


def utc_ms(value):
    if type(value) is not int:
        raise ValueError("Provider timestamp must be integer milliseconds")
    return datetime.fromtimestamp(value / 1000, UTC)


class BinanceSpot:
    id = "binance_spot_public"

    def __init__(self):
        self.lock = threading.Lock()

    def collect(self, spec, interval):
        config = runtime_config()
        endpoint = urlsplit(config.market_url)
        if (
            endpoint.scheme != "https"
            or endpoint.hostname not in {"data-api.binance.vision", "api.binance.com"}
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
        ):
            raise HTTPException(503, "The market provider endpoint is not supported.")
        # Bound simultaneous external requests; no keys, account endpoints or order methods.
        if not self.lock.acquire(blocking=False):
            raise HTTPException(429, "A market refresh is already running. Try again shortly.")
        try:
            with httpx.Client(
                timeout=config.market_timeout_seconds, follow_redirects=False
            ) as client:
                response = client.get(config.market_url.rstrip("/") + "/api/v3/time")
                response.raise_for_status()
                provider_time = utc_ms(response.json()["serverTime"])
                if abs((provider_time - now()).total_seconds()) > 90:
                    raise ValueError("Provider clock differs from host clock")
                response = client.get(
                    config.market_url.rstrip("/") + "/api/v3/klines",
                    params={
                        "symbol": spec.provider_symbol,
                        "interval": interval,
                        "limit": 300,
                        "endTime": int(provider_time.timestamp() * 1000),
                    },
                )
                response.raise_for_status()
                rows = response.json()
            received_at = now()
            if not isinstance(rows, list) or not rows or len(rows) > 300:
                raise ValueError("Provider returned no bounded candle data")
            candles, previous = [], None
            for row in rows:
                if not isinstance(row, list) or len(row) < 7:
                    raise ValueError("Malformed candle")
                start, end = utc_ms(row[0]), utc_ms(row[6])
                if start > provider_time or end <= start or (previous and start <= previous):
                    raise ValueError("Invalid candle ordering or time")
                if abs((end - start).total_seconds() - INTERVAL_SECONDS[interval]) > 0.01:
                    raise ValueError("Candle duration differs from requested interval")
                candle = Candle(
                    open_time=start,
                    close_time=end,
                    open=row[1],
                    high=row[2],
                    low=row[3],
                    close=row[4],
                    volume=row[5],
                    closed=end <= provider_time,
                )
                op, hi, lo, close = (
                    Decimal(getattr(candle, key)) for key in ["open", "high", "low", "close"]
                )
                if lo <= 0 or not lo <= min(op, close) <= max(op, close) <= hi:
                    raise ValueError("Inconsistent OHLC prices")
                candles.append(candle.model_dump(mode="json"))
                previous = start
            if (provider_time - previous).total_seconds() > INTERVAL_SECONDS[interval] * 2:
                raise ValueError("Provider returned an outdated candle series")
            return candles, provider_time, received_at
        except httpx.HTTPStatusError as error:
            if error.response.status_code in {418, 429}:
                raise HTTPException(
                    429,
                    "The market provider is rate-limiting requests. Wait before refreshing.",
                    headers={"Retry-After": "60"},
                ) from None
            raise HTTPException(
                502, "The market provider rejected this request. Saved data remains available."
            ) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
            raise HTTPException(
                502, "The market feed could not be verified. Check connectivity and try again."
            ) from None
        finally:
            self.lock.release()


PROVIDERS = {BinanceSpot.id: BinanceSpot(), OandaFX.id: OandaFX(), AlpacaStocks.id: AlpacaStocks()}


def latest(db, instrument_id, interval):
    return db.scalar(
        select(MarketSnapshot)
        .where(
            MarketSnapshot.instrument_id == instrument_id,
            MarketSnapshot.interval == interval,
            MarketSnapshot.received_at <= now(),
        )
        .order_by(MarketSnapshot.received_at.desc())
        .limit(1)
    )


def view(db, instrument_id, interval):
    spec = instrument(db, instrument_id)
    if interval not in spec.supported_intervals:
        raise HTTPException(422, "This instrument does not support the selected interval.")
    stored = latest(db, instrument_id, interval)
    if not stored:
        return MarketView(
            instrument=spec,
            interval=interval,
            snapshot_id=None,
            candles=[],
            state="empty",
            received_at=None,
            provider_time=None,
            source=spec.provider,
            checksum=None,
        )
    candles = stored.data["candles"]
    context = stored.data.get("data_context") or default_context(spec).model_dump(mode="json")
    quality = stored.data.get("quality") or verify(
        candles, interval, context, stored.provider_time
    ).model_dump(mode="json")
    gap_count = quality["missing_bars"]
    fresh = (
        timedelta(0)
        <= now() - stored.received_at
        <= timedelta(seconds=runtime_config().market_refresh_seconds * 2)
    )
    return MarketView(
        instrument=spec,
        interval=interval,
        snapshot_id=stored.id,
        candles=candles,
        state="fresh" if fresh and quality["status"] != "outdated" else "stale",
        received_at=stored.received_at,
        provider_time=stored.provider_time,
        source=spec.provider,
        checksum=stored.checksum,
        gaps=gap_count,
        data_context=context,
        quality=quality,
        session_state={True: "open", False: "closed", None: "unknown"}[
            session_open(context, now())
        ],
    )


def refresh(db, instrument_id, interval, *, commit=True):
    spec = instrument(db, instrument_id)
    if interval not in spec.supported_intervals:
        raise HTTPException(422, "This instrument does not support the selected interval.")
    # Serialize per instrument/timeframe across API processes and avoid duplicate refreshes.
    key = int.from_bytes(
        hashlib.sha256(f"{instrument_id}/{interval}".encode()).digest()[:8], "big", signed=True
    )
    if not db.scalar(select(func.pg_try_advisory_xact_lock(key))):
        raise HTTPException(429, "This instrument is already refreshing. Try again shortly.")
    existing = latest(db, instrument_id, interval)
    connection_provider = {"oanda_v20": "oanda", "alpaca_stocks": "alpaca"}.get(spec.provider)
    connection_changed = (
        existing
        and connection_provider
        and db.scalar(
            select(MarketConnectionVersion.id)
            .where(
                MarketConnectionVersion.provider == connection_provider,
                MarketConnectionVersion.created_at > existing.received_at,
            )
            .limit(1)
        )
    )
    if (
        existing
        and not connection_changed
        and 0
        <= (now() - existing.received_at).total_seconds()
        < runtime_config().market_refresh_seconds
    ):
        return view(db, instrument_id, interval)
    adapter = PROVIDERS.get(spec.provider)
    if not adapter:
        raise HTTPException(422, "This data provider is not implemented.")
    collected = (
        adapter.collect(spec, interval, config=connections(db))
        if isinstance(adapter, ReadOnlyProvider)
        else adapter.collect(spec, interval)
    )
    candles, provider_time, received_at = collected[:3]
    context = collected[3] if len(collected) == 4 else default_context(spec).model_dump(mode="json")
    try:
        quality = verify(candles, interval, context, provider_time)
        if not freshness(candles, interval, context, provider_time):
            quality.status = "outdated"
            quality.warnings.append("The provider omitted recent expected trading bars.")
    except (ValueError, ArithmeticError, TypeError):
        raise HTTPException(502, "Provider candles failed the asset data-quality checks.") from None
    payload = {
        "candles": candles,
        "source": spec.provider,
        "symbol": spec.provider_symbol,
        "volume_type": spec.volume_type,
        "availability": "observed_at_receipt",
        "data_context": context,
        "quality": quality.model_dump(mode="json"),
    }
    checksum = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    db.add(
        MarketSnapshot(
            id=identifier(),
            instrument_id=spec.id,
            interval=interval,
            received_at=received_at,
            provider_time=provider_time,
            checksum=checksum,
            data=payload,
        )
    )
    db.flush()
    if commit:
        db.commit()
    return view(db, instrument_id, interval)
