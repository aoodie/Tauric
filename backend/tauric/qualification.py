"""Separate training, held-out and cost-stress evidence. No trading/promotion writes."""

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select

from . import (
    analysis,
    asset_costs,
    data_quality,
    market,
    market_calendars,
    native_strategy_assessment,
    outcomes,
    paper,
    portfolios,
    qualification_contracts,
    replay_engine,
    research,
    research_contracts,
    services,
    strategies,
    strategy_contracts,
    strategy_rules,
)
from .market_contracts import AnalysisRequest
from .model_profiles import connection_head, load_profiles
from .models import AnalysisArtifact, Owner, PaperAccount, now
from .qualification_contracts import NativeStrategyVerdict, QualificationState, QualificationView
from .qualification_models import (
    QualificationAdmission,
    QualificationEvaluation,
    QualificationProtocol,
    QualificationReport,
)
from .research_contracts import ReplayParameters
from .research_models import ResearchRecord

ADAPTER_VERSION = "tauric_exact_strategy_assessment_v1"
MODULES = (
    replay_engine,
    outcomes,
    research_contracts,
    strategy_contracts,
    strategy_rules,
    asset_costs,
    data_quality,
    market_calendars,
    native_strategy_assessment,
    qualification_contracts,
)


def checked(row):
    if not row or paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Qualification evidence integrity failed.")
    return row


def owned(db, owner, model, key):
    row = db.get(model, str(key))
    if not row or row.owner_id != owner.id:
        raise HTTPException(404, "Qualification evidence not found.")
    return checked(row)


def repeat(db, owner, model, body):
    db.scalar(select(Owner.id).where(Owner.id == owner.id).with_for_update())
    old = db.scalar(
        select(model).where(model.owner_id == owner.id, model.request_id == str(body.request_id))
    )
    if old and checked(old).payload["request"] != body.model_dump(mode="json"):
        raise HTTPException(409, "This request ID belongs to different qualification inputs.")
    return old


def sources():
    return {m.__name__: Path(m.__file__).read_text() for m in MODULES}


def account_policies(db, owner, portfolio):
    result = []
    for account in portfolio.accounts:
        acct = db.get(PaperAccount, account["id"])
        if not acct or acct.owner_id != owner.id:
            raise HTTPException(409, "Linked account is unavailable.")
        limits, row = paper.policy(db, acct)
        result.append(
            {
                "account_id": acct.id,
                "policy_record_id": row.id,
                "checksum": row.checksum,
                "policy": limits.model_dump(mode="json"),
            }
        )
    return sorted(result, key=lambda r: r["account_id"])


def risk_check(db, owner, portfolio, parameters):
    for account in account_policies(db, owner, portfolio):
        limit = account["policy"]["risk_percent"]
        if Decimal(parameters.risk_percent) > Decimal(limit):
            raise HTTPException(
                422, "Experiment risk exceeds a current linked account per-trade limit."
            )


def freeze(db, owner, body):
    previous = repeat(db, owner, QualificationProtocol, body)
    if previous:
        return view(db, owner, previous)
    portfolio = portfolios.view(db, owner, body.portfolio_id)
    if not portfolio.preferences:
        raise HTTPException(422, "Save the portfolio mandate first.")
    strategy = strategies.view(strategies.version(db, owner, body.strategy_version_id))
    dataset = research.owned(db, owner, body.dataset_id, "dataset")
    asset_type = dataset.payload.get("asset_type", "crypto_spot")
    costs = body.parameters.asset_costs
    if (
        asset_type not in {"crypto_spot", "fx"}
        or (asset_type == "crypto_spot" and costs)
        or (asset_type == "fx" and (not costs or costs.asset_type != "fx"))
    ):
        raise HTTPException(
            422,
            "Qualification requires supported crypto spot or Forex data with matching explicit costs.",
        )
    context = dataset.payload.get("data_context")
    if (
        asset_type == "fx"
        and context
        and context["price_side"] == "bid_ask"
        and Decimal(costs.spread_bps) != 0
    ):
        raise HTTPException(422, "Bid/ask data includes spread; set synthetic spread to zero.")
    rules = strategy.parameters.rules
    if not rules or rules.interval != dataset.payload["interval"]:
        raise HTTPException(
            422, "Qualification requires saved executable rules matching the dataset interval."
        )
    risk_check(db, owner, portfolio, body.parameters)
    candles = research.validate_bars(
        dataset.payload["candles"], dataset.payload["interval"], dataset.payload.get("data_context")
    )
    start = body.training_bars + body.embargo_bars
    minimum = max(rules.entry_lookback, rules.exit_lookback) + 2
    if body.training_bars < minimum or len(candles) - start < max(
        minimum, body.thresholds.minimum_holdout_bars
    ):
        raise HTTPException(
            422,
            "Both separate windows need sufficient warmup/fill bars; holdout must satisfy the configured coverage.",
        )
    snapshot = sources()
    payload = {
        "request": body.model_dump(mode="json"),
        "portfolio_version_id": portfolio.version_id,
        "portfolio_revision": portfolio.revision,
        "portfolio_preferences": portfolio.preferences.model_dump(mode="json"),
        "account_policies": account_policies(db, owner, portfolio),
        "strategy": strategy.model_dump(mode="json"),
        "dataset_checksum": dataset.checksum,
        "dataset_context": {
            k: dataset.payload.get(k)
            for k in (
                "source",
                "availability",
                "quote_currency",
                "data_context",
                "quality",
                "provenance",
            )
        },
        "instrument_id": dataset.payload["instrument_id"],
        "interval": dataset.payload["interval"],
        "training_range": [candles[0]["open_time"], candles[body.training_bars - 1]["close_time"]],
        "holdout_range": [candles[start]["open_time"], candles[-1]["close_time"]],
        "holdout_bars": len(candles) - start,
        "engine_sources": snapshot,
        "engine_checksum": paper.checksum(snapshot),
        "assumptions": (
            research.asset_assumptions(
                market.instrument(db, dataset.payload["instrument_id"]), context
            )
            + [
                "Saved breakout rules use provider tick activity, not exchange volume; gap qualification uses expected trading sessions."
            ]
            if asset_type == "fx"
            else research.BREAKOUT_ASSUMPTIONS
        )
        + [
            "Windows start flat, warm up independently and liquidate at their last close; embargo bars are not traded.",
            "Held out from this registered protocol. Raw data may have been known to the owner; prior knowledge and external optimisation are not verifiable.",
        ],
        "native_assessment_adapter": ADAPTER_VERSION,
        "execution_authority": False,
    }
    row = QualificationProtocol(
        owner_id=owner.id,
        portfolio_id=portfolio.id,
        portfolio_version_id=portfolio.version_id,
        strategy_version_id=strategy.id,
        dataset_id=dataset.id,
        request_id=str(body.request_id),
        name=body.name,
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "qualification.protocol_frozen",
        "qualification",
        row.id,
        body.reason,
        {
            "checksum": row.checksum,
            "strategy_version_id": strategy.id,
            "execution_authority": False,
        },
    )
    db.commit()
    return view(db, owner, row)


def overlaps(left, right):
    return datetime.fromisoformat(left[0]) <= datetime.fromisoformat(
        right[1]
    ) and datetime.fromisoformat(right[0]) <= datetime.fromisoformat(left[1])


def exposures(db, owner, protocol):
    result = []
    frozen = protocol.payload
    # Track same-time data even when it was re-imported under a different dataset identifier.
    for row in db.scalars(
        select(ResearchRecord).where(
            ResearchRecord.owner_id == owner.id, ResearchRecord.kind == "run"
        )
    ):
        checked(row)
        data = research.owned(db, owner, row.parent_id, "dataset")
        candles = data.payload["candles"]
        if (
            data.payload["instrument_id"] == frozen["instrument_id"]
            and data.payload["interval"] == frozen["interval"]
            and overlaps(
                [candles[0]["open_time"], candles[-1]["close_time"]], frozen["holdout_range"]
            )
        ):
            result.append(
                {"kind": "ordinary_replay", "id": row.id, "created_at": row.created_at.isoformat()}
            )
    for evaluation in db.scalars(
        select(QualificationEvaluation).where(
            QualificationEvaluation.owner_id == owner.id,
            QualificationEvaluation.protocol_id != protocol.id,
        )
    ):
        checked(evaluation)
        other = owned(db, owner, QualificationProtocol, evaluation.protocol_id)
        if (
            other.payload["instrument_id"] == frozen["instrument_id"]
            and other.payload["interval"] == frozen["interval"]
            and overlaps(other.payload["holdout_range"], frozen["holdout_range"])
        ):
            result.append(
                {
                    "kind": "previous_holdout_evaluation",
                    "id": evaluation.id,
                    "created_at": evaluation.created_at.isoformat(),
                }
            )
        elif (
            other.payload["instrument_id"] == frozen["instrument_id"]
            and other.payload["interval"] == frozen["interval"]
            and overlaps(other.payload["training_range"], frozen["holdout_range"])
        ):
            result.append(
                {
                    "kind": "previous_training_evaluation",
                    "id": evaluation.id,
                    "created_at": evaluation.created_at.isoformat(),
                }
            )
    return result


def evaluate(db, owner, key, body):
    old = repeat(db, owner, QualificationEvaluation, body)
    protocol = owned(db, owner, QualificationProtocol, key)
    if old:
        if old.protocol_id != protocol.id:
            raise HTTPException(409, "This evaluation belongs to another protocol.")
        return view(db, owner, protocol)
    if db.scalar(
        select(QualificationEvaluation.id).where(QualificationEvaluation.protocol_id == protocol.id)
    ):
        raise HTTPException(
            409,
            "This frozen protocol was evaluated already. Read its saved results; do not optimise against its holdout.",
        )
    frozen = protocol.payload
    if sources() != frozen["engine_sources"]:
        raise HTTPException(
            409, "Replay implementation changed. Freeze a new protocol; old evidence is preserved."
        )
    portfolio = portfolios.view(db, owner, protocol.portfolio_id)
    if portfolio.version_id != protocol.portfolio_version_id:
        raise HTTPException(409, "Portfolio mandate changed. Freeze a new protocol.")
    if account_policies(db, owner, portfolio) != frozen["account_policies"]:
        raise HTTPException(409, "Linked account policy changed. Freeze a new protocol.")
    p = ReplayParameters.model_validate(frozen["request"]["parameters"])
    risk_check(db, owner, portfolio, p)
    data = research.owned(db, owner, protocol.dataset_id, "dataset")
    if data.checksum != frozen["dataset_checksum"]:
        raise HTTPException(409, "Frozen dataset changed.")
    candles = research.validate_bars(
        data.payload["candles"], data.payload["interval"], data.payload.get("data_context")
    )
    request = frozen["request"]
    training = candles[: request["training_bars"]]
    holdout = candles[request["training_bars"] + request["embargo_bars"] :]
    saved = strategies.view(strategies.version(db, owner, protocol.strategy_version_id))
    train = replay_engine.simulate(
        training, p, saved, data_context=data.payload.get("data_context")
    ).model_dump(mode="json")
    test = replay_engine.simulate(
        holdout, p, saved, data_context=data.payload.get("data_context")
    ).model_dump(mode="json")
    stresses = []
    for case in request["stresses"]:
        stressed = ReplayParameters.model_validate(
            {
                **p.model_dump(mode="json"),
                "fee_bps": case["fee_bps"],
                "slippage_bps": case["slippage_bps"],
            }
        )
        stresses.append(
            {
                "scenario": case,
                "result": replay_engine.simulate(
                    holdout, stressed, saved, data_context=data.payload.get("data_context")
                ).model_dump(mode="json"),
            }
        )
    exposure = exposures(db, owner, protocol)
    payload = {
        "request": body.model_dump(mode="json"),
        "protocol_checksum": protocol.checksum,
        "dataset_checksum": data.checksum,
        "engine_checksum": frozen["engine_checksum"],
        "training": train,
        "holdout": test,
        "stresses": stresses,
        "holdout_exposure": exposure,
        "registered_holdout_unseen": not exposure,
        "prior_owner_knowledge": "not_verifiable",
        "selection": "exact version and costs frozen before results; training cannot select or change a strategy here",
        "execution_authority": False,
    }
    row = QualificationEvaluation(
        owner_id=owner.id,
        protocol_id=protocol.id,
        request_id=str(body.request_id),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "qualification.evaluated",
        "qualification",
        protocol.id,
        body.reason,
        {
            "evaluation_id": row.id,
            "checksum": row.checksum,
            "registered_holdout_unseen": not exposure,
        },
    )
    db.commit()
    return view(db, owner, protocol)


def evidence_input(protocol, evaluation):
    frozen = protocol.payload

    def summary(result):
        return {
            k: result[k]
            for k in (
                "metrics",
                "return_percent",
                "max_drawdown_percent",
                "bars",
                "skipped_entries",
            )
        }

    return {
        "adapter_version": ADAPTER_VERSION,
        "protocol_id": protocol.id,
        "protocol_checksum": protocol.checksum,
        "evaluation_id": evaluation.id,
        "evaluation_checksum": evaluation.checksum,
        "portfolio_id": protocol.portfolio_id,
        "portfolio_version_id": protocol.portfolio_version_id,
        "portfolio_preferences": frozen["portfolio_preferences"],
        "account_policies": frozen["account_policies"],
        "dataset_context": frozen["dataset_context"],
        "strategy": frozen["strategy"],
        "instrument_id": frozen["instrument_id"],
        "interval": frozen["interval"],
        "hypothesis": frozen["request"]["hypothesis"],
        "embargo_bars": frozen["request"]["embargo_bars"],
        "engine_checksum": frozen["engine_checksum"],
        "intended_mode": frozen["request"]["intended_mode"],
        "parameters": frozen["request"]["parameters"],
        "thresholds": frozen["request"]["thresholds"],
        "training_range": frozen["training_range"],
        "holdout_range": frozen["holdout_range"],
        "training": summary(evaluation.payload["training"]),
        "holdout": summary(evaluation.payload["holdout"]),
        "stresses": [
            {"scenario": s["scenario"], "result": summary(s["result"])}
            for s in evaluation.payload["stresses"]
        ],
        "holdout_exposure": evaluation.payload["holdout_exposure"],
        "prior_owner_knowledge": "not_verifiable",
        "assumptions": frozen["assumptions"],
        "scope": "Prospective research-design/applicability review of exact frozen strategy, not historical AI replay or a current scalp entry approval.",
        "execution_authority": False,
    }


def latest_evaluation(db, owner, protocol):
    row = db.scalar(
        select(QualificationEvaluation).where(QualificationEvaluation.protocol_id == protocol.id)
    )
    if not row:
        raise HTTPException(
            422, "Evaluate the frozen protocol before requesting assessment or a report."
        )
    if row.owner_id != owner.id:
        raise HTTPException(404, "Evaluation not found.")
    return checked(row)


def assess(db, owner, key, body):
    old = repeat(db, owner, QualificationAdmission, body)
    protocol = owned(db, owner, QualificationProtocol, key)
    evaluation = latest_evaluation(db, owner, protocol)
    if old:
        if old.protocol_id != protocol.id:
            raise HTTPException(409, "Assessment belongs to another protocol.")
        return view(db, owner, protocol)
    if (
        portfolios.view(db, owner, protocol.portfolio_id).version_id
        != protocol.portfolio_version_id
    ):
        raise HTTPException(409, "Portfolio mandate changed; assessment admission is blocked.")
    if sources() != protocol.payload["engine_sources"]:
        raise HTTPException(
            409, "Frozen replay or assessment implementation changed. Freeze a new protocol."
        )
    if (
        account_policies(db, owner, portfolios.view(db, owner, protocol.portfolio_id))
        != protocol.payload["account_policies"]
    ):
        raise HTTPException(409, "Linked account policy changed; assessment admission is blocked.")
    risk_check(
        db,
        owner,
        portfolios.view(db, owner, protocol.portfolio_id),
        ReplayParameters.model_validate(protocol.payload["request"]["parameters"]),
    )
    profiles, _ = load_profiles(db)
    model = profiles.get(body.profile)
    if not model or not model.configured():
        raise HTTPException(422, "Configure this AI connection first.")
    conn = connection_head(db, body.profile)
    evidence = evidence_input(protocol, evaluation)
    context = {
        "protocol_id": protocol.id,
        "protocol_checksum": protocol.checksum,
        "evaluation_id": evaluation.id,
        "evaluation_checksum": evaluation.checksum,
        "connection_revision": conn.revision if conn else 0,
        "assessment_input": evidence,
        "evidence_checksum": paper.checksum(evidence),
        "adapter_version": ADAPTER_VERSION,
    }
    cache_key = paper.checksum(
        {
            "context": context,
            "model": model.fingerprint(),
            "date": now().date().isoformat(),
            "settings": {
                k: services.setting_view(db, owner, "analysis." + k).effective_value
                for k in ("max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds")
            },
        }
    )
    reused = db.scalar(
        select(QualificationAdmission).where(
            QualificationAdmission.evaluation_id == evaluation.id,
            QualificationAdmission.context_key == cache_key,
        )
    )
    if reused:
        return view(db, owner, protocol)  # Also preserves failed jobs; no silent paid retry.
    if not db.scalar(select(func.pg_try_advisory_xact_lock(analysis.QUEUE_LOCK))):
        raise HTTPException(429, "Native admission is busy. Retry this request shortly.")
    job = analysis.enqueue(
        db,
        owner,
        AnalysisRequest(
            request_id=uuid5(NAMESPACE_URL, "qualification:" + cache_key),
            instrument_id=protocol.payload["instrument_id"],
            profile=body.profile,
        ),
        commit=False,
        qualification_context=context,
    )
    payload = {
        "request": body.model_dump(mode="json"),
        "context": context,
        "execution_authority": False,
    }
    db.add(
        QualificationAdmission(
            owner_id=owner.id,
            protocol_id=protocol.id,
            evaluation_id=evaluation.id,
            job_id=job.id,
            request_id=str(body.request_id),
            context_key=cache_key,
            payload=payload,
            checksum=paper.checksum(payload),
        )
    )
    services.audit(
        db,
        owner,
        "qualification.assessment_requested",
        "qualification",
        protocol.id,
        body.reason,
        {
            "job_id": job.id,
            "evidence_checksum": context["evidence_checksum"],
            "execution_authority": False,
        },
    )
    db.commit()
    return view(db, owner, protocol)


def validate_admission(db, job):
    context = job.request.get("qualification_context")
    if not context:
        if job.request.get("mode") == "strategy_qualification":
            raise ValueError("Exact strategy assessment lacks its frozen context")
        return
    owner = db.get(Owner, job.owner_id)
    protocol = owned(db, owner, QualificationProtocol, context["protocol_id"])
    evaluation = owned(db, owner, QualificationEvaluation, context["evaluation_id"])
    conn = connection_head(db, job.profile)
    profiles, _ = load_profiles(db)
    model = profiles.get(job.profile)
    if (
        job.request.get("mode") != "strategy_qualification"
        or context.get("adapter_version") != ADAPTER_VERSION
        or job.instrument_id != protocol.payload["instrument_id"]
        or context["protocol_checksum"] != protocol.checksum
        or context["evaluation_checksum"] != evaluation.checksum
        or evaluation.protocol_id != protocol.id
        or context["assessment_input"] != evidence_input(protocol, evaluation)
        or context["evidence_checksum"] != paper.checksum(context["assessment_input"])
        or context["connection_revision"] != (conn.revision if conn else 0)
        or portfolios.view(db, owner, protocol.portfolio_id).version_id
        != protocol.portfolio_version_id
        or sources() != protocol.payload["engine_sources"]
        or account_policies(db, owner, portfolios.view(db, owner, protocol.portfolio_id))
        != protocol.payload["account_policies"]
        or not model
        or not model.configured()
        or model.fingerprint() != job.request["profile_fingerprint"]
        or any(
            job.request["settings"][k]
            != services.setting_view(db, owner, "analysis." + k).effective_value
            for k in ("max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds")
        )
    ):
        raise ValueError("Exact strategy assessment context changed before execution")
    risk_check(
        db,
        owner,
        portfolios.view(db, owner, protocol.portfolio_id),
        ReplayParameters.model_validate(protocol.payload["request"]["parameters"]),
    )


def admissions(db, protocol):
    return [
        checked(row)
        for row in db.scalars(
            select(QualificationAdmission)
            .where(QualificationAdmission.protocol_id == protocol.id)
            .order_by(QualificationAdmission.created_at.desc())
        )
    ]


def report(db, owner, key, body):
    old = repeat(db, owner, QualificationReport, body)
    protocol = owned(db, owner, QualificationProtocol, key)
    evaluation = latest_evaluation(db, owner, protocol)
    if old:
        if old.protocol_id != protocol.id:
            raise HTTPException(409, "Report belongs to another protocol.")
        return view(db, owner, protocol)
    criteria = []

    def criterion(key, status, detail, observed=None, required=None):
        criteria.append(
            {
                "key": key,
                "status": status,
                "detail": detail,
                "observed": observed,
                "required": required,
            }
        )

    thresholds = protocol.payload["request"]["thresholds"]
    criterion(
        "holdout_coverage",
        "passed"
        if protocol.payload["holdout_bars"] >= thresholds["minimum_holdout_bars"]
        else "failed",
        "Separate cold-start window; embargo excluded.",
        protocol.payload["holdout_bars"],
        thresholds["minimum_holdout_bars"],
    )
    criterion(
        "registered_holdout_unseen",
        "passed" if evaluation.payload["registered_holdout_unseen"] else "failed",
        "Prior knowledge outside registered records is not verifiable.",
        evaluation.payload["holdout_exposure"],
    )
    for name, result in [("holdout", evaluation.payload["holdout"])] + [
        (s["scenario"]["name"], s["result"]) for s in evaluation.payload["stresses"]
    ]:
        for metric, actual, limit, passes in (
            (
                "closed_trades",
                result["metrics"]["closed_trades"],
                thresholds["minimum_closed_trades"],
                result["metrics"]["closed_trades"] >= thresholds["minimum_closed_trades"],
            ),
            (
                "net_return",
                result["return_percent"],
                thresholds["minimum_net_return_percent"],
                Decimal(result["return_percent"])
                >= Decimal(thresholds["minimum_net_return_percent"]),
            ),
            (
                "drawdown",
                result["max_drawdown_percent"],
                thresholds["maximum_drawdown_percent"],
                Decimal(result["max_drawdown_percent"])
                <= Decimal(thresholds["maximum_drawdown_percent"]),
            ),
        ):
            criterion(
                name + ":" + metric,
                "passed" if passes else "failed",
                "Measured after the frozen cost assumptions.",
                actual,
                limit,
            )
    current = portfolios.view(db, owner, protocol.portfolio_id)
    criterion(
        "portfolio_scope",
        "passed" if current.version_id == protocol.portfolio_version_id else "incomplete",
        "Assessment is bound to the frozen mandate revision.",
    )
    criterion(
        "account_policy_scope",
        "passed"
        if account_policies(db, owner, current) == protocol.payload["account_policies"]
        else "incomplete",
        "Exact strategy assessment is bound to the frozen linked account policy records.",
    )
    criterion(
        "engine_source",
        "passed" if sources() == protocol.payload["engine_sources"] else "incomplete",
        "Frozen replay and assessment implementations must match the current implementations.",
    )
    mode = protocol.payload["request"]["intended_mode"]
    try:
        risk_check(
            db,
            owner,
            current,
            ReplayParameters.model_validate(protocol.payload["request"]["parameters"]),
        )
        risk_status = "passed"
    except HTTPException:
        risk_status = "incomplete"
    criterion(
        "current_risk_limits",
        risk_status,
        "Experiment must still satisfy current linked account risk limits.",
    )
    criterion(
        "execution_mode",
        "passed" if mode == "research_comparison" else "incomplete",
        "OHLC replay supports research comparison. Forward execution/fill coverage for paper promotion and live scalping is not established.",
        mode,
        "research_comparison",
    )
    artifact = None
    decision = None
    detail = "No exact-version native strategy assessment is linked."
    for admission in admissions(db, protocol):
        job = analysis.get_job(db, owner, admission.job_id)
        if job.status != "succeeded":
            detail = f"Exact strategy assessment is {job.status}; no automatic retry."
            break
        candidate = db.scalar(
            select(AnalysisArtifact).where(
                AnalysisArtifact.job_id == job.id, AnalysisArtifact.owner_id == owner.id
            )
        )
        if (
            not candidate
            or hashlib.sha256(
                json.dumps(candidate.payload, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            != candidate.checksum
        ):
            detail = "Native artifact missing or integrity failed."
            break
        try:
            validate_admission(db, job)
            profiles, _ = load_profiles(db)
            model = profiles.get(job.profile)
            if (
                not model
                or not model.configured()
                or model.fingerprint() != job.request["profile_fingerprint"]
            ):
                raise ValueError("Model route changed")
            if any(
                job.request["settings"][k]
                != services.setting_view(db, owner, "analysis." + k).effective_value
                for k in ("max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds")
            ):
                raise ValueError("Native settings changed")
        except (HTTPException, ValueError, KeyError):
            detail = "Native assessment context, model connection, settings or risk limits changed."
            break
        context = admission.payload["context"]
        sidecar = candidate.payload.get("strategy_assessment", {})
        original = candidate.payload
        try:
            NativeStrategyVerdict.model_validate(
                {k: sidecar[k] for k in NativeStrategyVerdict.model_fields}
            )
        except (ValueError, KeyError, TypeError):
            detail = "Native assessment has no valid structured strategy verdict."
            break
        if (
            original.get("request") != job.request
            or job.request.get("mode") != "strategy_qualification"
            or context != job.request.get("qualification_context")
            or sidecar.get("evidence_checksum") != context["evidence_checksum"]
            or sidecar.get("adapter_version") != ADAPTER_VERSION
            or sidecar.get("strategy_version_id") != protocol.strategy_version_id
            or sidecar.get("intended_mode") != mode
            or sidecar.get("source") != "native_tauric_strategy_review"
            or sidecar.get("execution_authority") is not False
            or original.get("execution_authority") is not False
            or not original.get("reports", {}).get("final_trade_decision")
            or original.get("upstream_revision") != analysis.UPSTREAM_REVISION
            or original.get("version") != "0.5.2"
            or original.get("runtime") != "TauricResearch/TradingAgents"
        ):
            detail = "Native assessment scope or exact evidence binding is invalid."
            break
        if not job.finished_at or now() > job.finished_at + timedelta(
            hours=protocol.payload["request"]["assessment_validity_hours"]
        ):
            detail = "Native strategy assessment expired."
            break
        artifact = candidate
        decision = sidecar.get("decision")
        detail = sidecar.get("rationale", "Native strategy review has no explanation.")
        break
    criterion(
        "exact_version_tauric",
        "passed"
        if decision == "suitable"
        else "failed"
        if decision == "unsuitable"
        else "incomplete",
        detail,
    )
    status = (
        "failed"
        if any(c["status"] == "failed" for c in criteria)
        else "incomplete"
        if any(c["status"] == "incomplete" for c in criteria)
        else "passed"
    )
    previous = (
        db.scalar(
            select(func.max(QualificationReport.revision)).where(
                QualificationReport.protocol_id == protocol.id
            )
        )
        or 0
    )
    payload = {
        "request": body.model_dump(mode="json"),
        "protocol_id": protocol.id,
        "protocol_checksum": protocol.checksum,
        "evaluation_id": evaluation.id,
        "evaluation_checksum": evaluation.checksum,
        "strategy_version_id": protocol.strategy_version_id,
        "strategy_checksum": protocol.payload["strategy"]["checksum"],
        "portfolio_id": protocol.portfolio_id,
        "portfolio_version_id": protocol.portfolio_version_id,
        "intended_mode": mode,
        "scope": "registered research protocol qualification",
        "status": status,
        "criteria": criteria,
        "native_artifact_id": artifact.id if artifact else None,
        "eligible_for_automatic_promotion": False,
        "execution_authority": False,
        "limitations": protocol.payload["assumptions"]
        + [
            "Qualification for research comparison never grants deployment, promotion or order authority."
        ],
        "as_of": now().isoformat(),
    }
    row = QualificationReport(
        owner_id=owner.id,
        protocol_id=protocol.id,
        evaluation_id=evaluation.id,
        artifact_id=artifact.id if artifact else None,
        request_id=str(body.request_id),
        revision=previous + 1,
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "qualification.report_saved",
        "qualification",
        protocol.id,
        body.reason,
        {
            "report_id": row.id,
            "revision": row.revision,
            "status": status,
            "execution_authority": False,
        },
    )
    db.commit()
    return view(db, owner, protocol)


def view(db, owner, protocol):
    checked(protocol)
    evaluation = db.scalar(
        select(QualificationEvaluation).where(QualificationEvaluation.protocol_id == protocol.id)
    )
    return QualificationView(
        id=protocol.id,
        portfolio_id=protocol.portfolio_id,
        strategy_version_id=protocol.strategy_version_id,
        dataset_id=protocol.dataset_id,
        name=protocol.name,
        checksum=protocol.checksum,
        created_at=protocol.created_at,
        frozen=protocol.payload,
        evaluation={
            "id": evaluation.id,
            "checksum": checked(evaluation).checksum,
            **evaluation.payload,
        }
        if evaluation
        else None,
        assessment_jobs=[
            {
                "admission_id": a.id,
                "job": analysis.view(db, analysis.get_job(db, owner, a.job_id)).model_dump(
                    mode="json"
                ),
            }
            for a in admissions(db, protocol)
        ],
        reports=[
            {"id": r.id, "revision": r.revision, "checksum": checked(r).checksum, **r.payload}
            for r in db.scalars(
                select(QualificationReport)
                .where(QualificationReport.protocol_id == protocol.id)
                .order_by(QualificationReport.revision.desc())
            )
        ],
    )


def state(db, owner, portfolio_id=None):
    query = select(QualificationProtocol).where(QualificationProtocol.owner_id == owner.id)
    if portfolio_id:
        portfolios.resolve(db, owner, portfolio_id)
        query = query.where(QualificationProtocol.portfolio_id == str(portfolio_id))
    return QualificationState(
        protocols=[
            view(db, owner, p)
            for p in db.scalars(query.order_by(QualificationProtocol.created_at.desc()).limit(20))
        ],
        native_assessment_ready=analysis.status(db).worker_ready,
        scope="Exact-strategy research-design review using pinned Tauric plus a separate structured native review. Historical AI replay, scalp setup review and promotion remain unavailable.",
    )


def portfolio_summary(db, owner, portfolio_id):
    """Compact bound-chat evidence; never include engine sources or raw fills in context."""
    result = []
    for protocol in db.scalars(
        select(QualificationProtocol)
        .where(
            QualificationProtocol.owner_id == owner.id,
            QualificationProtocol.portfolio_id == portfolio_id,
        )
        .order_by(QualificationProtocol.created_at.desc())
        .limit(10)
    ):
        checked(protocol)
        latest = db.scalar(
            select(QualificationReport)
            .where(QualificationReport.protocol_id == protocol.id)
            .order_by(QualificationReport.revision.desc())
            .limit(1)
        )
        evaluation = db.scalar(
            select(QualificationEvaluation).where(
                QualificationEvaluation.protocol_id == protocol.id
            )
        )
        assessment = admissions(db, protocol)
        result.append(
            {
                "protocol_id": protocol.id,
                "name": protocol.name,
                "strategy_version_id": protocol.strategy_version_id,
                "portfolio_version_id": protocol.portfolio_version_id,
                "intended_mode": protocol.payload["request"]["intended_mode"],
                "evaluated": evaluation is not None,
                "registered_holdout_unseen": checked(evaluation).payload[
                    "registered_holdout_unseen"
                ]
                if evaluation
                else None,
                "latest_report": {
                    "id": latest.id,
                    "revision": latest.revision,
                    "status": checked(latest).payload["status"],
                    "as_of": latest.payload["as_of"],
                    "criteria": latest.payload["criteria"],
                }
                if latest
                else None,
                "native_jobs": [
                    {
                        k: v
                        for k, v in analysis.view(db, analysis.get_job(db, owner, a.job_id))
                        .model_dump(mode="json")
                        .items()
                        if k in ("id", "status", "artifact_id", "error", "finished_at")
                    }
                    for a in assessment[:2]
                ],
                "scope": "Recorded research-comparison snapshot; fresh report needed after evidence/context expiry or changes. Prior external owner knowledge is not verifiable.",
                "execution_authority": False,
                "eligible_for_automatic_promotion": False,
            }
        )
    return result
