"""Owner-bound immutable datasets/results; retrospective evidence stays isolated."""

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from . import (
    market,
    outcomes,
    paper,
    replay_engine,
    research_contracts,
    services,
    strategies,
    strategy_contracts,
    strategy_rules,
)
from .data_quality import verify
from .market import INTERVAL_SECONDS
from .market_calendars import default_context
from .models import MarketSnapshot, Owner, identifier, now
from .research_contracts import DatasetView, ReplayView, ResearchBar, ResearchState
from .research_models import ResearchRecord

ASSUMPTIONS = [
    "Retrospective OHLC replay. Historical availability and point-in-time news/model evidence are not verified.",
    "Long-only unlevered spot; one position. SMA crossover signals use only closed-bar prefixes; fills use the next open.",
    "Exact Decimal fee/slippage and quantity-step arithmetic follows the paper spot price and risk formulas. Historical fills assume full liquidity.",
    "Stop gaps fill at the open; intrabar stops fill at the stop reference with slippage. OHLC cannot establish intrabar timing; these exits are stamped at bar close.",
    "Any remaining position is liquidated at the final close with fees/slippage. The final-bar signal cannot execute.",
    "Replay monitors simulated stops; current owner-reviewed paper accounts use stop references and explicit closes.",
    "Equity uses each bar's close mark, including entry fees. Drawdown is sampled at bar close; it excludes unseen intrabar extremes.",
    "No order-book spread, partial fills, exchange minimum notional, funding, leverage or broker connectivity is modeled. This experiment cannot deploy a strategy or authorize orders.",
]

BREAKOUT_ASSUMPTIONS = [
    ASSUMPTIONS[0],
    "Long-only unlevered spot; one position. The pinned strategy uses the same closed-bar breakout/volume and stop/target/channel evaluator as on-demand paper evaluation.",
    "All entry and exit signals execute at the next candle open with fees/slippage, not at a touched trigger price. Stop wins if the closed candle touches both stop and target. No intrabar protective order is simulated.",
    "Sizing uses the saved quantity step, capital ceiling and capital-based strategy risk ceiling, plus the experiment's cash-based risk/allocation caps. Entries at or below the saved stop reference are skipped.",
    "Replay assumes prompt evaluation at every candle close and next-open dispatch within the saved signal validity. It does not simulate polling outages or governance delays.",
    ASSUMPTIONS[2],
    ASSUMPTIONS[4],
    ASSUMPTIONS[6],
    ASSUMPTIONS[7],
    "Account policies, shared allocation ancestry, current deployment controls and model/owner approvals are not replayed. This is a rule experiment, not certification of the full execution workflow or profitability.",
]


def protected_assumptions(spec, context):
    return [
        ASSUMPTIONS[0],
        f"Long-only unlevered {spec.asset_type} research scenario; full fills, one position, quote-currency cash and no margin model.",
        "Signals use closed bars. Entry waits for the first observed open after the assumed delay, and expires at the saved signal lifetime. These delays are scenario assumptions, not measured model latency.",
        "The signal candle's closing ask is a proxy for the pre-submission quote. Stop prices round down and targets round up to the supplied price step; both remain fixed after entry, including delayed or slipped fills.",
        "Protection is assumed active on the entire filled quantity from the entry open. Stop gaps use the observed executable open; intrabar stops use the threshold with adverse slippage. Take-profit limits never fill below their limit before fees.",
        "Opening prices establish opening-gap precedence. If a later bar range touches both protection levels, stop-first is an explicitly conservative scenario and the ambiguity is recorded; the intrabar path and exit time are unknown.",
        "Intrabar exits are stamped at candle close. Financing uses that approximate holding period; rollover order within the candle cannot be reconstructed.",
        "Only channel exits wait for the next observed open. A queued channel exit cannot bypass protection at that open.",
        "Forex uses historical bid/ask without added spread. Equity/ETF bid/ask is approximated from trade candles and the declared spread; limit-touch liquidity and trigger eligibility are assumptions.",
        "Fees use the declared per-unit/minimum commission, notional fee and sell tax. FX financing uses the explicit daily rate at 17:00 New York and configured triple weekday; historical rates and holiday multipliers are not verified.",
        ASSUMPTIONS[4],
        ASSUMPTIONS[6],
        "Partial fills, bracket activation races, order-book depth, outages, actual broker fills and AI/owner approvals are not modeled. This scenario establishes neither broker parity, strategy qualification nor trading authority.",
        *(context or {}).get("warnings", []),
    ]


def owned(db, owner, record_id, kind):
    row = db.get(ResearchRecord, str(record_id))
    if not row or row.owner_id != owner.id or row.kind != kind:
        raise HTTPException(404, "This research record does not exist.")
    if paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Research record integrity failed.")
    return row


def repeated(db, owner, kind, body):
    # Serializes identical requests, including across API processes.
    db.scalar(select(Owner.id).where(Owner.id == owner.id).with_for_update())
    row = db.scalar(
        select(ResearchRecord).where(
            ResearchRecord.owner_id == owner.id,
            ResearchRecord.kind == kind,
            ResearchRecord.request_id == str(body.request_id),
        )
    )
    if row:
        row = owned(db, owner, row.id, kind)
        if row.payload["request"] != body.model_dump(mode="json", exclude_none=True):
            raise HTTPException(
                409, "This request identifier belongs to different research inputs."
            )
    return row


def validate_bars(candles, interval, context=None):
    parsed = [ResearchBar.model_validate(bar) for bar in candles]
    if not 3 <= len(parsed) <= 5000:
        raise HTTPException(422, "Save between 3 and 5,000 closed candles.")
    step = INTERVAL_SECONDS[interval]
    stamp = now()
    if context:
        try:
            quality = verify(candles, interval, context, stamp, require_closed=True)
            if quality.missing_bars:
                raise ValueError("Missing trading bars")
        except (ValueError, ArithmeticError):
            raise HTTPException(
                422, "Resolve calendar, price-side or data gaps before replay."
            ) from None
    for i, bar in enumerate(parsed):
        duration = (bar.close_time - bar.open_time).total_seconds()
        if (not context and not step - 0.001 <= duration <= step) or bar.close_time > stamp:
            raise HTTPException(
                422, "Candles must be closed, historical and match the selected interval."
            )
        if not context and i and (bar.open_time - parsed[i - 1].open_time).total_seconds() != step:
            raise HTTPException(
                422,
                "Candles must be ordered, unique and continuous. Resolve data gaps before replay.",
            )
        if i and bar.open_time < parsed[i - 1].close_time:
            raise HTTPException(422, "Candle periods must not overlap.")
    return [
        {
            **bar.model_dump(mode="json"),
            "open_time": bar.open_time.astimezone(UTC).isoformat(),
            "close_time": bar.close_time.astimezone(UTC).isoformat(),
        }
        for bar in parsed
    ]


def save(db, owner, kind, body, payload, parent_id=None, *, commit=True):
    row = ResearchRecord(
        id=identifier(),
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
        f"research.{kind}_saved",
        f"research_{kind}",
        row.id,
        body.reason,
        {"checksum": row.checksum, "availability": "retrospective"},
    )
    if commit:
        db.commit()
    return row


def capture(db, owner, body):
    existing = repeated(db, owner, "dataset", body)
    if existing:
        return dataset_view(existing)
    snapshot = db.get(MarketSnapshot, str(body.snapshot_id))
    if not snapshot:
        raise HTTPException(404, "This market snapshot does not exist.")
    digest = hashlib.sha256(json.dumps(snapshot.data, sort_keys=True).encode()).hexdigest()
    if digest != snapshot.checksum:
        raise HTTPException(409, "The market snapshot checksum does not match.")
    spec = market.instrument(db, snapshot.instrument_id)
    context = snapshot.data.get("data_context") or default_context(spec).model_dump(mode="json")
    candles = validate_bars(
        [bar for bar in snapshot.data["candles"] if bar["closed"]], snapshot.interval, context
    )
    row = save(
        db,
        owner,
        "dataset",
        body,
        {
            "instrument_id": snapshot.instrument_id,
            "interval": snapshot.interval,
            "source": snapshot.data["source"],
            "availability": "retrospective",
            "candles": candles,
            "asset_type": spec.asset_type,
            "quote_currency": spec.quote_currency,
            "data_context": context,
            "quality": verify(
                candles, snapshot.interval, context, now(), require_closed=True
            ).model_dump(mode="json"),
            "provenance": {
                "snapshot_id": snapshot.id,
                "snapshot_checksum": snapshot.checksum,
                "received_at": snapshot.received_at.isoformat(),
                "provider_time": snapshot.provider_time.isoformat(),
                "original_availability": snapshot.data.get("availability", "observed_at_receipt"),
            },
        },
    )
    return dataset_view(row)


def import_dataset(db, owner, body):
    existing = repeated(db, owner, "dataset", body)
    if existing:
        return dataset_view(existing)
    spec = market.instrument(db, body.instrument_id)
    if spec.asset_type != "crypto_spot" and body.data_context is None:
        raise HTTPException(
            422, "FX and stock imports require explicit calendar and price/volume semantics."
        )
    context = body.data_context or default_context(spec)
    if (context.calendar_id, context.volume_type, context.timezone) != (
        spec.calendar_id,
        spec.volume_type,
        "UTC" if spec.asset_type == "crypto_spot" else "America/New_York",
    ):
        raise HTTPException(422, "Imported data semantics disagree with this instrument.")
    if spec.asset_type == "fx" and context.price_side not in ("midpoint", "bid_ask"):
        raise HTTPException(422, "FX imports require midpoint or bid/ask pricing.")
    if spec.asset_type != "fx" and context.price_side != "trades":
        raise HTTPException(422, "This instrument requires trade-price bars.")
    if spec.asset_type in ("equity", "etf") and context.adjustment not in ("raw", "split"):
        raise HTTPException(422, "Stock imports must declare raw or split-adjusted prices.")
    if spec.asset_type != "crypto_spot":
        context.warnings = [
            *context.warnings,
            "Owner-supplied calendar, price semantics and historical availability are unverified.",
        ]
    candles = validate_bars(
        [bar.model_dump(mode="json") for bar in body.candles], body.interval, context
    )
    row = save(
        db,
        owner,
        "dataset",
        body,
        {
            "instrument_id": body.instrument_id,
            "interval": body.interval,
            "source": body.source,
            "availability": "retrospective",
            "candles": candles,
            "asset_type": spec.asset_type,
            "quote_currency": spec.quote_currency,
            "data_context": context.model_dump(mode="json"),
            "quality": verify(
                candles, body.interval, context, now(), require_closed=True
            ).model_dump(mode="json"),
            "provenance": {
                "imported_at": now().isoformat(),
                "source_verification": "owner_supplied_unverified",
            },
        },
    )
    return dataset_view(row)


def run(db, owner, body, *, commit=True):
    existing = repeated(db, owner, "run", body)
    if existing:
        return run_view(existing)
    dataset = owned(db, owner, body.dataset_id, "dataset")
    spec = market.instrument(db, dataset.payload["instrument_id"])
    context = dataset.payload.get("data_context")
    candles = validate_bars(dataset.payload["candles"], dataset.payload["interval"], context)
    costs = body.parameters.asset_costs
    if spec.asset_type != "crypto_spot":
        if not costs or costs.asset_type != spec.asset_type:
            raise HTTPException(422, "Select explicit trading costs matching the dataset asset.")
        if (
            spec.asset_type != "fx"
            and body.parameters.engine != "sma_cross_spot_v1"
            and not body.parameters.broker_protection
        ):
            raise HTTPException(
                422, "Saved breakout execution is currently qualified only for crypto spot."
            )
        if context and context["price_side"] == "bid_ask" and Decimal(costs.spread_bps) != 0:
            raise HTTPException(
                422,
                "Historical bid/ask prices already include spread; enter zero synthetic spread.",
            )
    elif costs:
        raise HTTPException(422, "FX and stock costs cannot be applied to a crypto dataset.")
    strategy = None
    from . import asset_costs

    modules = [replay_engine, outcomes, research_contracts, asset_costs]
    if costs:
        from . import data_quality, market_calendars

        modules.extend([data_quality, market_calendars])
    if body.parameters.engine == "closed_bar_breakout_v1":
        strategy = strategies.view(
            strategies.version(db, owner, body.parameters.strategy_version_id)
        )
        rules = strategy.parameters.rules
        if not rules:
            raise HTTPException(422, "This saved strategy has no executable breakout rules.")
        if rules.interval != dataset.payload["interval"]:
            raise HTTPException(422, "The dataset interval must match the saved strategy rules.")
        minimum_bars = max(rules.entry_lookback, rules.exit_lookback) + 2
        modules.extend([strategy_rules, strategy_contracts])
    else:
        minimum_bars = body.parameters.slow_window + 2
    if len(candles) < minimum_bars:
        raise HTTPException(
            422,
            f"The dataset needs at least {minimum_bars} candles for these rules and a later fill.",
        )
    try:
        result = replay_engine.simulate(candles, body.parameters, strategy, data_context=context)
    except (ValueError, ArithmeticError):
        raise HTTPException(
            422, "Replay inputs, protection assumptions or sizing cannot be supported by the candles and unlevered cash balance."
        ) from None
    sources = {module.__name__: Path(module.__file__).read_text() for module in modules}
    row = save(
        db,
        owner,
        "run",
        body,
        {
            "dataset_checksum": dataset.checksum,
            "asset_type": spec.asset_type,
            "quote_currency": spec.quote_currency,
            "engine_version": replay_engine.PROTECTED_ENGINE_VERSION
            if body.parameters.broker_protection
            else replay_engine.BREAKOUT_ENGINE_VERSION
            if strategy
            else replay_engine.ASSET_ENGINE_VERSION
            if costs
            else replay_engine.ENGINE_VERSION,
            "engine_checksum": paper.checksum(sources),
            "engine_sources": sources,
            "result": result.model_dump(mode="json"),
            "assumptions": protected_assumptions(spec, context)
            if body.parameters.broker_protection
            else asset_assumptions(spec, context)
            if costs
            else BREAKOUT_ASSUMPTIONS
            if strategy
            else ASSUMPTIONS,
            **({"strategy_version": strategy.model_dump(mode="json")} if strategy else {}),
        },
        dataset.id,
        commit=commit,
    )
    return run_view(row)


def dataset_view(row):
    p = row.payload
    return DatasetView(
        id=row.id,
        name=row.name,
        instrument_id=p["instrument_id"],
        interval=p["interval"],
        source=p["source"],
        bar_count=len(p["candles"]),
        start_time=datetime.fromisoformat(p["candles"][0]["open_time"]),
        end_time=datetime.fromisoformat(p["candles"][-1]["close_time"]),
        checksum=row.checksum,
        created_at=row.created_at,
        asset_type=p.get("asset_type", "crypto_spot"),
        quote_currency=p.get("quote_currency", "USDT"),
        data_context=p.get("data_context"),
        quality=p.get("quality"),
    )


def asset_assumptions(spec, context):
    return [
        ASSUMPTIONS[0],
        f"Long-only unlevered {spec.asset_type} experiment; one position; cash, fees, financing and P&L are in {spec.quote_currency}. No currency conversion or margin model.",
        "Closed-bar SMA signals and next-open full-liquidity fills; session closures delay fills to the next available trading bar. Calendar gaps are rejected.",
        "Historical FX bid/ask opens/closes take precedence over synthetic spread. Other fills use the explicit full spread divided by two on each side, then adverse slippage.",
        "Long FX SMA stops trigger on historical bid open/low (or the declared synthetic bid when sides are absent). Gap fills use the bid open; intrabar fills use the executable stop threshold plus adverse slippage. OHLC cannot recover the trigger time, path, or liquidity; intrabar exits are stamped at bar close. Breakout rules retain their separately declared closed-bar/next-open execution model.",
        "Fees are the maximum of per-unit plus notional commission and minimum commission, with an additional sell-side tax. Sizing reserves conservative minimum commissions.",
        "FX financing is an owner-supplied constant daily debit (negative is a credit), at 17:00 New York, with a configured triple weekday. The preceding observed mark is used, without future-bar prices. Broker holiday multipliers and historical rates are not verified.",
        ASSUMPTIONS[3],
        ASSUMPTIONS[4],
        ASSUMPTIONS[6],
        "No dividends, borrow fees, shorts, partial fills, corporate-action cashflows, settlement/buying-power restrictions or broker execution are modeled. This is research-grade evidence.",
        *(context or {}).get("warnings", []),
    ]


def run_view(row):
    p = row.payload
    return ReplayView(
        id=row.id,
        name=row.name,
        dataset_id=row.parent_id,
        dataset_checksum=p["dataset_checksum"],
        hypothesis=p["request"]["hypothesis"],
        parameters=p["request"]["parameters"],
        asset_type=p.get("asset_type", "crypto_spot"),
        quote_currency=p.get("quote_currency", "USDT"),
        strategy_version=p.get("strategy_version"),
        engine_version=p["engine_version"],
        engine_checksum=p["engine_checksum"],
        result=p["result"],
        assumptions=p["assumptions"],
        checksum=row.checksum,
        created_at=row.created_at,
    )


def state(db, owner, offset=0):
    datasets, runs = [], []
    for kind, dest, view in [("dataset", datasets, dataset_view), ("run", runs, run_view)]:
        rows = db.scalars(
            select(ResearchRecord)
            .where(ResearchRecord.owner_id == owner.id, ResearchRecord.kind == kind)
            .order_by(ResearchRecord.created_at.desc(), ResearchRecord.id.desc())
            .offset(offset)
            .limit(25)
        )
        for row in rows:
            dest.append(view(owned(db, owner, row.id, kind)))
    return ResearchState(datasets=datasets, runs=runs)
