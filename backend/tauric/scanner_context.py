"""Economic evidence acquisition is separate from deterministic candle detection."""

from datetime import datetime, timedelta
from time import monotonic
from types import SimpleNamespace

from sqlalchemy import select

from . import intelligence_catalog, market, tradingview_sources
from .intelligence_models import IntelligenceReceipt
from .models import ScannerProfileVersion, now
from .scanner_contracts import ScannerParameters


def refresh(db, owner, instrument_ids, *, maximum_currencies=16):
    currencies = set()
    for identity in instrument_ids:
        spec = market.instrument(db, identity)
        currencies.update(
            (spec.base_currency, spec.quote_currency)
            if spec.asset_type == "fx"
            else ("USD",)
            if spec.asset_type in {"equity", "etf"}
            else ()
        )
    receipts = []
    acquired = 0
    deadline = monotonic() + 20
    for currency in sorted(currencies):
        key = "scanner_calendar:" + currency
        previous = intelligence_catalog.latest(db, owner, key)
        if previous and now() - previous.created_at < timedelta(minutes=5):
            receipts.append(previous.id)
            continue
        if acquired >= maximum_currencies or monotonic() >= deadline:
            break
        acquired += 1
        sources = tradingview_sources.collect(
            db,
            SimpleNamespace(base_currency=currency, quote_currency=currency),
            calendar_only=True,
            deadline=deadline,
        )
        source = next((s for s in sources if s["kind"] == "economic_calendar"), {})
        source = {
            **source,
            "known_at": now().isoformat(),
        }
        receipts.append(intelligence_catalog.store(db, owner, key, source).id)
    db.commit()
    return {"receipt_ids": receipts, "currencies_refreshed": acquired, "model_calls": 0}


def process_one(db):
    """Acquire calendar observations before, and separately from, candle scoring."""
    from . import scanner
    from .models import Owner

    seen = set()
    work = {}
    for profile in db.scalars(
        select(ScannerProfileVersion).order_by(ScannerProfileVersion.created_at.desc())
    ):
        if profile.profile_id in seen:
            continue
        seen.add(profile.profile_id)
        p = ScannerParameters.model_validate(scanner.checked(profile).payload["parameters"])
        if p.enabled and p.automatic_enabled and p.scoring_version == "multifactor_v1":
            ids = list(p.universe)
            if p.catalog:
                from .models import Instrument

                provider = "oanda_v20" if p.catalog.provider == "oanda" else "alpaca_stocks"
                ids.extend(
                    r.id
                    for r in db.scalars(select(Instrument))
                    if r.definition.get("provider") == provider
                )
            work.setdefault(profile.owner_id, []).extend(ids)
    for owner_id, ids in work.items():
        refresh(db, db.get(Owner, owner_id), list(dict.fromkeys(ids)), maximum_currencies=2)


def evidence(db, owner, spec, stamp):
    if spec.asset_type == "crypto_spot":
        return {"status": "not_applicable", "receipts": []}
    currencies = {spec.base_currency, spec.quote_currency} if spec.asset_type == "fx" else {"USD"}
    receipts, known_events, complete = [], [], True
    for currency in sorted(currencies):
        row = db.scalar(
            select(IntelligenceReceipt)
            .where(
                IntelligenceReceipt.owner_id == owner.id,
                IntelligenceReceipt.source_key == "scanner_calendar:" + currency,
                IntelligenceReceipt.created_at <= stamp,
            )
            .order_by(IntelligenceReceipt.created_at.desc(), IntelligenceReceipt.id.desc())
            .limit(1)
        )
        if not row:
            complete = False
            continue
        # Verify the exact as-of receipt, not a newer replacement.
        if intelligence_catalog.digest(row.payload) != row.checksum:
            from fastapi import HTTPException

            raise HTTPException(409, "Economic calendar receipt integrity failed.")
        source = row.payload
        receipts.append({"id": row.id, "checksum": row.checksum, "currency": currency})
        try:
            start = datetime.fromisoformat(source["coverage_start"])
            end = datetime.fromisoformat(source["coverage_end"])
            known = datetime.fromisoformat(source["known_at"])
            usable = (
                source.get("status") == "available"
                and not source.get("items_omitted")
                and known <= stamp
                and 0 <= (stamp - known).total_seconds() <= 3600
                and start <= stamp - timedelta(minutes=30)
                and end >= stamp + timedelta(minutes=30)
            )
            complete &= usable
            for event in source.get("items", []):
                if not event.get("time"):
                    complete = False
                    continue
                when = datetime.fromisoformat(event["time"])
                if known <= stamp and abs((when - stamp).total_seconds()) <= 1800:
                    known_events.append(event["id"])
        except (ValueError, KeyError, TypeError):
            complete = False
    return {
        "status": "event" if known_events else "clean" if complete else "unknown",
        "receipts": receipts,
        "events": known_events,
        "complete": complete,
    }
