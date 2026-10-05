"""Shared market views from immutable observations; no models, scans or orders."""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select

from . import (
    analysis,
    candidate_pipeline,
    forex_macro,
    intelligence_catalog,
    intelligence_prices,
    market,
    scanner,
    services,
)
from .intelligence_contracts import (
    IntelligenceDashboard,
    IntelligenceItem,
    IntelligenceMove,
    IntelligenceRefreshResult,
    IntelligenceSource,
    IntelligenceSymbol,
    IntelligenceWatchlistView,
)
from .intelligence_models import IntelligenceReceipt, IntelligenceWatchlist
from .intelligence_sources import alpaca_news, digest
from .market_contracts import Candle, DataQuality
from .models import AnalysisJob, Instrument, ScannerProfileVersion, now

FAMILIES = {
    "overview": {"fx", "equity", "etf", "us_listing", "crypto_spot"},
    "fx": {"fx"},
    "stocks": {"equity", "etf", "us_listing"},
    "crypto": {"crypto_spot"},
    "commodities": set(),
    "rates": set(),
}


def checked(row):
    if digest(row.payload) != row.checksum:
        raise HTTPException(409, "Saved intelligence failed its integrity check.")
    return row


def watchlist(db, owner):
    receipt = intelligence_catalog.latest(db, owner, "market_watchlist")
    if receipt:
        saved = IntelligenceWatchlistView.model_validate(receipt.payload["watchlist"])
        aliases = {
            s.catalog_id: s.id for s in intelligence_catalog.universe(db, owner)[0] if s.catalog_id
        }
        saved.instrument_ids = list(
            dict.fromkeys(aliases.get(key, key) for key in saved.instrument_ids)
        )
        return saved
    row = db.scalar(
        select(IntelligenceWatchlist)
        .where(IntelligenceWatchlist.owner_id == owner.id)
        .order_by(IntelligenceWatchlist.revision.desc())
        .limit(1)
    )
    return (
        IntelligenceWatchlistView(revision=row.revision, instrument_ids=row.instrument_ids)
        if row
        else IntelligenceWatchlistView()
    )


def save_watchlist(db, owner, body):
    key = int.from_bytes(
        hashlib.sha256((owner.id + "/watchlist").encode()).digest()[:8], "big", signed=True
    )
    if not db.scalar(select(func.pg_try_advisory_xact_lock(key))):
        raise HTTPException(409, "The watchlist is being saved. Try again.")
    current = watchlist(db, owner)
    ids = list(dict.fromkeys(body.instrument_ids))
    ids = [s.id for s in intelligence_catalog.resolve(db, owner, ids)]
    if ids == current.instrument_ids:
        return current
    if current.revision != body.expected_revision:
        raise HTTPException(409, "Your watchlist changed in another tab. Reload and try again.")
    saved = IntelligenceWatchlistView(revision=current.revision + 1, instrument_ids=ids)
    row = intelligence_catalog.store(
        db, owner, "market_watchlist", {"watchlist": saved.model_dump(mode="json")}
    )
    services.audit(
        db,
        owner,
        "intelligence.watchlist_saved",
        "intelligence_watchlist",
        row.id,
        "Owner updated market watchlist",
        {"revision": saved.revision, "instrument_ids": ids},
    )
    db.commit()
    return watchlist(db, owner)


def move(db, spec, owner=None):
    if not isinstance(spec, IntelligenceSymbol):
        spec = IntelligenceSymbol.model_validate(spec.model_dump(exclude={"native_research_ready"}))
    stored = market.latest(db, spec.id, "1d") if spec.registered else None
    browse = (
        intelligence_catalog.latest(db, owner, intelligence_prices.key(spec))
        if owner and not stored
        else None
    )

    result = IntelligenceMove(
        instrument=spec,
        state="empty",
        source=spec.provider,
        volume_type=spec.volume_type,
        changes={"1d": None, "5d": None, "1m": None},
    )
    if browse:
        payload = browse.payload
        result.price_receipt_id = browse.id
        if payload["status"] != "available":
            result.state, result.warning = "unavailable", payload["warning"]
            return result
        stored = SimpleNamespace(
            id=None,
            provider_time=datetime.fromisoformat(payload["provider_time"]),
            received_at=datetime.fromisoformat(payload["received_at"]),
            data=payload,
            checksum=None,
        )
        feed = SimpleNamespace(
            candles=[Candle.model_validate(b) for b in payload["candles"]],
            data_context=payload["data_context"],
            quality=DataQuality.model_validate(payload["quality"]),
            gaps=payload["quality"]["missing_bars"],
            daily_aggregate=payload.get("daily_aggregate", False),
        )
    if not stored:
        result.warning = "Refresh this view to collect daily prices."
        return result
    if max(stored.received_at, stored.provider_time) > now() + timedelta(seconds=90):
        result.state, result.warning = (
            "unverified",
            "Saved price evidence has a future timestamp. Refresh prices.",
        )
        return result
    if not browse and (
        hashlib.sha256(json.dumps(stored.data, sort_keys=True).encode()).hexdigest()
        != stored.checksum
    ):
        result.state, result.warning = (
            "unverified",
            "Saved price evidence failed its integrity check. Refresh prices.",
        )
        return result
    if not browse:
        feed = market.view(db, spec.id, "1d")
    bars = [bar for bar in feed.candles if bar.closed and bar.close_time <= stored.provider_time]
    result.snapshot_id, result.received_at = stored.id, stored.received_at
    if not bars:
        result.warning = "No completed daily bars are available yet."
        return result
    result.price, result.observed_at = bars[-1].close, bars[-1].close_time
    result.sparkline = [b.close for b in bars[-22:]]
    result.daily_bars = [b.model_dump(mode="json") for b in bars[-65:]]
    result.state = "saved"
    # Historical gaps outside the displayed comparison window cannot change its returns.
    # Verify the recent bars themselves; never bridge an unknown closure within that window.
    window = bars[-22:]
    try:
        recent_quality = intelligence_prices.validate(
            [b.model_dump(mode="json") for b in window],
            feed.data_context,
            stored.provider_time,
            getattr(feed, "daily_aggregate", False),
        )
    except (ValueError, ArithmeticError, TypeError):
        recent_quality = None
    if (
        not recent_quality
        or recent_quality.missing_bars
        or (feed.quality and feed.quality.status == "outdated")
    ):
        result.state, result.warning = (
            "unverified",
            "Recent daily data has gaps or missing bars; comparisons are unavailable.",
        )
        return result
    for horizon, count in (("1d", 1), ("5d", 5), ("1m", 21)):
        if len(bars) > count:
            baseline = Decimal(bars[-count - 1].close)
            if baseline > 0:
                result.changes[horizon] = str(
                    ((Decimal(bars[-1].close) / baseline - 1) * 100).quantize(Decimal("0.0001"))
                )
    if len(bars) >= 21:
        baseline = sum(Decimal(b.volume) for b in bars[-21:-1]) / 20
        if baseline > 0:
            result.activity_ratio = str(
                (Decimal(bars[-1].volume) / baseline).quantize(Decimal("0.01"))
            )
    result.warning = (
        "; ".join(feed.quality.warnings) if feed.quality and feed.quality.warnings else None
    )
    if feed.gaps:
        result.warning = (
            result.warning + " " if result.warning else ""
        ) + "The longer saved series has gaps outside the verified comparison window."
    return result


def latest_receipts(db, owner):
    ranked = (
        select(
            IntelligenceReceipt.id,
            func.row_number()
            .over(
                partition_by=IntelligenceReceipt.source_key,
                order_by=(IntelligenceReceipt.created_at.desc(), IntelligenceReceipt.id.desc()),
            )
            .label("rank"),
        )
        .where(
            IntelligenceReceipt.owner_id == owner.id,
            ~IntelligenceReceipt.source_key.like("catalog:%"),
            ~IntelligenceReceipt.source_key.like("price:%"),
            IntelligenceReceipt.source_key != "market_watchlist",
        )
        .subquery()
    )
    return list(
        db.scalars(
            select(IntelligenceReceipt)
            .join(ranked, ranked.c.id == IntelligenceReceipt.id)
            .where(ranked.c.rank == 1)
            .order_by(IntelligenceReceipt.created_at.desc())
            .limit(100)
        )
    )


def source_items(db, owner, specs):
    aliases = {s.catalog_id: s.id for s in specs if s.catalog_id}
    ids = {s.id for s in specs} | set(aliases)
    currencies = {
        c for s in specs if s.asset_type == "fx" for c in (s.base_currency, s.quote_currency)
    }
    sources, items = [], {}
    for row in latest_receipts(db, owner):
        source = checked(row).payload
        if not (
            ids.intersection(source.get("instrument_ids", []))
            or currencies.intersection(source.get("currencies", []))
        ):
            continue
        age = now() - row.created_at
        future = age < -timedelta(seconds=90)
        stale = future or age > timedelta(hours=6)
        fields = {k: v for k, v in source.items() if k not in {"items", "response_checksum"}}
        fields["receipt_id"] = row.id
        if future:
            fields.update(
                status="unavailable",
                warning="This source receipt has a future timestamp. Refresh sources.",
            )
        elif stale and fields["status"] == "available":
            fields.update(
                status="stale",
                warning="This source has not been checked in more than six hours. Refresh sources.",
            )
        sources.append(IntelligenceSource.model_validate(fields))
        if future:
            continue
        for item in source["items"]:
            if not (
                ids.intersection(item.get("instrument_ids", []))
                or currencies.intersection(item.get("currencies", []))
            ):
                continue
            # Meeting dates belong to the source timezone, not the receipt timezone.
            if item.get("event_date"):
                from zoneinfo import ZoneInfo

                if (
                    item["event_date"]
                    < now().astimezone(ZoneInfo(item["event_timezone"])).date().isoformat()
                ):
                    continue
            view = IntelligenceItem.model_validate(
                {
                    **item,
                    "instrument_ids": [
                        aliases.get(key, key) for key in item.get("instrument_ids", [])
                    ],
                    "receipt_id": row.id,
                    "stale": stale or source["status"] != "available",
                }
            )
            previous = items.get(view.id)
            if previous:
                view.instrument_ids = sorted(set(previous.instrument_ids + view.instrument_ids))
            if not previous or (view.updated_at or view.published_at or row.created_at) > (
                previous.updated_at or previous.published_at or row.created_at
            ):
                items[view.id] = view
            elif previous:
                previous.instrument_ids = sorted(set(previous.instrument_ids + view.instrument_ids))
    return sources, sorted(
        items.values(),
        key=lambda x: (
            x.event_date or "",
            (x.published_at.isoformat() if x.published_at else ""),
            x.id,
        ),
        reverse=True,
    )[:100]


def dashboard(db, owner, family, offset=0, limit=24, query="", watched=False):
    specs, catalogs = intelligence_catalog.universe(db, owner)
    saved = watchlist(db, owner)
    filtered = [
        s
        for s in specs
        if s.asset_type in FAMILIES[family]
        and (not watched or s.id in saved.instrument_ids)
        and (not query or query.casefold() in (s.symbol + " " + s.name).casefold())
    ]
    visible = filtered[offset : offset + limit]
    sources, items = source_items(db, owner, visible)
    candidates = []
    seen = set()
    profiles = db.scalars(
        select(ScannerProfileVersion)
        .where(ScannerProfileVersion.owner_id == owner.id)
        .order_by(ScannerProfileVersion.revision.desc())
    )
    ids = {s.id for s in visible}
    for profile in profiles:
        if profile.profile_id in seen:
            continue
        seen.add(profile.profile_id)
        if not profile.payload["parameters"]["enabled"] or not ids.intersection(
            profile.payload["parameters"]["universe"]
        ):
            continue
        selected, _ = candidate_pipeline.shortlist(db, owner, profile)
        for candidate in selected:
            if candidate.instrument_id in ids and candidate.candidate_id not in {
                c.id for c in candidates
            }:
                candidates.append(
                    scanner.candidate_view(
                        db,
                        owner,
                        scanner.owned(db, scanner.ScannerCandidate, owner, candidate.candidate_id),
                    )
                )
    coverage = [
        "Provider market catalogs are browsable; prices and mover rankings cover the displayed page, not the entire market.",
        "Changes compare completed daily closes: 1, 5 and 21 trading bars. Crypto bars use UTC days; Forex and stocks use their provider session calendars.",
        "Related headlines are reported context; a cause of price movement is not established by a matching article.",
        "News refresh checks metadata and preserves receipts. Earnings estimates, complete economic calendars, positioning and sector classifications are not supplied.",
    ]
    if family == "fx":
        missing = currencies_for(visible) - set(forex_macro.SOURCES)
        coverage.append(
            "Policy news and date-only meetings cover USD, EUR, GBP and JPY. Other currencies have no policy source adapter."
        )
        if missing:
            coverage.append("Missing policy sources: " + ", ".join(sorted(missing)) + ".")
    if family == "crypto":
        coverage.append(
            "Spot prices only. Derivatives funding, open interest and unlock calendars are not connected."
        )
    if family in {"commodities", "rates"}:
        coverage.append(
            "Dedicated instruments and price adapters are not connected for this market. ETFs retain their own identities and are not substituted for underlying instruments."
        )
    return IntelligenceDashboard(
        market=family,
        generated_at=now(),
        total=len(filtered),
        offset=offset,
        limit=limit,
        moves=[move(db, s, owner) for s in visible],
        catalogs=catalogs,
        sources=sources,
        items=items,
        candidates=sorted(candidates, key=lambda c: (-c.setup_score, c.id)),
        watchlist=saved,
        coverage=coverage,
    )


def currencies_for(specs):
    return {c for s in specs if s.asset_type == "fx" for c in (s.base_currency, s.quote_currency)}


def store_source(db, owner, key, payload):
    row = IntelligenceReceipt(
        owner_id=owner.id, source_key=key, payload=payload, checksum=digest(payload)
    )
    db.add(row)
    db.flush()
    return row.id


def recent(db, owner, key):
    row = db.scalar(
        select(IntelligenceReceipt)
        .where(IntelligenceReceipt.owner_id == owner.id, IntelligenceReceipt.source_key == key)
        .order_by(IntelligenceReceipt.created_at.desc())
        .limit(1)
    )
    return (
        checked(row)
        if row and timedelta(0) <= now() - row.created_at < timedelta(minutes=5)
        else None
    )


def refresh(db, owner, body):
    specs = intelligence_catalog.resolve(db, owner, body.instrument_ids)
    if not db.scalar(select(func.pg_try_advisory_xact_lock(913570080))):
        raise HTTPException(
            429, "A market intelligence refresh is already running. Try again shortly."
        )
    results, receipts = [], []
    browse_results, browse_receipts = intelligence_prices.collect_page(
        db, owner, [s for s in specs if not s.registered]
    )
    results.extend(browse_results)
    receipts.extend(browse_receipts)
    for spec in [s for s in specs if s.registered]:
        try:
            with db.begin_nested():
                feed = market.refresh(db, spec.id, "1d", commit=False)
            results.append({"instrument_id": spec.id, "status": feed.state})
        except HTTPException as error:
            results.append(
                {"instrument_id": spec.id, "status": "unavailable", "message": error.detail}
            )
    if body.sources:
        currencies = sorted(currencies_for(specs).intersection(forex_macro.SOURCES))
        needed = [
            c
            for c in currencies
            if any(not recent(db, owner, "policy:" + c + ":" + k) for k in ("news", "calendar"))
        ]
        if needed:
            context = forex_macro.collect_currencies(needed)["data"]
            for source in context["sources"]:
                key = "policy:" + source["currency"] + ":" + source["kind"]
                items = []
                for item in source["items"]:
                    is_news = source["kind"] == "news"
                    items.append(
                        {
                            "id": digest(
                                {"url": item.get("url", source["url"]), "date": item.get("date")}
                            ),
                            "kind": "headline" if is_news else "policy_meeting",
                            "title": item["title"]
                            if is_news
                            else source["label"] + " policy meeting",
                            "url": item.get("url", source["url"]),
                            "source": source["label"],
                            "currencies": [source["currency"]],
                            "published_at": item.get("published_at"),
                            "event_date": item.get("date"),
                            "date_only": not is_news,
                            "event_timezone": item.get("timezone"),
                            "evidence_type": "official",
                        }
                    )
                payload = {
                    "key": key,
                    "label": source["label"]
                    + (" news" if source["kind"] == "news" else " meetings"),
                    "url": source["url"],
                    "status": source["status"],
                    "checked_at": source["checked_at"],
                    "warning": source["warning"],
                    "currencies": [source["currency"]],
                    "items": items,
                }
                receipts.append(store_source(db, owner, key, payload))
        news_specs = [
            s
            for s in specs
            if s.asset_type in {"equity", "etf", "us_listing", "crypto_spot"}
            and s.quote_currency in {"USD", "USDT"}
        ]
        if news_specs:
            # News API uses BTCUSD, while the spot chart provider uses BTCUSDT.
            news_specs = [
                s.model_copy(update={"provider_symbol": s.base_currency + "USD"})
                if s.asset_type == "crypto_spot"
                else s
                for s in news_specs
            ]
            key = "alpaca:" + digest(sorted(s.id for s in news_specs))
            cached = recent(db, owner, key)
            receipts.append(
                cached.id if cached else store_source(db, owner, key, alpaca_news(db, news_specs))
            )
    services.audit(
        db,
        owner,
        "intelligence.refreshed",
        "intelligence",
        owner.id,
        "Owner refreshed market prices and source metadata",
        {"results": results, "receipt_ids": receipts},
    )
    db.commit()
    return IntelligenceRefreshResult(results=results, receipt_ids=receipts)


def reports(db, owner, instrument_id):
    if not db.get(Instrument, instrument_id):
        intelligence_catalog.resolve(db, owner, [instrument_id])
        return []
    return [
        analysis.view(db, row)
        for row in db.scalars(
            select(AnalysisJob)
            .where(
                AnalysisJob.owner_id == owner.id,
                AnalysisJob.instrument_id == instrument_id,
            )
            .order_by(AnalysisJob.created_at.desc())
            .limit(10)
        )
    ]
