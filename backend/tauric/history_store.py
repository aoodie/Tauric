"""Reuse compressed, checksummed OANDA history; only missing ranges use the network."""

import gzip
import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select

from . import discovery_journal as journal
from . import market, paper, quant_features, research
from .data_providers import connections
from .data_quality import verify
from .discovery_models import DiscoveryRecord, HistoryChunk
from .market_calendars import default_context, periods
from .models import Owner, now
from .research_contracts import ResearchBar

PAGE_SLOTS = 4000


def bounds(spec, interval, start, end):
    complete = [(a, b) for a, b in periods(default_context(spec), interval, start, end) if b <= end]
    if not complete:
        raise HTTPException(422, "Choose a history range containing complete trading bars")
    return complete[0][0], complete[-1][1]


def source_key(spec, config):
    # Credential rotation does not change the source; account/environment changes do.
    return paper.checksum(
        {
            "provider": spec.provider,
            "symbol": spec.provider_symbol,
            "environment": config.oanda.environment,
            "account": config.oanda.account_id,
            "prices": "MBA",
            "alignment": "17:00 America/New_York",
            "smooth": False,
        }
    )


def unpack(row):
    # Refuse a corrupt or unexpectedly large compressed body before JSON decoding.
    import zlib

    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = decoder.decompress(bytes(row.content), 12_000_001)
    if len(raw) > 12_000_000 or decoder.unconsumed_tail or not decoder.eof:
        raise HTTPException(409, "Saved history exceeds its bounded format")
    if hashlib.sha256(raw).hexdigest() != row.checksum:
        raise HTTPException(409, "Saved history checksum failed; existing evidence was retained")
    value = json.loads(raw)
    if len(value["candles"]) != row.bars:
        raise HTTPException(409, "Saved history count does not match its receipt")
    return value


def chunks(db, owner, instrument_id, interval, start, end):
    return list(
        db.scalars(
            select(HistoryChunk)
            .where(
                HistoryChunk.owner_id == owner.id,
                HistoryChunk.instrument_id == instrument_id,
                HistoryChunk.interval == interval,
                HistoryChunk.end_at > start,
                HistoryChunk.start_at < end,
            )
            .order_by(HistoryChunk.created_at, HistoryChunk.id)
        )
    )


def missing(start, end, ranges):
    cursor, result = start, []
    for left, right in sorted(ranges):
        if left > cursor:
            result.append((cursor, min(left, end)))
        cursor = max(cursor, right)
        if cursor >= end:
            return result
    if cursor < end:
        result.append((cursor, end))
    return result


def read(db, owner, instrument_id, interval, start, end, *, key=None, require_complete=True):
    rows = chunks(db, owner, instrument_id, interval, start, end)
    selected, ranges, receipts, context, selected_key = {}, [], [], None, key
    for row in rows:
        value = unpack(row)
        if selected_key is None:
            selected_key = value["source_key"]
        if value["source_key"] != selected_key:
            continue
        context = value["data_context"]
        ranges.append((row.start_at, row.end_at))
        receipts.append(
            {"id": row.id, "checksum": row.checksum, "received_at": row.created_at.isoformat()}
        )
        for bar in value["candles"]:
            if (
                quant_features.stamp(bar["open_time"]) >= start
                and quant_features.stamp(bar["close_time"]) <= end
            ):
                selected.setdefault(bar["open_time"], bar)
    gaps = missing(start, end, ranges)
    if context:
        actual = {
            (quant_features.stamp(b["open_time"]), quant_features.stamp(b["close_time"]))
            for b in selected.values()
        }
        internal = [
            (a, b)
            for a, b in periods(context, interval, start, end)
            if b <= end and (a, b) not in actual
        ]
        merged = []
        for a, b in sorted([*gaps, *internal]):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
            else:
                merged.append((a, b))
        gaps = merged
    if require_complete and gaps:
        raise HTTPException(
            409, "Save the missing history range in Data library before running research"
        )
    return {
        "candles": [selected[t] for t in sorted(selected)],
        "data_context": context,
        "chunks": receipts,
        "source_key": selected_key,
        "missing_ranges": [[a.isoformat(), b.isoformat()] for a, b in gaps],
    }


def persist(db, owner, spec, interval, start, end, bars, context, key, received):
    expected = {
        (a, b) for a, b in periods(context, interval, start, end) if a >= start and b <= end
    }
    actual = {
        (quant_features.stamp(b["open_time"]), quant_features.stamp(b["close_time"])) for b in bars
    }
    if actual - expected:
        raise HTTPException(422, "Provider history contains unexpected candle boundaries")
    value = {
        "candles": bars,
        "data_context": context,
        "source_key": key,
        "provider": spec.provider,
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "received_at": received.isoformat(),
        "missing_bars": len(expected - actual),
        "missing_intervals": [[a.isoformat(), b.isoformat()] for a, b in sorted(expected - actual)],
        "history_format": "oanda-history-v1",
        "point_in_time_verified": False,
    }
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    row = HistoryChunk(
        owner_id=owner.id,
        instrument_id=spec.id,
        interval=interval,
        start_at=start,
        end_at=end,
        bars=len(bars),
        content=gzip.compress(raw, mtime=0),
        checksum=hashlib.sha256(raw).hexdigest(),
    )
    db.add(row)
    db.flush()
    return row


def load_page(db, owner, spec, interval, start, end, config):
    context = default_context(spec).model_dump(mode="json")
    expected = [(a, b) for a, b in periods(context, interval, start, end) if b <= end]
    if not expected:
        bars, received = [], now()
    else:
        candles, _, received, context = market.PROVIDERS[spec.provider].collect(
            spec, interval, config=config, start=start, end=end
        )
        bars = [
            ResearchBar.model_validate(b).model_dump(mode="json")
            for b in candles
            if b["closed"]
            and quant_features.stamp(b["open_time"]) >= start
            and quant_features.stamp(b["close_time"]) <= end
        ]
        if bars:
            verify(bars, interval, context, received, require_closed=True)
        elif expected:
            raise HTTPException(
                502, "The provider omitted expected complete bars; saved gaps remain visible"
            )
    if len(bars) > 5000:
        raise HTTPException(422, "Provider history page exceeded the saved-data bound")
    return persist(
        db, owner, spec, interval, start, end, bars, context, source_key(spec, config), received
    )


def request_load(db, owner, body):
    request = body.model_dump(mode="json")
    old = journal.repeated(db, owner, "history_sync", body.request_id, request)
    if old:
        return journal.view(old)
    spec = market.instrument(db, body.instrument_id)
    if spec.provider != "oanda_v20" or spec.asset_type != "fx":
        raise HTTPException(
            422, "The reusable history library currently requires an OANDA currency pair"
        )
    if body.interval not in spec.supported_intervals or body.end > now():
        raise HTTPException(422, "Choose a supported interval and a historical end time")
    key = source_key(spec, connections(db))
    start, end = bounds(spec, body.interval, body.start, body.end)
    row = journal.append(
        db,
        owner,
        "history_sync",
        body.request_id,
        request,
        inputs={
            "source_key": key,
            "instrument": spec.model_dump(mode="json"),
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        status="queued",
        pages_loaded=0,
        cache_hits=0,
        attempts=0,
        error=None,
        next_check_at=None,
    )
    db.commit()
    return journal.view(row)


def process_one(db):
    row = db.scalar(
        select(DiscoveryRecord)
        .where(
            DiscoveryRecord.kind == "history_sync",
            DiscoveryRecord.payload["status"].as_string().in_(("queued", "running")),
        )
        .order_by(DiscoveryRecord.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not row:
        return False
    journal.checked(row)
    retry = row.payload.get("next_check_at")
    if retry and quant_features.stamp(retry) > now():
        return False
    owner = db.get(Owner, row.owner_id)
    journal.lock(db, owner)
    spec = market.instrument(db, row.payload["request"]["instrument_id"])
    request = row.payload["request"]
    start, end = (quant_features.stamp(row.payload["input"][k]) for k in ("start", "end"))
    try:
        config = connections(db)
        key = source_key(spec, config)
        if key != row.payload["input"]["source_key"]:
            raise HTTPException(409, "Market source changed; register a new history request")
        stored = read(
            db, owner, spec.id, request["interval"], start, end, key=key, require_complete=False
        )
        if not stored["missing_ranges"]:
            journal.update(
                row,
                status="completed",
                cache_hits=len(stored["chunks"]),
                finished_at=now().isoformat(),
                saved_bars=len(stored["candles"]),
                error=None,
            )
        else:
            if row.payload["pages_loaded"] >= 64:
                raise HTTPException(
                    422,
                    "History load reached its 64-page recovery bound; inspect saved gaps before retrying",
                )
            left, right = map(quant_features.stamp, stored["missing_ranges"][0])
            # Bound pages on the provider's own bar grid; arbitrary UTC page boundaries can lose daily/H4 bars.
            grid = list(
                periods(
                    default_context(spec),
                    request["interval"],
                    left,
                    min(
                        right,
                        left
                        + timedelta(
                            seconds=market.INTERVAL_SECONDS[request["interval"]] * PAGE_SLOTS
                        ),
                    ),
                )
            )
            boundary = min(right, grid[-1][0] if len(grid) > 1 else right)
            if boundary <= left:
                boundary = right
            load_page(db, owner, spec, request["interval"], left, boundary, config)
            journal.update(
                row,
                status="running",
                pages_loaded=row.payload["pages_loaded"] + 1,
                cache_hits=len(stored["chunks"]),
                error=None,
                attempts=0,
                next_check_at=None,
            )
        db.commit()
    except (HTTPException, ValueError, ArithmeticError) as error:
        attempts = row.payload.get("attempts", 0) + 1
        message = (
            error.detail
            if isinstance(error, HTTPException) and isinstance(error.detail, str)
            else "Saved history could not be verified"
        )
        journal.update(
            row,
            status="failed" if attempts >= 3 else "running",
            attempts=attempts,
            error=message[:500],
            next_check_at=(now() + timedelta(seconds=30 * attempts)).isoformat(),
        )
        db.commit()
    return True


def cached_dataset(db, owner, body):
    """Return None only for missing coverage. Corruption never silently falls back to a fresh download."""
    spec = market.instrument(db, body.instrument_id)
    if spec.provider != "oanda_v20" or spec.asset_type != "fx":
        return None
    start, end = bounds(spec, body.interval, body.start, body.end)
    if not chunks(db, owner, body.instrument_id, body.interval, start, end):
        return None
    key = source_key(spec, connections(db))
    value = read(
        db, owner, body.instrument_id, body.interval, start, end, key=key, require_complete=False
    )
    if value["missing_ranges"]:
        return None
    bars = research.validate_bars(value["candles"], body.interval, value["data_context"])
    payload = {
        "instrument_id": spec.id,
        "interval": body.interval,
        "source": spec.provider,
        "availability": "retrospective",
        "candles": bars,
        "asset_type": spec.asset_type,
        "quote_currency": spec.quote_currency,
        "data_context": value["data_context"],
        "quality": verify(
            bars, body.interval, value["data_context"], now(), require_closed=True
        ).model_dump(mode="json"),
        "provenance": {
            "saved_history": True,
            "chunks": value["chunks"],
            "point_in_time_verified": False,
            "requested_start": body.start.isoformat(),
            "requested_end": body.end.isoformat(),
        },
    }
    return research.dataset_view(research.save(db, owner, "dataset", body, payload))


def historical_dataset(db, owner, body):
    spec = market.instrument(db, body.instrument_id)
    start, end = bounds(spec, body.interval, body.start, body.end)
    config = connections(db)
    value = read(
        db,
        owner,
        spec.id,
        body.interval,
        start,
        end,
        key=source_key(spec, config),
        require_complete=False,
    )
    for left, right in value["missing_ranges"]:
        load_page(
            db,
            owner,
            spec,
            body.interval,
            quant_features.stamp(left),
            quant_features.stamp(right),
            config,
        )
    cached = cached_dataset(db, owner, body)
    if cached is None:
        raise HTTPException(409, "The saved history range remains incomplete")
    return cached


def library(db, owner):
    groups = {}
    for row in db.scalars(
        select(HistoryChunk)
        .where(HistoryChunk.owner_id == owner.id)
        .order_by(HistoryChunk.start_at)
    ):
        value = unpack(row)
        group = groups.setdefault(
            (row.instrument_id, row.interval, value["source_key"]),
            {
                "instrument_id": row.instrument_id,
                "interval": row.interval,
                "source_key": value["source_key"],
                "ranges": [],
                "bars": 0,
                "bytes": 0,
                "missing_bars": 0,
                "last_saved_at": row.created_at.isoformat(),
                "_available": set(),
                "_gaps": set(),
            },
        )
        group["ranges"].append([row.start_at.isoformat(), row.end_at.isoformat()])
        group["bars"] += row.bars
        group["bytes"] += len(row.content)
        group["missing_bars"] += value["missing_bars"]
        group["_available"].update(
            quant_features.stamp(b["open_time"]).timestamp() for b in value["candles"]
        )
        group["_gaps"].update(
            quant_features.stamp(a).timestamp() for a, _ in value.get("missing_intervals", [])
        )
        group["last_saved_at"] = max(group["last_saved_at"], row.created_at.isoformat())
    for group in groups.values():
        group["missing_bars"] = len(group.pop("_gaps") - group.pop("_available"))
    return list(groups.values())
