"""Provider catalog resolution into bounded resumable read-only scan slices."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from . import instrument_discovery, intelligence_catalog, market, paper
from .market_connections import stored_connection
from .market_contracts import InstrumentView
from .models import Instrument, ScannerRun, now


def resolve(db, owner, profile, parameters):
    if not parameters.catalog:
        return parameters.universe, {
            "universe_count": len(parameters.universe),
            "deferred_count": 0,
        }
    provider = parameters.catalog.provider
    family = "fx" if provider == "oanda" else "stocks"
    config, revision, _ = stored_connection(db, provider)
    key = "scanner_catalog:" + provider
    receipt = intelligence_catalog.latest(db, owner, key)
    if (
        not receipt
        or now() - receipt.created_at > timedelta(minutes=5)
        or receipt.payload.get("connection_revision") != revision
    ):
        items, checked_at = instrument_discovery.catalog(provider, config, revision)
        payload = {
            "provider": provider,
            "connection_revision": revision,
            "checked_at": checked_at,
            "items": [r.model_dump(mode="json") for r in items if r.available],
        }
        receipt = intelligence_catalog.store(db, owner, key, payload)
    available = sorted(receipt.payload["items"], key=lambda v: v["symbol"])
    if not available:
        raise HTTPException(409, "The configured account has no available catalog instruments.")
    membership = paper.checksum([r["symbol"] for r in available])
    previous = db.scalar(
        select(ScannerRun)
        .where(ScannerRun.profile_version_id == profile.id)
        .order_by(ScannerRun.created_at.desc(), ScannerRun.id.desc())
        .limit(1)
    )
    progress = previous.payload.get("metrics", {}) if previous else {}
    if previous:
        if paper.checksum(previous.payload) != previous.checksum:
            raise HTTPException(409, "Saved scan progress integrity failed.")
    offset = (
        progress.get("next_universe_offset", 0)
        if progress.get("catalog_membership") == membership
        else 0
    )
    offset = offset if isinstance(offset, int) and 0 <= offset < len(available) else 0
    selected = available[offset : offset + parameters.catalog.instruments_per_cycle]
    specs = [InstrumentView.model_validate(r.definition) for r in db.scalars(select(Instrument))]
    known = {
        (s.provider, s.provider_symbol): s
        for s in specs
        if s.classification_source != "unclassified"
    }
    identifiers = []
    for row in selected:
        symbol = row["symbol"]
        registered = known.get(("oanda_v20" if provider == "oanda" else "alpaca_stocks", symbol))
        if registered:
            identifiers.append(registered.id)
            continue
        fx = family == "fx"
        base, quote = symbol.split("_") if fx else (symbol, "USD")
        spec = InstrumentView(
            id=f"{provider}:{'fx' if fx else 'listing'}:{symbol}",
            symbol=symbol.replace("_", "/") if fx else symbol,
            name=row["name"],
            provider="oanda_v20" if fx else "alpaca_stocks",
            provider_symbol=symbol,
            asset_type="fx" if fx else "equity",
            base_currency=base,
            quote_currency=quote,
            volume_type="provider_ticks" if fx else "feed_shares",
            native_symbol=base + quote + "=X" if fx else symbol.replace(".", "-"),
            native_asset_type="forex" if fx else "stock",
            analysis_scope="",
            calendar_id="oanda_fx" if fx else "us_equity",
            price_side="bid_ask" if fx else "trades",
            classification_source="provider_currency" if fx else "unclassified",
        )
        if not db.get(Instrument, spec.id):
            db.add(
                Instrument(
                    id=spec.id,
                    definition=spec.model_dump(mode="json", exclude={"native_research_ready"}),
                )
            )
            db.flush()
        identifiers.append(spec.id)
    catalog_selected = list(identifiers)
    end = offset + len(selected)
    identifiers = list(dict.fromkeys([*parameters.universe, *identifiers]))
    for identity in parameters.universe:
        market.instrument(db, identity)
    return identifiers, {
        "universe_count": len(available),
        "catalog_membership": membership,
        "catalog_receipt_id": receipt.id,
        "catalog_checksum": receipt.checksum,
        "catalog_provider": provider,
        "connection_revision": revision,
        "universe_offset": offset,
        "next_universe_offset": end if end < len(available) else 0,
        "processed_universe_count": len(selected),
        "catalog_selected": catalog_selected,
        "catalog_slice_attempt": (int(progress.get("catalog_slice_attempt", 0)) + 1)
        if progress.get("universe_offset") == offset
        and progress.get("catalog_membership") == membership
        and progress.get("retry_after")
        else 1,
        "catalog_prior_covered": int(progress.get("catalog_covered_count", 0))
        if offset and progress.get("catalog_membership") == membership
        else 0,
        "catalog_prior_failed": int(progress.get("catalog_failed_count", 0))
        if offset and progress.get("catalog_membership") == membership
        else 0,
        "deferred_count": max(0, len(available) - end),
        "catalog_pass_complete": end == len(available),
    }


def finish(metrics, results, intervals):
    """Advance a failed slice only after bounded recovery; report incomplete coverage."""
    if "catalog_selected" not in metrics:
        return
    selected = set(metrics["catalog_selected"])
    failed = {
        r["instrument_id"]
        for r in results
        if r["instrument_id"] in selected and r.get("status") == "unavailable"
    }
    observed = {
        (r["instrument_id"], r.get("interval"))
        for r in results
        if r["instrument_id"] in selected and r.get("status") != "unavailable"
    }
    covered = {
        identity
        for identity in selected
        if all((identity, interval) in observed for interval in intervals)
    } - failed
    failed |= selected - covered
    metrics["processed_universe_count"] = len(covered)
    metrics["catalog_covered_count"] = metrics.pop("catalog_prior_covered") + len(covered)
    metrics["catalog_failed_count"] = metrics.pop("catalog_prior_failed") + len(failed)
    reached_end = metrics["next_universe_offset"] == 0
    metrics["catalog_pass_reached_end"] = reached_end
    metrics["catalog_pass_complete"] = reached_end and metrics["catalog_failed_count"] == 0
    if failed and metrics["catalog_slice_attempt"] < 3:
        metrics["next_universe_offset"] = metrics["universe_offset"]
        metrics["retry_after"] = max(
            int(metrics.get("retry_after", 0)), 5 * 2 ** (metrics["catalog_slice_attempt"] - 1)
        )
        metrics["next_check_at"] = (now() + timedelta(seconds=metrics["retry_after"])).isoformat()
        # Retrying a slice replaces that slice's coverage; it cannot double count.
        metrics["catalog_covered_count"] -= len(covered)
        metrics["catalog_failed_count"] -= len(failed)
        metrics["catalog_pass_complete"] = False
        metrics["deferred_count"] = metrics["universe_count"] - metrics["universe_offset"]
    elif failed:
        metrics["recovery_exhausted_count"] = len(failed)
        metrics["catalog_pass_complete"] = False
        # Continue to other symbols. Failed members are revisited on the next pass.
    metrics.pop("catalog_selected")
