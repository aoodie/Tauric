"""Bounded provider discovery and additive, owner-selected instrument registration."""

import hashlib
import json
import re
import threading
import time
from typing import Literal

import httpx
from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func, select

from . import services
from .config import runtime_config
from .market_connections import stored_connection
from .market_contracts import InstrumentView
from .models import Instrument, now
from .schemas import Contract

Provider = Literal["oanda", "alpaca"]
MAX_BYTES = 20_000_000
MAX_ROWS = 50_000
TTL = 300
LOCKS = {key: threading.Lock() for key in ("oanda", "alpaca")}
CACHE = {}


class DiscoveredInstrument(Contract):
    symbol: str
    name: str
    exchange: str
    category: Literal["fx", "us_equity"]
    available: bool
    unavailable_reason: str | None = None
    registered_id: str | None = None
    registered_asset_type: Literal["fx", "equity", "etf"] | None = None

    @field_validator("name")
    @classmethod
    def meaningful_text(cls, value):
        if not value.strip():
            raise ValueError("Instrument name must not be blank")
        return value.strip()


class DiscoveryView(Contract):
    provider: Provider
    connection_revision: int
    checked_at: str
    source: str
    total: int
    offset: int
    has_more: bool
    items: list[DiscoveredInstrument]
    limitations: list[str]


class InstrumentAdd(Contract):
    provider: Provider
    symbol: str = Field(min_length=1, max_length=30, pattern=r"^[A-Z][A-Z0-9_.-]*$")
    asset_type: Literal["fx", "equity", "etf"]
    expected_connection_revision: int = Field(ge=0, strict=True)


def endpoint(provider, config):
    if provider == "oanda":
        if not config.token.get_secret_value() or not config.account_id:
            raise HTTPException(503, "Save an OANDA token and account ID to discover Forex pairs.")
        host = (
            "api-fxpractice.oanda.com"
            if config.environment == "practice"
            else "api-fxtrade.oanda.com"
        )
        return (
            f"https://{host}/v3/accounts/{config.account_id}/instruments",
            {"Authorization": "Bearer " + config.token.get_secret_value()},
        )
    if not config.key.get_secret_value() or not config.secret.get_secret_value():
        raise HTTPException(
            503, "Save the Alpaca market-data connection to discover stocks and ETFs."
        )
    host = "paper-api.alpaca.markets" if config.environment == "paper" else "api.alpaca.markets"
    return (
        f"https://{host}/v2/assets",
        {
            "APCA-API-KEY-ID": config.key.get_secret_value(),
            "APCA-API-SECRET-KEY": config.secret.get_secret_value(),
        },
    )


def request_json(url, headers, params):
    # Fixed provider hosts, no redirects, and a cap on decompressed response bytes.
    try:
        with httpx.Client(
            timeout=runtime_config().market_timeout_seconds, follow_redirects=False
        ) as client:
            with client.stream("GET", url, headers=headers, params=params) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("Unbounded provider catalog")
                    chunks.append(chunk)
                return json.loads(b"".join(chunks))
    except httpx.HTTPStatusError as error:
        if error.response.status_code == 429:
            raise HTTPException(
                429,
                "Instrument discovery is rate limited. Try again later.",
                headers={"Retry-After": "60"},
            ) from None
        if error.response.status_code in (401, 403):
            raise HTTPException(
                503, "Check the saved provider credentials and account access."
            ) from None
        raise HTTPException(502, "The provider could not verify this instrument catalog.") from None
    except (httpx.HTTPError, ValueError, TypeError):
        raise HTTPException(
            502, "The provider catalog could not be read safely. Try again later."
        ) from None


def normalize(provider, payload):
    rows = (
        payload.get("instruments") if provider == "oanda" and isinstance(payload, dict) else payload
    )
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise HTTPException(502, "The provider returned an unsupported instrument catalog.")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(502, "The provider returned an invalid instrument catalog.")
        if provider == "oanda":
            if row.get("type") != "CURRENCY":
                continue
            symbol = row.get("name")
            valid = isinstance(symbol, str) and re.fullmatch(r"[A-Z]{3}_[A-Z]{3}", symbol)
            name = row.get("displayName", symbol)
            available = bool(valid and symbol[:3] != symbol[4:])
            reason = None if available else "This currency-pair format is not supported."
            exchange, category = "OANDA", "fx"
        else:
            if row.get("class") != "us_equity":
                continue
            symbol = row.get("symbol")
            valid = isinstance(symbol, str) and re.fullmatch(r"[A-Z][A-Z0-9.-]{0,14}", symbol)
            name = row.get("name", symbol)
            exchange, category = row.get("exchange", ""), "us_equity"
            available = bool(
                valid
                and row.get("status") == "active"
                and row.get("tradable") is True
                and exchange in {"AMEX", "ARCA", "BATS", "NYSE", "NASDAQ", "NYSEARCA"}
            )
            reason = (
                None
                if available
                else "Only active, tradable US exchange listings are supported; OTC and unsupported symbol formats are excluded."
            )
        if (
            not isinstance(symbol, str)
            or len(symbol) > 30
            or not isinstance(name, str)
            or not 1 <= len(name.strip()) <= 500
            or not isinstance(exchange, str)
            or len(exchange) > 40
        ):
            raise HTTPException(502, "The provider returned invalid instrument identifiers.")
        if symbol in seen:
            raise HTTPException(502, "The provider returned duplicate instrument identifiers.")
        seen.add(symbol)
        result.append(
            DiscoveredInstrument(
                symbol=symbol,
                name=name,
                exchange=exchange,
                category=category,
                available=available,
                unavailable_reason=reason,
            )
        )
    return sorted(result, key=lambda row: row.symbol)


def fetch(provider, config, symbol=None):
    url, headers = endpoint(provider, config)
    params = {"instruments": symbol} if provider == "oanda" and symbol else {}
    if provider == "alpaca":
        if symbol:
            url += "/" + symbol
        else:
            params = {"status": "active", "asset_class": "us_equity"}
    payload = request_json(url, headers, params)
    if provider == "alpaca" and symbol:
        payload = [payload]
    return normalize(provider, payload)


def catalog(provider, config, revision):
    url, headers = endpoint(provider, config)
    fingerprint = hashlib.sha256(
        json.dumps([url, headers, revision], sort_keys=True).encode()
    ).hexdigest()
    if not LOCKS[provider].acquire(blocking=False):
        raise HTTPException(429, "Instrument discovery is already running. Try again shortly.")
    try:
        cached = CACHE.get(provider)
        if cached and cached[0] == fingerprint and time.monotonic() - cached[1] < TTL:
            return cached[2], cached[3]
        items = fetch(provider, config)
        checked_at = now().isoformat()
        CACHE[provider] = (fingerprint, time.monotonic(), items, checked_at)
        return items, checked_at
    finally:
        LOCKS[provider].release()


def discover(db, provider, query="", offset=0, limit=50):
    config, revision, _ = stored_connection(db, provider)
    items, checked_at = catalog(provider, config, revision)
    needle = query.strip().casefold().replace("/", "_")
    matching = [
        item for item in items if needle in item.symbol.casefold() or needle in item.name.casefold()
    ]
    matching.sort(
        key=lambda item: (
            item.symbol.casefold() != needle,
            not item.symbol.casefold().startswith(needle),
            item.symbol,
        )
    )
    registered = {}
    for record in db.scalars(select(Instrument)):
        spec = InstrumentView.model_validate(record.definition)
        if spec.provider == ("oanda_v20" if provider == "oanda" else "alpaca_stocks"):
            registered[spec.provider_symbol] = spec
    page = []
    for item in matching[offset : offset + limit]:
        spec = registered.get(item.symbol)
        page.append(
            item.model_copy(
                update={
                    "registered_id": spec.id if spec else None,
                    "registered_asset_type": spec.asset_type if spec else None,
                }
            )
        )
    return DiscoveryView(
        provider=provider,
        connection_revision=revision,
        checked_at=checked_at,
        source="OANDA account instruments" if provider == "oanda" else "Alpaca US assets",
        total=len(matching),
        offset=offset,
        has_more=offset + limit < len(matching),
        items=page,
        limitations=[
            "Provider availability is a catalog observation; prices, historical coverage and research data are checked separately.",
            "The catalog is reused for up to five minutes; adding a symbol rechecks its provider availability.",
            "Daily Forex AI research supports all available currency pairs. Recent closed OANDA prices are required; missing policy and economic sources remain explicit."
            if provider == "oanda"
            else "Choose Stock or ETF when adding a listing. The provider catalog does not establish this classification; ETF research omits company fundamentals.",
        ],
    )


def register(db, owner, body):
    # Serialize with saves/checks of this connection as well as other additions.
    if not db.scalar(
        select(func.pg_try_advisory_xact_lock(913570032 if body.provider == "oanda" else 913570033))
    ):
        raise HTTPException(409, "This connection is busy. Try adding the symbol again shortly.")
    config, revision, _ = stored_connection(db, body.provider)
    if revision != body.expected_connection_revision:
        raise HTTPException(
            409, "The provider connection changed. Search again before adding a symbol."
        )
    if (body.provider == "oanda") != (body.asset_type == "fx"):
        raise HTTPException(422, "Choose Forex for OANDA or Stock/ETF for Alpaca.")
    rows = fetch(body.provider, config, body.symbol)
    if len(rows) != 1 or rows[0].symbol != body.symbol or not rows[0].available:
        raise HTTPException(
            422, "This symbol is unavailable or unsupported for the selected provider."
        )
    existing = [
        InstrumentView.model_validate(row.definition) for row in db.scalars(select(Instrument))
    ]
    for spec in existing:
        if (
            spec.provider == ("oanda_v20" if body.provider == "oanda" else "alpaca_stocks")
            and spec.provider_symbol == body.symbol
        ):
            if spec.asset_type != body.asset_type:
                raise HTTPException(
                    409, "This symbol is already saved with a different asset classification."
                )
            return spec
    row = rows[0]
    is_fx = body.provider == "oanda"
    base, quote = body.symbol.split("_") if is_fx else (body.symbol, "USD")
    spec = InstrumentView(
        id=f"{body.provider}:{body.asset_type}:{body.symbol}",
        symbol=body.symbol.replace("_", "/") if is_fx else body.symbol,
        name=row.name,
        provider="oanda_v20" if is_fx else "alpaca_stocks",
        provider_symbol=body.symbol,
        asset_type=body.asset_type,
        base_currency=base,
        quote_currency=quote,
        volume_type="provider_ticks" if is_fx else "feed_shares",
        native_symbol=base + quote + "=X" if is_fx else body.symbol.replace(".", "-"),
        native_asset_type="forex" if is_fx else "stock",
        analysis_scope="",
        calendar_id="oanda_fx" if is_fx else "us_equity",
        price_side="bid_ask" if is_fx else "trades",
    )
    db.add(
        Instrument(
            id=spec.id, definition=spec.model_dump(mode="json", exclude={"native_research_ready"})
        )
    )
    services.audit(
        db,
        owner,
        "instrument.registered",
        "instrument",
        spec.id,
        "Added provider-verified research instrument",
        {
            "provider": body.provider,
            "symbol": body.symbol,
            "asset_type": body.asset_type,
            "classification_source": "provider_currency" if is_fx else "owner_selection",
            "connection_revision": revision,
            "checked_at": now().isoformat(),
        },
    )
    db.commit()
    return spec
