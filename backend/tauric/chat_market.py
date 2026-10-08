"""Fresh, bounded market observations for owner chat; no research or order authority."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from pydantic import Field, field_validator
from sqlalchemy import select

from . import chat, chat_research, market, services
from .data_providers import stamp
from .instrument_discovery import endpoint, request_json
from .market_connections import stored_connection
from .market_contracts import Candle, Interval
from .models import Instrument, now
from .portfolio_contracts import ChatIdentity


class MarketObservationRequest(ChatIdentity):
    symbol: str = Field(min_length=1, max_length=120)
    intervals: list[Interval] = Field(
        default_factory=lambda: ["15m", "1h"], min_length=1, max_length=2
    )

    @field_validator("intervals")
    @classmethod
    def unique_intervals(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Choose distinct chart intervals")
        return values


def normalize(symbol):
    return symbol.strip().upper().replace("/", "").replace("_", "").replace("-", "")


def gold(interval, connection):
    if interval == "1d":
        raise HTTPException(
            422,
            "Gold observations currently support intraday bars; daily gold research is unavailable.",
        )
    url, headers = endpoint("oanda", connection)
    catalog = request_json(url, headers, {"instruments": "XAU_USD"})
    rows = catalog.get("instruments") if isinstance(catalog, dict) else None
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or not isinstance(rows[0], dict)
        or rows[0].get("name") != "XAU_USD"
        or rows[0].get("type") != "METAL"
    ):
        raise HTTPException(503, "XAU/USD is unavailable for the saved OANDA account.")
    host = (
        "api-fxpractice.oanda.com"
        if connection.environment == "practice"
        else "api-fxtrade.oanda.com"
    )
    granularity = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4"}[interval]
    payload = request_json(
        f"https://{host}/v3/instruments/XAU_USD/candles",
        headers,
        {"price": "MBA", "granularity": granularity, "count": 61, "smooth": "false"},
    )
    received = now()
    try:
        if payload["instrument"] != "XAU_USD" or payload["granularity"] != granularity:
            raise ValueError("Unexpected instrument or interval")
        candles, previous = [], None
        rows = payload["candles"]
        if not isinstance(rows, list) or not 1 <= len(rows) <= 61:
            raise ValueError("Unbounded candle response")
        for row in rows:
            opening = stamp(row["time"])
            ending = opening + timedelta(seconds=market.INTERVAL_SECONDS[interval])
            if (
                type(row["complete"]) is not bool
                or type(row["volume"]) is not int
                or row["volume"] < 0
                or opening > received + timedelta(seconds=90)
                or (previous and opening <= previous)
            ):
                raise ValueError("Invalid candle ordering")
            if row["complete"] and ending > received + timedelta(seconds=90):
                raise ValueError("Future closed candle")
            sides = {
                name: {
                    key: row[name][short]
                    for key, short in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))
                }
                for name in ("mid", "bid", "ask")
            }
            for side in sides.values():
                prices = [Decimal(side[k]) for k in ("open", "high", "low", "close")]
                if (
                    any(not p.is_finite() or p <= 0 for p in prices)
                    or not prices[2]
                    <= min(prices[0], prices[3])
                    <= max(prices[0], prices[3])
                    <= prices[1]
                ):
                    raise ValueError("Invalid OHLC")
            if any(Decimal(sides["bid"][k]) > Decimal(sides["ask"][k]) for k in sides["bid"]):
                raise ValueError("Crossed bid/ask bars")
            candles.append(
                Candle(
                    open_time=opening,
                    close_time=ending,
                    **sides["mid"],
                    bid=sides["bid"],
                    ask=sides["ask"],
                    volume=str(row["volume"]),
                    closed=row["complete"],
                ).model_dump(mode="json")
            )
            previous = opening
    except (KeyError, ValueError, TypeError, ArithmeticError):
        raise HTTPException(502, "The gold candle response could not be verified.") from None
    closed = [c for c in candles if c["closed"]][-60:]
    if not closed:
        raise HTTPException(503, "The provider supplied no completed gold bars.")
    latest_age = (received - stamp(candles[-1]["open_time"])).total_seconds()
    return {
        "symbol": "XAU/USD",
        "asset_type": "metal",
        "source": "OANDA v20 account-verified gold candles",
        "environment": connection.environment,
        "interval": interval,
        "received_at": received.isoformat(),
        "provider_time": None,
        "state": "received" if latest_age <= market.INTERVAL_SECONDS[interval] * 2 else "outdated",
        "candles": closed,
        "forming_candle": next((c for c in reversed(candles) if not c["closed"]), None),
        "checksum": hashlib.sha256(json.dumps(candles, sort_keys=True).encode()).hexdigest(),
        "native_research_supported": False,
        "execution_eligible": False,
        "warnings": [
            "Receipt time is the host clock; OANDA supplies no candle-response server clock.",
            "Gold session/holiday coverage and missing-bar completeness are not certified. These observations cannot qualify a strategy or authorize execution.",
            "Midpoint reference prices and separate bid/ask bars; volume is provider tick activity.",
        ],
    }


def observations(db, owner, body):
    config, _ = chat.load_config(db)
    chat_research.authorize(db, owner, body, config)
    key = normalize(body.symbol)
    if key == "XAUUSD":
        connection, _, _ = stored_connection(db, "oanda")
        results = [gold(interval, connection) for interval in body.intervals]
    else:
        exact = db.get(Instrument, body.symbol)
        records = (
            [exact]
            if exact
            else list(db.scalars(select(Instrument).order_by(Instrument.id).limit(5000)))
        )
        matches = [
            r
            for r in records
            if r.id == body.symbol
            or normalize(r.definition["symbol"]) == key
            or normalize(r.definition["provider_symbol"]) == key
        ]
        if len(matches) != 1:
            raise HTTPException(
                422,
                "Choose one registered instrument using its exact ID or symbol. Use Find more symbols for an unregistered instrument.",
            )
        results = []
        for interval in body.intervals:
            feed = market.refresh(db, matches[0].id, interval, commit=False)
            value = feed.model_dump(mode="json")
            value["candles"] = [c for c in value["candles"] if c["closed"]][-60:]
            value.update(
                symbol=feed.instrument.symbol,
                native_research_supported=feed.instrument.native_research_ready,
            )
            results.append(value)
    # Data collection does not extend a revoked chat lease.
    chat_research.authorize(db, owner, body, config)
    services.audit(
        db,
        owner,
        "chat.market_observed",
        "chat",
        body.tool_call_id,
        "OpenClaw read fresh market observations through Tauric",
        {
            "symbol": body.symbol,
            "intervals": body.intervals,
            "observations": [
                {
                    "interval": result["interval"],
                    "received_at": result.get("received_at"),
                    "checksum": result.get("checksum"),
                }
                for result in results
            ],
            "native_report_created": False,
            "orders_submitted": False,
        },
    )
    db.commit()
    return {
        "authority": "market_observation_only",
        "observations": results,
        "native_report_created": False,
        "orders_submitted": False,
        "instruction": "Use the received timestamps, closed bars and warnings. Distinguish your chart interpretation from a native Tauric report. Outdated observations cannot establish current entry levels.",
    }
