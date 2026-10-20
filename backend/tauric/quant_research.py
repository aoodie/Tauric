"""Session evidence and durable external-engine jobs with frozen Lab inputs."""

import math
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from . import market, paper, quant_features, research, services
from .models import Owner, WorkerStatus, now
from .quant_contracts import LabRecordView, LabState, SessionWindow
from .quant_models import QuantRecord

KINDS = ("session_report", "quant_job")
PRESETS = [
    SessionWindow(),
    SessionWindow(
        name="London",
        timezone="Europe/London",
        opening="08:00",
        closing="17:00",
        weekdays=list(range(5)),
    ),
    SessionWindow(
        name="New York",
        timezone="America/New_York",
        opening="08:00",
        closing="17:00",
        weekdays=list(range(5)),
    ),
    SessionWindow(
        name="Tokyo",
        timezone="Asia/Tokyo",
        opening="09:00",
        closing="18:00",
        weekdays=list(range(5)),
    ),
    SessionWindow(
        name="US stock regular session",
        timezone="America/New_York",
        opening="09:30",
        closing="16:00",
        weekdays=list(range(5)),
    ),
]


def owned(db, owner, identity, kind):
    row = db.get(QuantRecord, str(identity))
    if not row or row.owner_id != owner.id or row.kind != kind:
        raise HTTPException(404, "This Lab record does not exist")
    view(row)
    return row


def repeated(db, owner, kind, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    row = db.scalar(
        select(QuantRecord).where(
            QuantRecord.owner_id == owner.id,
            QuantRecord.kind == kind,
            QuantRecord.request_id == str(body.request_id),
        )
    )
    if row:
        view(row)
        if row.payload["request"] != body.model_dump(mode="json", exclude_none=True):
            raise HTTPException(409, "This request identifier belongs to different Lab inputs")
    return row


def save(db, owner, kind, body, payload, parent_id):
    row = QuantRecord(
        owner_id=owner.id,
        kind=kind,
        request_id=str(body.request_id),
        parent_id=parent_id,
        name=body.name,
        payload={"request": body.model_dump(mode="json", exclude_none=True), **payload},
        checksum="",
    )
    row.checksum = paper.checksum(row.payload)
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "research." + kind + "_saved",
        "research_lab",
        row.id,
        body.reason,
        {"checksum": row.checksum, "authority": "research_only"},
    )
    db.commit()
    return row


def historical_dataset(db, owner, body):
    from .data_providers import connections
    from .data_quality import verify

    existing = research.repeated(db, owner, "dataset", body)
    if existing:
        return research.dataset_view(existing)
    from .history_store import cached_dataset

    cached = cached_dataset(db, owner, body)
    if cached:
        return cached
    if body.end > now():
        raise HTTPException(422, "Historical data cannot end in the future")
    spec = market.instrument(db, body.instrument_id)
    if spec.provider not in ("oanda_v20", "alpaca_stocks"):
        raise HTTPException(
            422,
            "Historical retrieval currently supports connected OANDA Forex and Alpaca stocks/ETFs; other assets can be imported",
        )
    if (
        spec.provider == "oanda_v20"
        and (body.end - body.start).total_seconds() / market.INTERVAL_SECONDS[body.interval] > 5000
    ):
        raise HTTPException(
            422, "Choose a range of at most 5,000 calendar bars for OANDA retrieval"
        )
    if spec.provider == "oanda_v20" and spec.asset_type == "fx":
        from .history_store import historical_dataset as saved_history

        return saved_history(db, owner, body)
    candles, provider_at, received, context = market.PROVIDERS[spec.provider].collect(
        spec, body.interval, config=connections(db), start=body.start, end=body.end
    )
    selected = [
        b
        for b in candles
        if b["closed"]
        and quant_features.stamp(b["open_time"]) >= body.start
        and quant_features.stamp(b["close_time"]) <= body.end
    ]
    try:
        bars = research.validate_bars(selected, body.interval, context)
    except ValueError:
        raise HTTPException(422, "Historical provider prices failed validation") from None
    payload = {
        "instrument_id": spec.id,
        "interval": body.interval,
        "source": spec.provider,
        "availability": "retrospective",
        "candles": bars,
        "asset_type": spec.asset_type,
        "quote_currency": spec.quote_currency,
        "data_context": context,
        "quality": verify(
            bars, body.interval, context, provider_at, require_closed=True
        ).model_dump(mode="json"),
        "provenance": {
            "received_at": received.isoformat(),
            "provider_as_of": provider_at.isoformat(),
            "requested_start": body.start.isoformat(),
            "requested_end": body.end.isoformat(),
            "point_in_time_verified": False,
        },
    }
    return research.dataset_view(research.save(db, owner, "dataset", body, payload))


def view(row):
    if paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Research record integrity failed")
    return LabRecordView(
        id=row.id,
        kind=row.kind,
        name=row.name,
        checksum=row.checksum,
        created_at=row.created_at,
        payload=row.payload,
    )


def state(db, owner):
    engines = []
    for key, label in (("lean", "LEAN execution backtests"), ("qlib", "Qlib machine learning")):
        worker = db.get(WorkerStatus, "lab_" + key)
        ready = bool(worker and now() - worker.heartbeat_at < timedelta(seconds=90))
        engines.append(
            {
                "id": key,
                "label": label,
                "ready": ready,
                "version": worker.version if worker else None,
                "detail": "Ready for research jobs"
                if ready
                else "Start the separate research worker to process experiments",
            }
        )
    rows = db.scalars(
        select(QuantRecord)
        .where(QuantRecord.owner_id == owner.id, QuantRecord.kind.in_(KINDS))
        .order_by(QuantRecord.created_at.desc())
        .limit(20)
    )
    records = []
    for row in rows:
        record = view(row)
        payload = {
            k: v for k, v in record.payload.items() if k not in ("features", "model_predictions")
        }
        if payload.get("result"):
            result = dict(payload["result"])
            result.pop("predictions", None)
            for key, maximum in (("equity_curve", 200), ("fills", 100)):
                values = result.get(key, [])
                if len(values) > maximum:
                    result[key] = values[:: math.ceil(len(values) / (maximum - 1))] + [values[-1]]
            payload["result"] = result
        payload["preview"] = True
        record.payload = payload
        records.append(record)
    return LabState(engines=engines, sessions=PRESETS, records=records)


def dataset(db, owner, body):
    row = research.owned(db, owner, body.dataset_id, "dataset")
    candles = row.payload["candles"]
    research.validate_bars(candles, row.payload["interval"], row.payload.get("data_context"))
    return row


def report(db, owner, body):
    existing = repeated(db, owner, "session_report", body)
    if existing:
        return view(existing)
    if body.as_of > now():
        raise HTTPException(
            422, "The evidence cutoff cannot be in the future; use forecast mode for scenarios"
        )
    source = dataset(db, owner, body)
    selected = [
        b
        for b in source.payload["candles"]
        if body.start <= quant_features.stamp(b["open_time"])
        and quant_features.stamp(b["close_time"]) <= body.as_of
        and quant_features.in_session(
            quant_features.stamp(b["open_time"]),
            quant_features.stamp(b["close_time"]),
            body.session,
        )
    ]
    if len(selected) < 3:
        raise HTTPException(422, "At least three whole closed bars must fit the period and session")
    first, last = Decimal(selected[0]["open"]), Decimal(selected[-1]["close"])
    high, low = max(Decimal(b["high"]) for b in selected), min(Decimal(b["low"]) for b in selected)
    returns = [
        math.log(float(b["close"]) / float(a["close"]))
        for a, b in zip(selected, selected[1:], strict=False)
    ]
    mean = sum(returns) / len(returns)
    spreads = [
        Decimal(b["ask"]["close"]) - Decimal(b["bid"]["close"])
        for b in selected
        if b.get("bid") and b.get("ask")
    ]
    warnings = [
        "Price-based descriptive session research; this is not a native analyst graph report or trading approval.",
        "Session hours are configurable research windows, not a claim that OTC Forex has one official exchange session.",
        "Historical provider revisions and point-in-time news availability have not been independently certified.",
        "No future observations or probability estimates are supplied by these scenarios.",
    ]
    if not body.evidence:
        warnings.append(
            "No timestamped news or macro evidence was supplied; this report covers prices and activity only."
        )
    if body.mode == "current" and now() - quant_features.stamp(
        selected[-1]["close_time"]
    ) > timedelta(days=1):
        warnings.append(
            "The saved dataset is older than one day; this summary is not fresh current-market evidence."
        )
    if any(
        quant_features.stamp(b["open_time"]) - quant_features.stamp(a["close_time"])
        > timedelta(seconds=1)
        for a, b in zip(selected, selected[1:], strict=False)
    ):
        warnings.append(
            "Aggregate return and close-to-close volatility include gaps between selected sessions; they are not isolated intraday strategy returns."
        )
    context = source.payload.get("data_context", {})
    if context.get("adjustment") == "split":
        warnings.append(
            "Split-adjusted prices are retrospective; corporate-action hindsight is possible."
        )
    payload = {
        "dataset_checksum": source.checksum,
        "instrument_id": source.payload["instrument_id"],
        "interval": source.payload["interval"],
        "mode": body.mode,
        "as_of": body.as_of.isoformat(),
        "session": body.session.model_dump(),
        "bars": len(selected),
        "evidence_end": selected[-1]["close_time"],
        "source": source.payload["source"],
        "statistics": {
            "opening": str(first),
            "closing": str(last),
            "high": str(high),
            "low": str(low),
            "return_percent": str((last / first - 1) * 100),
            "log_return_volatility": math.sqrt(
                sum((r - mean) ** 2 for r in returns) / len(returns)
            ),
            "mean_spread": str(sum(spreads) / len(spreads)) if spreads else None,
        },
        "scenarios": [
            {
                "name": "Upside breakout",
                "condition": f"A subsequent closed bar exceeds {high}",
                "invalidation": f"A subsequent closed bar falls below {low}",
            },
            {
                "name": "Downside breakout",
                "condition": f"A subsequent closed bar falls below {low}",
                "invalidation": f"A subsequent closed bar exceeds {high}",
            },
            {
                "name": "Range continuation",
                "condition": f"Price remains between {low} and {high}",
                "invalidation": "A closed-bar breakout of the observed range",
            },
        ],
        "forecast_horizon_bars": body.forecast_bars if body.mode == "forecast" else None,
        "evidence": [n.model_dump(mode="json") for n in body.evidence],
        "point_in_time_verified": False,
        "warnings": warnings,
        "authority": "research_only",
    }
    return view(save(db, owner, "session_report", body, payload, source.id))


def enqueue(db, owner, body):
    existing = repeated(db, owner, "quant_job", body)
    if existing:
        return view(existing)
    source = dataset(db, owner, body)
    context = None
    if body.context_dataset_id:
        context = research.owned(db, owner, body.context_dataset_id, "dataset")
        if (
            context.payload["instrument_id"] != source.payload["instrument_id"]
            or context.payload["interval"] != body.research_strategy.context_interval
            or market.INTERVAL_SECONDS[context.payload["interval"]]
            <= market.INTERVAL_SECONDS[source.payload["interval"]]
        ):
            raise HTTPException(422, "Context must be the same instrument on a higher timeframe")
        research.validate_bars(
            context.payload["candles"],
            context.payload["interval"],
            context.payload.get("data_context"),
        )
    spec = market.instrument(db, source.payload["instrument_id"])
    if (
        spec.asset_type not in ("fx", "equity", "etf")
        or body.asset_costs.asset_type != spec.asset_type
    ):
        raise HTTPException(422, "Select matching Forex, stock or ETF costs for this experiment")
    if spec.asset_type == "fx" and any(
        c in {"XAU", "XAG", "XPT", "XPD"} for c in (spec.base_currency, spec.quote_currency)
    ):
        raise HTTPException(
            422, "This execution lab supports currency pairs; metals need a separate CFD model"
        )
    if body.test_end > now() or body.test_end > quant_features.stamp(
        source.payload["candles"][-1]["close_time"]
    ):
        raise HTTPException(
            422, "The unseen test period must end within the saved historical dataset"
        )
    if (
        spec.asset_type == "fx"
        and source.payload.get("data_context", {}).get("price_side") != "bid_ask"
    ):
        raise HTTPException(422, "Forex experiments require historical bid and ask candles")
    if spec.asset_type == "fx" and Decimal(body.asset_costs.spread_bps) != 0:
        raise HTTPException(
            422, "Bid/ask data already includes spread; synthetic spread must be zero"
        )
    try:
        features = quant_features.build(source.payload["candles"], body)
    except (ValueError, ArithmeticError) as error:
        raise HTTPException(422, str(error)) from None
    model = None
    if body.model_run_id:
        model = owned(db, owner, body.model_run_id, "quant_job")
        model_request = model.payload["request"]
        if (
            model_request.get("engine") != "qlib"
            or model.payload.get("status") != "completed"
            or model.payload.get("dataset_checksum") != source.checksum
            or any(
                model_request.get(k) != body.model_dump(mode="json").get(k)
                for k in (
                    "session",
                    "train_end",
                    "validation_end",
                    "test_end",
                    "horizon_bars",
                    "lookback_bars",
                )
            )
        ):
            raise HTTPException(
                422,
                "Use a completed Qlib experiment with the same dataset, session, splits and feature horizon",
            )
    # Bounded per-owner queue without a new schema or any trading-table mutations.
    count = len(
        list(
            db.scalars(
                select(QuantRecord.id)
                .where(
                    QuantRecord.owner_id == owner.id,
                    QuantRecord.kind == "quant_job",
                    QuantRecord.payload["status"].as_string().in_(("queued", "running")),
                )
                .limit(8)
            )
        )
    )
    if count >= 8:
        raise HTTPException(429, "Finish or cancel queued experiments before adding more")
    payload = {
        "status": "queued",
        "engine": body.engine,
        "dataset_checksum": source.checksum,
        "feature_checksum": paper.checksum(features),
        "features": features,
        "instrument": spec.model_dump(mode="json"),
        "result": None,
        "error": None,
        "model_predictions": model.payload["result"]["predictions"] if model else None,
        "authority": "research_only",
        "point_in_time_verified": False,
    }
    if context:
        payload["context_candles"] = context.payload["candles"]
        payload["context_checksum"] = context.checksum
    return view(save(db, owner, "quant_job", body, payload, source.id))


def cancel(db, owner, record_id):
    row = owned(db, owner, record_id, "quant_job")
    db.refresh(row, with_for_update=True)
    if row.payload["status"] not in ("queued", "running"):
        raise HTTPException(409, "Only queued or running experiments can be cancelled")
    row.payload = {**row.payload, "status": "cancelled", "finished_at": now().isoformat()}
    row.checksum = paper.checksum(row.payload)
    services.audit(
        db,
        owner,
        "research.quant_cancelled",
        "research_quant_job",
        row.id,
        "Owner cancelled the research experiment",
        {"checksum": row.checksum},
    )
    db.commit()
    return view(row)
