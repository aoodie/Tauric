"""Bounded strategy exploration over saved history; no financial authority."""

from datetime import UTC, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from . import discovery_journal as journal
from . import (
    discovery_signals,
    history_store,
    market,
    paper,
    quant_features,
    quant_research,
    research,
)
from .discovery_contracts import DiscoveryParameters, DiscoveryStart, HistoryLoad, ResearchStrategy
from .discovery_models import DiscoveryRecord
from .models import Owner, WorkerStatus, now
from .quant_contracts import ExperimentCreate, HistoricalDatasetCreate
from .quant_models import QuantRecord


def identity(value):
    return uuid5(NAMESPACE_URL, "tauric:strategy-discovery:v1:" + value)


def heartbeat(db):
    db.execute(
        insert(WorkerStatus)
        .values(id="strategy_discovery", heartbeat_at=now(), version="discovery-v1")
        .on_conflict_do_update(
            index_elements=[WorkerStatus.id],
            set_={"heartbeat_at": now(), "version": "discovery-v1"},
        )
    )
    db.commit()


def head(db, owner):
    row = db.scalar(
        select(DiscoveryRecord)
        .where(DiscoveryRecord.owner_id == owner.id, DiscoveryRecord.kind == "settings")
        .order_by(DiscoveryRecord.created_at.desc(), DiscoveryRecord.id.desc())
        .limit(1)
    )
    return journal.checked(row) if row else None


def parameters(row):
    return (
        DiscoveryParameters.model_validate(row.payload["request"]["parameters"])
        if row
        else DiscoveryParameters()
    )


def save_settings(db, owner, body):
    request = body.model_dump(mode="json")
    previous = journal.repeated(db, owner, "settings", body.request_id, request)
    if previous:
        return state(db, owner)
    current = head(db, owner)
    revision = current.payload["revision"] if current else 0
    if body.expected_revision != revision:
        raise HTTPException(409, "Research settings changed; reload before saving")
    for key in body.parameters.instrument_ids:
        spec = market.instrument(db, key)
        if spec.provider != "oanda_v20" or spec.asset_type != "fx":
            raise HTTPException(
                422, "Automatic discovery currently uses saved OANDA currency history"
            )
    if body.parameters.ai_enabled:
        from .discovery_ai import validate_profile

        validate_profile(db, body.parameters)
    journal.append(db, owner, "settings", body.request_id, request, revision=revision + 1)
    db.commit()
    return state(db, owner)


def today_count(db, owner, model, *, kind=None):
    query = (
        select(func.count())
        .select_from(model)
        .where(
            model.owner_id == owner.id,
            model.created_at >= now().replace(hour=0, minute=0, second=0, microsecond=0),
        )
    )
    if kind:
        query = query.where(model.kind == kind)
    if model is QuantRecord:
        query = query.where(
            QuantRecord.payload["request"]["reason"].as_string()
            == "Strategy discovery: frozen chronological comparison"
        )
    return db.scalar(query)


def explanation(definition):
    names = {
        "trend": "Trend following",
        "breakout": "Closed-bar breakout",
        "mean_reversion": "Mean reversion",
    }
    if not definition:
        return {
            "title": "Qlib return forecast",
            "entry": "Predicted return exceeds the declared trading-cost threshold",
            "exit": "Forecast no longer exceeds costs; final test position closes",
            "failure_conditions": "Forecast decay, changing market conditions and underestimated execution costs",
            "execution_eligibility": "Research model; requires a separately qualified Tauric execution strategy",
        }
    components = definition["components"]
    title = " + ".join(names[c["family"]] for c in components)
    entries, exits = [], []
    for c in components:
        fast, slow = c.get("fast_window", 5), c.get("slow_window", 20)
        if c["family"] == "trend":
            entries.append(f"{fast}-bar average above the {slow}-bar average")
            exits.append("Fast average returns to or below the slow average")
        elif c["family"] == "mean_reversion":
            entries.append(
                f"Close at least {c.get('entry_z', 2)} standard deviations below the {slow}-bar mean"
            )
            exits.append("Close returns to the rolling mean")
        else:
            entries.append(
                f"Close above the prior {slow}-bar high; provider activity ratio at least {c.get('activity_ratio', '1')}"
            )
            exits.append(f"Close below the prior {fast}-bar low")
    return {
        "title": title,
        "entry": "; ".join(entries),
        "exit": "; ".join(exits) + ". Remaining exposure closes at test end.",
        "context": f"Use closed {definition['context_interval']} bars only. Compare the latest context close with its {definition.get('context_window', 20)}-bar mean: trend/breakout require it above the mean; mean reversion requires it at or below the mean."
        if definition.get("context_interval")
        else "Single-timeframe rules",
        "combination": (
            "Use one rule, capped at the experiment allocation"
            if definition["mode"] == "single"
            else "Switch the full experiment allocation between trend/breakout and mean reversion according to the context filter; otherwise stay flat"
            if definition["mode"] == "regime_switch"
            else "Combine independently active components: "
            + ", ".join(f"{names[c['family']]} {Decimal(c['weight']) * 100:g}%" for c in components)
            + ". Total exposure cannot exceed the experiment allocation"
        ),
        "failure_conditions": "Whipsaws, persistent moves against mean reversion, unstable combinations and higher trading costs",
        "execution_eligibility": "Research rules; broker qualification and activation remain separate",
    }


def template(p, number):
    family_count = len(p.families) + int(p.qlib)
    phase = number // (family_count * len(p.intervals))
    variant = phase % 3
    family = (
        p.families[number % family_count] if number % family_count < len(p.families) else "model"
    )
    interval = p.intervals[(number // family_count) % len(p.intervals)]
    higher = next(
        (
            t
            for t in sorted(p.intervals, key=lambda t: market.INTERVAL_SECONDS[t])
            if market.INTERVAL_SECONDS[t] > market.INTERVAL_SECONDS[interval]
        ),
        None,
    )
    if family == "model":
        return interval, None
    component = {
        "family": family,
        "fast_window": (5, 10, 15)[variant],
        "slow_window": (20, 40, 60)[variant],
        "entry_z": (1.5, 2.0, 2.5)[variant],
        "activity_ratio": ("1", "1.1", "1.25")[variant],
        "weight": "1",
    }
    definition = {
        "mode": "single",
        "components": [component],
        "context_interval": higher if p.multi_timeframe and (number + phase) % 2 else None,
    }
    if p.mixtures and (number + phase) % 3 == 2:
        other = next((f for f in p.families if f != family), None)
        if other:
            definition["mode"] = (
                "regime_switch" if higher and p.multi_timeframe and number % 2 == 0 else "blend"
            )
            definition["context_interval"] = higher if p.multi_timeframe else None
            if definition["mode"] == "regime_switch":
                if "mean_reversion" in p.families:
                    other = "mean_reversion" if family != "mean_reversion" else other
                else:
                    definition["mode"] = "blend"
            definition["components"] = [
                {**component, "weight": "0.5"},
                {"family": other, "fast_window": 5, "slow_window": 20, "weight": "0.5"},
            ]
    return interval, ResearchStrategy.model_validate(definition).model_dump(mode="json")


def start(db, owner, body, *, scheduled=False):
    request = body.model_dump(mode="json")
    previous = journal.repeated(db, owner, "campaign", body.request_id, request)
    if previous:
        return journal.view(previous)
    settings = head(db, owner)
    if not settings:
        raise HTTPException(422, "Save strategy discovery settings before starting a cycle")
    p = parameters(settings)
    if scheduled and not p.enabled:
        raise HTTPException(409, "Automatic discovery is paused")
    active = db.scalar(
        select(DiscoveryRecord.id)
        .where(
            DiscoveryRecord.owner_id == owner.id,
            DiscoveryRecord.kind == "campaign",
            DiscoveryRecord.payload["status"]
            .as_string()
            .in_(("queued", "running", "waiting", "paused")),
        )
        .limit(1)
    )
    if active:
        raise HTTPException(409, "Finish, resume or cancel the existing discovery cycle first")
    allowance = p.daily_hypothesis_limit - today_count(db, owner, DiscoveryRecord, kind="trial")
    if allowance <= 0:
        raise HTTPException(
            429, "Today's hypothesis allowance is exhausted; existing evidence remains available"
        )
    count = min(allowance, p.daily_hypothesis_limit)
    total = db.scalar(
        select(func.count())
        .select_from(DiscoveryRecord)
        .where(DiscoveryRecord.owner_id == owner.id, DiscoveryRecord.kind == "trial")
    )
    as_of = now().astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    inputs = {
        "settings_id": settings.id,
        "settings_checksum": settings.checksum,
        "parameters": p.model_dump(mode="json"),
        "as_of": as_of.isoformat(),
        "first_trial_number": total,
        "source_checksum": source_checksum(),
        "scheduled": scheduled,
        "source_keys": {
            key: history_store.source_key(market.instrument(db, key), history_store.connections(db))
            for key in p.instrument_ids
        },
    }
    row = journal.append(
        db,
        owner,
        "campaign",
        body.request_id,
        request,
        inputs=inputs,
        status="queued",
        planned_hypotheses=count,
        error=None,
        trial_ids=[],
        ai_ideas=None,
    )
    plans = []
    for offset in range(count if not p.ai_enabled else 0):
        number = total + offset
        interval, definition = template(p, number)
        instrument_id = p.instrument_ids[
            (number // max(1, len(p.intervals))) % len(p.instrument_ids)
        ]
        plans.append(
            {"instrument_id": instrument_id, "interval": interval, "definition": definition}
        )
    freeze_trials(db, owner, row, plans, "fixed_research_template")
    db.commit()
    return journal.view(row)


def freeze_trials(db, owner, row, plans, origin):
    for offset, plan in enumerate(plans):
        trial_inputs = {
            **plan,
            "explanation": explanation(plan["definition"]),
            "origin": origin,
            "papers": discovery_signals.PAPERS,
        }
        if plan.get("hypothesis"):
            trial_inputs["explanation"] = {
                **trial_inputs["explanation"],
                "entry": plan["hypothesis"],
            }
        trial = journal.append(
            db,
            owner,
            "trial",
            identity(row.id + f":trial:{offset}"),
            {"reason": "Freeze strategy hypothesis before observing experimental results"},
            inputs=trial_inputs,
            parent_id=row.id,
            status="queued",
            jobs={},
            analysis=None,
            error=None,
        )
        row.payload = {**row.payload, "trial_ids": [*row.payload["trial_ids"], trial.id]}
        row.checksum = paper.checksum(row.payload)


def source_checksum():
    from pathlib import Path

    root = Path(__file__).parent
    return paper.checksum(
        {
            name: __import__("hashlib").sha256((root / name).read_bytes()).hexdigest()
            for name in (
                "discovery.py",
                "discovery_signals.py",
                "quant_features.py",
                "quant_runtime.py",
                "history_store.py",
                "discovery_contracts.py",
                "discovery_ai.py",
                "quant_contracts.py",
                "quant_research.py",
                "market_calendars.py",
                "data_quality.py",
                "../runners/lean_algorithm.py",
                "../runners/qlib_runner.py",
            )
        }
    )


def materialize(db, owner, campaign, instrument_id, interval):
    p = DiscoveryParameters.model_validate(campaign.payload["input"]["parameters"])
    end = quant_features.stamp(campaign.payload["input"]["as_of"])
    start_at = end - timedelta(days=p.lookback_days)
    # Each immutable study remains bounded to 5,000 bars; the saved library can be much larger.
    start_at = max(start_at, end - timedelta(seconds=market.INTERVAL_SECONDS[interval] * 6500))
    spec = market.instrument(db, instrument_id)
    start_at, end = history_store.bounds(spec, interval, start_at, end)
    key = campaign.payload["input"]["source_keys"][instrument_id]
    if key != history_store.source_key(spec, history_store.connections(db)):
        raise HTTPException(409, "Market-data account changed; register a new discovery cycle")
    value = history_store.read(
        db, owner, instrument_id, interval, start_at, end, key=key, require_complete=False
    )
    if value["missing_ranges"]:
        request_id = identity(campaign.id + f":history:{instrument_id}:{interval}")
        loading = history_store.request_load(
            db,
            owner,
            HistoryLoad(
                request_id=request_id,
                instrument_id=instrument_id,
                interval=interval,
                start=start_at,
                end=end,
            ),
        )
        if loading.payload["status"] == "failed":
            raise HTTPException(
                409,
                "History load failed: " + str(loading.payload.get("error", "inspect saved gaps")),
            )
        return None
    candles = value["candles"][-4500:]
    if len(candles) < 300:
        raise HTTPException(
            422, "At least 300 complete saved bars are required for chronological research splits"
        )
    request = HistoricalDatasetCreate(
        request_id=identity(campaign.id + f":dataset:{instrument_id}:{interval}"),
        name=f"Discovery history {spec.symbol} {interval}",
        instrument_id=instrument_id,
        interval=interval,
        start=candles[0]["open_time"],
        end=candles[-1]["close_time"],
        reason="Use saved OANDA data for a frozen discovery cycle",
    )
    return quant_research.historical_dataset(db, owner, request)


def dataset_for(db, owner, view):
    return research.owned(db, owner, view.id, "dataset")


def enqueue_stage(db, owner, campaign, trial, stage, dataset, context=None, model_id=None):
    journal.lock(db, owner)
    db.refresh(campaign)
    db.refresh(trial)
    if (
        campaign.payload["status"] in journal.TERMINAL | {"paused"}
        or trial.payload["status"] in journal.TERMINAL
    ):
        return False
    p = DiscoveryParameters.model_validate(campaign.payload["input"]["parameters"])
    current = parameters(head(db, owner))
    if today_count(db, owner, QuantRecord, kind="quant_job") >= min(
        p.daily_experiment_limit, current.daily_experiment_limit
    ):
        journal.update(
            campaign,
            status="waiting",
            error="Daily experiment ceiling reached; this cycle resumes on the next UTC day",
            next_check_at=(
                now().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            ).isoformat(),
        )
        db.commit()
        return False
    source = dataset_for(db, owner, dataset)
    bars = source.payload["candles"]
    walking = stage in {"walk_model", "walk"}
    confirming = trial.kind == "confirmation"
    training_fraction, validation_fraction, ending_fraction = (
        (0.4, 0.6, 0.8) if walking else (0.6, 0.8, 1.0 if confirming else 0.9)
    )
    is_model = stage in {"model", "walk_model"}
    ending = (
        bars[-1]["close_time"]
        if ending_fraction == 1
        else bars[int(len(bars) * ending_fraction)]["close_time"]
    )
    request = ExperimentCreate(
        request_id=identity(trial.id + ":" + stage),
        dataset_id=dataset.id,
        name=f"{trial.payload['input']['explanation']['title']} · {stage}"[:120],
        hypothesis=trial.payload["input"]["explanation"]["entry"],
        reason="Strategy discovery: frozen chronological comparison",
        engine="qlib" if is_model else "lean",
        train_end=bars[int(len(bars) * training_fraction)]["close_time"],
        validation_end=bars[int(len(bars) * validation_fraction)]["close_time"],
        test_end=ending,
        evaluation_start=trial.payload["input"].get("holdout_start")
        if confirming and not is_model
        else None,
        slippage_bps=p.stressed_slippage_bps if stage == "stress" else p.slippage_bps,
        fee_bps=p.fee_bps,
        starting_balance="10000",
        allocation_percent="50",
        asset_costs={
            "asset_type": "fx",
            "spread_bps": "0",
            "financing_daily_bps": p.financing_daily_bps,
        },
        model_run_id=model_id,
        research_strategy=trial.payload["input"]["definition"] if not is_model else None,
        context_dataset_id=context.id if context and not is_model else None,
    )
    # Persist the identity before handing off; a lost response is reconciled through this original ID.
    ids = {**trial.payload["jobs"], stage: str(request.request_id)}
    journal.update(
        trial,
        status="waiting",
        jobs=ids,
        dataset_id=dataset.id,
        dataset_checksum=dataset.checksum,
        train_end=request.train_end.isoformat(),
        validation_end=request.validation_end.isoformat(),
        test_end=request.test_end.isoformat(),
    )
    db.commit()
    result = quant_research.enqueue(db, owner, request)
    journal.update(trial, jobs={**trial.payload["jobs"], stage: result.id})
    db.commit()
    return True


def resolve_job(db, owner, reference):
    row = db.get(QuantRecord, reference)
    if not row:
        row = db.scalar(
            select(QuantRecord).where(
                QuantRecord.owner_id == owner.id,
                QuantRecord.kind == "quant_job",
                QuantRecord.request_id == reference,
            )
        )
    if row:
        if row.owner_id != owner.id:
            raise HTTPException(404, "Research engine job does not belong to this owner")
        quant_research.view(row)
    return row


def completed_trades(fills):
    quantity, completed = Decimal(0), 0
    for fill in fills:
        previous = quantity
        quantity += Decimal(str(fill["quantity"]))
        if quantity == 0 and previous > 0:
            completed += 1
    return completed


def analyse(p, baseline, stress):
    result, stressed = baseline.payload["result"], stress.payload["result"]
    count = completed_trades(result.get("fills", []))
    criteria = [
        {
            "name": "Closed simulated trades",
            "passed": count >= p.minimum_closed_trades,
            "observed": count,
            "required": p.minimum_closed_trades,
        },
        {
            "name": "Net return after baseline costs",
            "passed": Decimal(str(result["return_percent"])) >= Decimal(p.minimum_return_percent),
            "observed": result["return_percent"],
            "required": p.minimum_return_percent,
        },
        {
            "name": "Net return under stressed costs",
            "passed": Decimal(str(stressed["return_percent"])) >= Decimal(p.minimum_return_percent),
            "observed": stressed["return_percent"],
            "required": p.minimum_return_percent,
        },
        {
            "name": "Maximum sampled drawdown",
            "passed": Decimal(str(result["max_drawdown_percent"]))
            <= Decimal(p.maximum_drawdown_percent),
            "observed": result["max_drawdown_percent"],
            "required": p.maximum_drawdown_percent,
        },
    ]
    return {
        "criteria": criteria,
        "screen_passed": all(c["passed"] for c in criteria),
        "return_percent": result["return_percent"],
        "stressed_return_percent": stressed["return_percent"],
        "max_drawdown_percent": result["max_drawdown_percent"],
        "closed_trades": count,
        "daily_returns": discovery_signals.daily_returns(result.get("equity_curve", [])),
        "native_qualification": False,
        "requires_fresh_final_holdout": True,
        "limitations": [
            "Research screening cannot authorize orders or substitute for exact native qualification.",
            "The displayed test window is inspected screening evidence, not a fresh final holdout.",
            "Candle snapshots do not establish intrabar stops, liquidity or live fill quality.",
        ],
    }


def advance_trial(db, owner, campaign, trial):
    inputs = trial.payload["input"]
    dataset = materialize(db, owner, campaign, inputs["instrument_id"], inputs["interval"])
    if dataset is None:
        return False
    context = None
    if inputs["definition"] and inputs["definition"].get("context_interval"):
        context = materialize(
            db, owner, campaign, inputs["instrument_id"], inputs["definition"]["context_interval"]
        )
        if context is None:
            return False
    jobs = {stage: resolve_job(db, owner, key) for stage, key in trial.payload["jobs"].items()}
    resolved = {
        stage: job.id if job else trial.payload["jobs"][stage] for stage, job in jobs.items()
    }
    if resolved != trial.payload["jobs"]:
        journal.update(trial, jobs=resolved)
        db.commit()
    p = DiscoveryParameters.model_validate(campaign.payload["input"]["parameters"])
    stages = []
    if p.walk_forward and trial.kind == "trial":
        stages.extend((["walk_model"] if inputs["definition"] is None else []) + ["walk"])
    stages.extend((["model"] if inputs["definition"] is None else []) + ["baseline", "stress"])
    for stage in stages:
        job = jobs.get(stage)
        if not job:
            return enqueue_stage(
                db,
                owner,
                campaign,
                trial,
                stage,
                dataset,
                context,
                jobs["walk_model"].id
                if stage == "walk" and jobs.get("walk_model")
                else jobs["model"].id
                if stage not in {"model", "walk_model"} and jobs.get("model")
                else None,
            )
        if job.payload["status"] in {"failed", "cancelled"}:
            journal.update(
                trial,
                status="failed",
                error=job.payload.get("error") or "Research engine job was cancelled",
            )
            db.commit()
            return True
        if job.payload["status"] != "completed":
            return False
    analysis = analyse(p, jobs["baseline"], jobs["stress"])
    if jobs.get("walk"):
        earlier = jobs["walk"].payload["result"]
        criterion = {
            "name": "Earlier walk-forward window net return",
            "passed": Decimal(str(earlier["return_percent"])) >= Decimal(p.minimum_return_percent),
            "observed": earlier["return_percent"],
            "required": p.minimum_return_percent,
        }
        analysis["criteria"].append(criterion)
        analysis["walk_forward"] = {
            "return_percent": earlier["return_percent"],
            "max_drawdown_percent": earlier["max_drawdown_percent"],
            "job_id": jobs["walk"].id,
        }
        analysis["daily_returns"] = (
            discovery_signals.daily_returns(earlier.get("equity_curve", []))
            + analysis["daily_returns"]
        )
        analysis["screen_passed"] = analysis["screen_passed"] and criterion["passed"]
    if trial.kind == "confirmation":
        analysis["requires_fresh_final_holdout"] = False
        analysis["confirmation"] = True
        analysis["limitations"][1] = (
            "This final window was reserved after the strategy was selected; it must not be reused as fresh evidence."
        )
    journal.update(
        trial,
        status="screen_passed" if analysis["screen_passed"] else "rejected",
        analysis=analysis,
        finished_at=now().isoformat(),
        error=None,
    )
    db.commit()
    return True


def process_one(db):
    campaign = db.scalar(
        select(DiscoveryRecord)
        .where(
            DiscoveryRecord.kind == "campaign",
            DiscoveryRecord.payload["status"].as_string().in_(("queued", "running", "waiting")),
        )
        .order_by(DiscoveryRecord.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not campaign:
        return schedule_one(db)
    journal.checked(campaign)
    retry = campaign.payload.get("next_check_at")
    if retry and quant_features.stamp(retry) > now():
        return False
    owner = db.get(Owner, campaign.owner_id)
    journal.lock(db, owner)
    try:
        if campaign.payload["input"]["source_checksum"] != source_checksum():
            raise HTTPException(
                409, "Research source changed; cancel this cycle and register a new one"
            )
        p = DiscoveryParameters.model_validate(campaign.payload["input"]["parameters"])
        current = parameters(head(db, owner))
        if not current.enabled and campaign.payload["input"]["scheduled"]:
            journal.update(
                campaign, status="paused", error="Automatic discovery is paused in Settings"
            )
            db.commit()
            return True
        if p.ai_enabled and campaign.payload.get("ai_ideas") is None:
            from .discovery_ai import generate

            result = generate(db, owner, campaign, p)
            journal.lock(db, owner)
            db.refresh(campaign)
            if campaign.payload["status"] in journal.TERMINAL | {"paused"}:
                return False
            remaining = min(p.daily_hypothesis_limit, current.daily_hypothesis_limit) - today_count(
                db, owner, DiscoveryRecord, kind="trial"
            )
            if remaining < len(result["ideas"]):
                raise HTTPException(
                    429,
                    "The hypothesis ceiling changed; generated ideas remain saved in the usage record",
                )
            freeze_trials(db, owner, campaign, result["ideas"], "ai:" + result["usage_id"])
            journal.update(campaign, ai_ideas=result)
            db.commit()
        journal.update(campaign, status="running", error=None, next_check_at=None)
        db.commit()
        for trial_id in campaign.payload["trial_ids"]:
            trial = journal.owned(db, owner, trial_id, "trial")
            if trial.payload["status"] not in journal.TERMINAL:
                return advance_trial(db, owner, campaign, trial)
        trials = [journal.owned(db, owner, i, "trial") for i in campaign.payload["trial_ids"]]
        if any(t.payload["status"] == "screen_passed" for t in trials):
            if advance_confirmation(db, owner, campaign, trials):
                return True
        prior = list(
            db.scalars(
                select(DiscoveryRecord).where(
                    DiscoveryRecord.owner_id == owner.id, DiscoveryRecord.kind == "trial"
                )
            )
        )
        sharpes = [
            discovery_signals.sharpe(
                journal.checked(t).payload.get("analysis", {}).get("daily_returns", [])
            )
            for t in prior
            if t.payload.get("analysis")
        ]
        diagnostics = {
            t.id: discovery_signals.selection_diagnostic(
                t.payload["analysis"]["daily_returns"], sharpes
            )
            for t in trials
            if t.payload.get("analysis")
        }
        journal.update(
            campaign,
            status="completed",
            finished_at=now().isoformat(),
            selection_diagnostics=diagnostics,
            summary={
                "trials": len(trials),
                "screen_passed": sum(t.payload["status"] == "screen_passed" for t in trials),
                "rejected": sum(t.payload["status"] == "rejected" for t in trials),
                "failed": sum(t.payload["status"] == "failed" for t in trials),
                "trading_qualification": False,
                "confirmation_id": campaign.payload.get("confirmation_id"),
            },
        )
        db.commit()
        return True
    except (HTTPException, ValueError, ArithmeticError, KeyError, TypeError, OSError) as error:
        message = (
            error.detail
            if isinstance(error, HTTPException) and isinstance(error.detail, str)
            else "Research inputs could not be verified"
        )
        journal.update(
            campaign,
            status="waiting",
            error=message[:500],
            next_check_at=(now() + timedelta(seconds=30)).isoformat(),
        )
        db.commit()
        return False


def advance_confirmation(db, owner, campaign, trials):
    """Choose once, then evaluate only data beyond all recorded prior engine-test cutoffs."""
    if campaign.payload.get("confirmation_id"):
        confirmation = journal.owned(db, owner, campaign.payload["confirmation_id"], "confirmation")
        if confirmation.payload["status"] not in journal.TERMINAL:
            advance_trial(db, owner, campaign, confirmation)
            return True
        return False
    selected = max(
        (t for t in trials if t.payload["status"] == "screen_passed"),
        key=lambda t: (float(t.payload["analysis"]["stressed_return_percent"]), t.id),
    )
    source = research.owned(db, owner, selected.payload["dataset_id"], "dataset")
    bars = source.payload["candles"]
    nominal = quant_features.stamp(bars[int(len(bars) * 0.9)]["close_time"])
    earlier = list(
        db.scalars(
            select(QuantRecord)
            .join(research.ResearchRecord, QuantRecord.parent_id == research.ResearchRecord.id)
            .where(
                QuantRecord.owner_id == owner.id,
                research.ResearchRecord.payload["instrument_id"].as_string()
                == selected.payload["input"]["instrument_id"],
                QuantRecord.kind == "quant_job",
            )
            .limit(10001)
        )
    )
    if len(earlier) > 10000:
        raise HTTPException(409, "Research exposure inventory exceeded its verification bound")
    last_seen = max(
        [nominal, *[quant_features.stamp(q.payload["request"]["test_end"]) for q in earlier]]
    )
    remaining = [b for b in bars if quant_features.stamp(b["open_time"]) >= last_seen]
    holdout = journal.append(
        db,
        owner,
        "holdout",
        identity(campaign.id + ":holdout"),
        {"reason": "Reserve final data after freezing the selected strategy"},
        inputs={
            "selected_trial_id": selected.id,
            "start": last_seen.isoformat(),
            "end": bars[-1]["close_time"],
            "dataset_checksum": source.checksum,
            "earlier_job_checksums": [q.checksum for q in earlier],
            "limitation": "Fresh relative to recorded engine test cutoffs; private human or external inspection cannot be independently verified",
        },
    )
    confirmation = journal.append(
        db,
        owner,
        "confirmation",
        identity(campaign.id + ":confirmation"),
        {"reason": "Evaluate the selected frozen research strategy on reserved final data"},
        parent_id=campaign.id,
        inputs={
            **selected.payload["input"],
            "selected_trial_id": selected.id,
            "holdout_start": last_seen.isoformat(),
            "holdout_id": holdout.id,
        },
        status="queued" if len(remaining) >= 30 else "rejected",
        jobs={},
        analysis=None,
        error=None
        if len(remaining) >= 30
        else "Insufficient uninspected final data beyond the recorded research cutoffs",
    )
    journal.update(campaign, confirmation_id=confirmation.id)
    db.commit()
    return True


def schedule_one(db):
    for owner in db.scalars(select(Owner)):
        settings = head(db, owner)
        p = parameters(settings)
        if not settings or not p.enabled:
            continue
        latest = db.scalar(
            select(DiscoveryRecord)
            .where(DiscoveryRecord.owner_id == owner.id, DiscoveryRecord.kind == "campaign")
            .order_by(DiscoveryRecord.created_at.desc())
            .limit(1)
        )
        due = (
            (latest.created_at + timedelta(hours=p.cadence_hours))
            if latest
            else settings.created_at
        )
        if now() < due:
            continue
        try:
            start(
                db,
                owner,
                DiscoveryStart(
                    request_id=identity(settings.id + ":" + due.isoformat()),
                    reason="Scheduled strategy exploration",
                ),
                scheduled=True,
            )
        except HTTPException:
            db.rollback()
            continue
        return True
    return False


def control(db, owner, identity_value, action):
    row = journal.owned(db, owner, identity_value)
    journal.lock(db, owner)
    if row.kind not in {"campaign", "history_sync"} or action not in {"pause", "resume", "cancel"}:
        raise HTTPException(422, "Choose a research cycle or history load and a supported action")
    if row.payload.get("status") in journal.TERMINAL:
        raise HTTPException(409, "This research work has already finished")
    if action == "resume" and row.payload.get("status") != "paused":
        raise HTTPException(409, "Only paused research work can be resumed")
    if row.kind == "campaign" and action in {"pause", "cancel"}:
        for trial_id in [
            *row.payload["trial_ids"],
            *([row.payload["confirmation_id"]] if row.payload.get("confirmation_id") else []),
        ]:
            trial = journal.owned(db, owner, trial_id)
            for ref in trial.payload["jobs"].values():
                job = resolve_job(db, owner, ref)
                if job and job.payload["status"] in {"queued", "running"} and action == "cancel":
                    quant_research.cancel(db, owner, job.id)
            if action == "cancel" and trial.payload["status"] not in journal.TERMINAL:
                journal.update(trial, status="cancelled", finished_at=now().isoformat())
    journal.update(
        row,
        status={"pause": "paused", "resume": "queued", "cancel": "cancelled"}[action],
        error=None,
        next_check_at=None,
    )
    journal.append(
        db,
        owner,
        "event",
        identity(str(identity_value) + ":" + action + ":" + now().isoformat()),
        {"reason": "Owner " + action + " research work", "action": action},
        parent_id=row.id,
    )
    db.commit()
    return journal.view(row)


def state(db, owner, offset=0):
    from .discovery_ai import charged

    settings = head(db, owner)
    rows = list(
        db.scalars(
            select(DiscoveryRecord)
            .where(
                DiscoveryRecord.owner_id == owner.id,
                DiscoveryRecord.kind.in_(("campaign", "trial", "confirmation", "history_sync")),
            )
            .order_by(DiscoveryRecord.created_at.desc(), DiscoveryRecord.id.desc())
            .offset(offset)
            .limit(101)
        )
    )
    worker = db.get(WorkerStatus, "strategy_discovery")
    return {
        "revision": settings.payload["revision"] if settings else 0,
        "parameters": parameters(settings).model_dump(mode="json"),
        "worker_ready": bool(
            worker and timedelta(0) <= now() - worker.heartbeat_at < timedelta(seconds=90)
        ),
        "engines": quant_research.state(db, owner).engines,
        "library": history_store.library(db, owner),
        "records": [journal.view(r).model_dump(mode="json") for r in rows[:100]],
        "offset": offset,
        "has_more": len(rows) > 100,
        "today": {
            "hypotheses": today_count(db, owner, DiscoveryRecord, kind="trial"),
            "engine_jobs": today_count(db, owner, QuantRecord, kind="quant_job"),
            "ai_charged_usd": str(charged(db, owner)),
        },
        "papers": discovery_signals.PAPERS,
        "authority": "research_only",
    }
