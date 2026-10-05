"""Provider-wide browsing, separate from research registration and scanner universes."""

import re
from datetime import timedelta
from itertools import zip_longest

from fastapi import HTTPException
from sqlalchemy import func, select

from . import instrument_discovery, services
from .intelligence_contracts import IntelligenceCatalog, IntelligenceSymbol
from .intelligence_models import IntelligenceReceipt
from .intelligence_sources import digest
from .market_connections import stored_connection
from .market_contracts import InstrumentView
from .models import Instrument, now

CATALOGS = {
    "fx": ("oanda", "OANDA Forex pairs", "https://api-fxpractice.oanda.com"),
    "stocks": ("alpaca", "Alpaca US listings", "https://paper-api.alpaca.markets/v2/assets"),
    "crypto": (
        "binance",
        "Binance USDT spot pairs",
        "https://data-api.binance.vision/api/v3/exchangeInfo",
    ),
}


def latest(db, owner, key):
    row = db.scalar(
        select(IntelligenceReceipt)
        .where(
            IntelligenceReceipt.owner_id == owner.id,
            IntelligenceReceipt.source_key == key,
            IntelligenceReceipt.created_at <= now(),
        )
        .order_by(IntelligenceReceipt.created_at.desc(), IntelligenceReceipt.id.desc())
        .limit(1)
    )
    if row and digest(row.payload) != row.checksum:
        raise HTTPException(409, "Saved market catalog evidence failed its integrity check.")
    return row


def store(db, owner, key, payload):
    row = IntelligenceReceipt(
        owner_id=owner.id, source_key=key, payload=payload, checksum=digest(payload)
    )
    db.add(row)
    db.flush()
    return row


def fetch(family, db):
    provider, _, endpoint = CATALOGS[family]
    if provider != "binance":
        config, revision, _ = stored_connection(db, provider)
        items, _ = instrument_discovery.catalog(provider, config, revision)
        return [item.model_dump(mode="json") for item in items if item.available]
    payload = instrument_discovery.request_json(endpoint, {}, {"permissions": "SPOT"})
    rows = payload.get("symbols")
    if not isinstance(rows, list) or len(rows) > 50000:
        raise HTTPException(502, "The crypto catalog could not be verified.")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(502, "The crypto catalog could not be verified.")
        if (
            row.get("status") != "TRADING"
            or row.get("quoteAsset") != "USDT"
            or row.get("isSpotTradingAllowed") is not True
        ):
            continue
        base, symbol = row.get("baseAsset"), row.get("symbol")
        if (
            not isinstance(base, str)
            or not re.fullmatch(r"[A-Z0-9]{1,30}", base)
            or symbol != base + "USDT"
            or symbol in seen
        ):
            continue
        seen.add(symbol)
        result.append(
            {
                "symbol": symbol,
                "name": base + " / USDT",
                "base_currency": base,
                "quote_currency": "USDT",
            }
        )
    return sorted(result, key=lambda item: item["symbol"])


def synchronize(db, owner):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(913570081))):
        raise HTTPException(429, "A market catalog check is already running. Try again shortly.")
    for family, (_, label, endpoint) in CATALOGS.items():
        key = "catalog:" + family
        previous = latest(db, owner, key)
        if previous and now() - previous.created_at < timedelta(minutes=5):
            continue
        try:
            items = fetch(family, db)
            status, warning = "available", None
        except HTTPException as error:
            # Keep the last verified catalog available for browsing during an outage.
            items = previous.payload["items"] if previous else []
            status = "stale" if items else "unavailable"
            warning = str(error.detail)
        store(
            db,
            owner,
            key,
            {
                "family": family,
                "label": label,
                "url": endpoint,
                "status": status,
                "checked_at": now().isoformat(),
                "warning": warning,
                "items": items,
                "verified_at": now().isoformat()
                if status == "available"
                else (previous.payload.get("verified_at") if previous else None),
            },
        )
    services.audit(
        db,
        owner,
        "intelligence.catalogs_checked",
        "intelligence",
        owner.id,
        "Checked provider market catalogs",
        {},
    )
    db.commit()
    return universe(db, owner)[1]


def universe(db, owner):
    registered = [
        InstrumentView.model_validate(row.definition) for row in db.scalars(select(Instrument))
    ]
    existing = {(s.provider, s.provider_symbol): s for s in registered}
    by_id, groups, statuses = {}, {key: [] for key in CATALOGS}, []
    for family, (provider, label, _) in CATALOGS.items():
        row = latest(db, owner, "catalog:" + family)
        status = row.payload["status"] if row else "missing"
        if row and status == "available" and now() - row.created_at > timedelta(hours=24):
            status = "stale"
        items = row.payload["items"] if row else []
        statuses.append(
            IntelligenceCatalog(
                market=family,
                label=label,
                total=len(items),
                status=status,
                checked_at=row.payload["checked_at"] if row else None,
                warning=row.payload.get("warning")
                if row
                else "The provider catalog has not been checked yet.",
            )
        )
        for item in items:
            source = {
                "oanda": "oanda_v20",
                "alpaca": "alpaca_stocks",
                "binance": "binance_spot_public",
            }[provider]
            saved = existing.get((source, item["symbol"]))
            if saved:
                spec = IntelligenceSymbol.model_validate(
                    saved.model_dump(exclude={"native_research_ready"})
                )
            else:
                fx, crypto = family == "fx", family == "crypto"
                base, quote = (
                    item["symbol"].split("_")
                    if fx
                    else (
                        item.get("base_currency", item["symbol"]),
                        item.get("quote_currency", "USD"),
                    )
                )
                spec = IntelligenceSymbol(
                    id=f"catalog:{provider}:{item['symbol']}",
                    symbol=item["symbol"].replace("_", "/")
                    if fx
                    else (base + "/" + quote if crypto else item["symbol"]),
                    name=item["name"],
                    provider=source,
                    provider_symbol=item["symbol"],
                    asset_type="fx" if fx else "crypto_spot" if crypto else "us_listing",
                    base_currency=base,
                    quote_currency=quote,
                    volume_type="provider_ticks"
                    if fx
                    else "exchange_base"
                    if crypto
                    else "feed_shares",
                    native_symbol="",
                    native_asset_type="forex" if fx else "crypto" if crypto else "stock",
                    analysis_scope="Market browsing only; add this instrument to the research workspace before using research services.",
                    calendar_id="oanda_fx" if fx else "crypto_24x7" if crypto else "us_equity",
                    price_side="bid_ask" if fx else "trades",
                    registered=False,
                )
            by_id[spec.id] = spec
            spec.catalog_id = f"catalog:{provider}:{item['symbol']}"
            groups[family].append(spec)
    for saved in registered:
        if saved.id not in by_id:
            spec = IntelligenceSymbol.model_validate(
                saved.model_dump(exclude={"native_research_ready"})
            )
            family = (
                "fx"
                if spec.asset_type == "fx"
                else "crypto"
                if spec.asset_type == "crypto_spot"
                else "stocks"
            )
            groups[family].append(spec)
            by_id[spec.id] = spec
    for group in groups.values():
        group.sort(key=lambda s: (not s.registered, s.symbol))
    # A balanced overview exposes each connected market instead of a page of alphabetically first stocks.
    ordered = [s for batch in zip_longest(*groups.values()) for s in batch if s is not None]
    return ordered, statuses


def resolve(db, owner, keys):
    known = {s.id: s for s in universe(db, owner)[0]}
    known.update({s.catalog_id: s for s in list(known.values()) if s.catalog_id})
    if any(key not in known for key in keys):
        raise HTTPException(
            422, "This symbol is not in the saved provider catalogs. Reload markets and try again."
        )
    return [known[key] for key in dict.fromkeys(keys)]


def register(db, owner, body):
    spec = resolve(db, owner, [body.instrument_id])[0]
    if spec.registered:
        if spec.asset_type != body.asset_type:
            raise HTTPException(
                409, "This symbol is already registered with a different classification."
            )
        return InstrumentView.model_validate(
            spec.model_dump(exclude={"registered", "catalog_id", "native_research_ready"})
        )
    if (
        spec.asset_type == "us_listing"
        and body.asset_type not in {"equity", "etf"}
        or spec.asset_type != "us_listing"
        and body.asset_type != spec.asset_type
    ):
        raise HTTPException(422, "Choose a classification that matches this market listing.")
    if spec.provider != "binance_spot_public":
        provider = "oanda" if spec.asset_type == "fx" else "alpaca"
        _, revision, _ = stored_connection(db, provider)
        return instrument_discovery.register(
            db,
            owner,
            instrument_discovery.InstrumentAdd(
                provider=provider,
                symbol=spec.provider_symbol,
                asset_type=body.asset_type,
                expected_connection_revision=revision,
            ),
        )
    if not db.scalar(select(func.pg_try_advisory_xact_lock(913570082))):
        raise HTTPException(409, "A crypto instrument is being registered. Try again shortly.")
    if spec.provider_symbol not in {item["symbol"] for item in fetch("crypto", db)}:
        raise HTTPException(422, "This crypto spot pair is no longer available.")
    key = "binance:spot:" + spec.provider_symbol
    current = db.get(Instrument, key)
    if current:
        return InstrumentView.model_validate(current.definition)
    saved = InstrumentView.model_validate(
        {
            **spec.model_dump(exclude={"registered", "catalog_id", "native_research_ready"}),
            "id": key,
            "native_symbol": spec.base_currency + "USD",
            "asset_type": "crypto_spot",
        }
    )
    db.add(
        Instrument(
            id=key, definition=saved.model_dump(mode="json", exclude={"native_research_ready"})
        )
    )
    db.flush()
    services.audit(
        db,
        owner,
        "instrument.registered",
        "instrument",
        key,
        "Added provider-verified crypto spot instrument",
        {"provider_symbol": spec.provider_symbol},
    )
    db.commit()
    return saved
