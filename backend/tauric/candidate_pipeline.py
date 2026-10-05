"""Downstream deterministic suppression and ranking; no broker I/O or model calls."""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from time import perf_counter

from sqlalchemy import or_, select

from . import market, paper, scanner, services
from .broker_execution import checksum as broker_checksum
from .broker_models import BrokerOrderEvent, BrokerOrderIntent, BrokerPositionCursor
from .models import (
    AnalysisJob,
    PaperDeployment,
    ScannerCandidate,
    ScannerEvent,
    ScannerMatch,
    ScannerProfileVersion,
    StrategyVersion,
    TradingRecord,
)
from .scanner_contracts import PotentialCandidate, ScannerParameters


@dataclass
class GateState:
    published: set[str] = field(default_factory=set)
    processing: set[tuple[str, str]] = field(default_factory=set)
    processing_candidates: dict[str, tuple[str, str]] = field(default_factory=dict)
    open_positions: set[tuple[str, str]] = field(default_factory=set)
    pending_orders: set[tuple[str, str]] = field(default_factory=set)
    last_published: dict[tuple[str, str], datetime] = field(default_factory=dict)


def scope(candidate):
    return candidate.strategy_id, candidate.instrument_id


def gate(
    candidate,
    state,
    stamp,
    cooldown_seconds=0,
    *,
    check_duplicate=True,
    allow_processing_reuse=False,
):
    key = scope(candidate)
    unlinked = candidate.strategy_id in {
        "closed_bar_breakout_v1",
        "closed_bar_breakdown_v1",
        "trend_pullback_v1",
    }
    if (
        key in state.open_positions
        or ("*", key[1]) in state.open_positions
        or (unlinked and any(k[1] == key[1] for k in state.open_positions))
    ):
        return "OPEN_POSITION"
    if (
        key in state.pending_orders
        or ("*", key[1]) in state.pending_orders
        or (unlinked and any(k[1] == key[1] for k in state.pending_orders))
    ):
        return "PENDING_ORDER"
    reusable = (
        allow_processing_reuse
        and state.processing_candidates.get(candidate.candidate_id) == key
        and all(
            k != key or cid == candidate.candidate_id
            for cid, k in state.processing_candidates.items()
        )
    )
    if key in state.processing and not reusable:
        return "PROCESSING"
    if check_duplicate and candidate.dedupe_key in state.published:
        return "DUPLICATE"
    previous = state.last_published.get(key)
    if previous and (stamp - previous).total_seconds() < cooldown_seconds:
        # An explicit admission of the already published candidate is not a new setup.
        if check_duplicate or candidate.dedupe_key not in state.published:
            return "COOLDOWN"
    return None


def rank(candidates, config):
    """Quality first, then priority, data freshness and stable identity for ties."""
    ordered = sorted(
        candidates,
        key=lambda c: (
            -c.setup_score,
            -c.strategy_priority,
            -c.market_data_timestamp.timestamp(),
            c.instrument_id,
            c.strategy_id,
            c.dedupe_key,
            c.profile_version_id,
            c.candidate_id,
        ),
    )
    assets, strategies = Counter(), Counter()
    seen = set()
    selected, rejected = [], {}
    for candidate in ordered:
        reason = None
        if candidate.dedupe_key in seen:
            reason = "DUPLICATE"
        elif candidate.setup_score < config.minimum_score:
            reason = "LOW_SCORE"
        elif len(selected) >= config.max_candidates_per_cycle:
            reason = "CYCLE_LIMIT"
        elif assets[candidate.asset_class] >= config.max_candidates_per_asset_class:
            reason = "ASSET_LIMIT"
        elif strategies[candidate.strategy_id] >= config.max_candidates_per_strategy:
            reason = "STRATEGY_LIMIT"
        if reason:
            rejected[candidate.candidate_id] = reason
        else:
            seen.add(candidate.dedupe_key)
            selected.append(candidate)
            assets[candidate.asset_class] += 1
            strategies[candidate.strategy_id] += 1
    return selected, rejected


def state(db, owner, candidates, *, exclude_candidate_id=None, exclude_job_id=None):
    """Read persisted state once per cycle. Never reconcile with a broker or value a portfolio."""
    result = GateState()
    instruments = {c.instrument_id for c in candidates}
    if not instruments:
        return result
    versions = {
        v.id: v.strategy_id
        for v in db.scalars(select(StrategyVersion).where(StrategyVersion.owner_id == owner.id))
    }
    deployments = {
        d.id: d
        for d in db.scalars(
            select(PaperDeployment).where(
                PaperDeployment.owner_id == owner.id, PaperDeployment.instrument_id.in_(instruments)
            )
        )
    }
    entries = {}
    consumed_plans = set()
    if deployments:
        for fill in db.scalars(
            select(TradingRecord)
            .where(
                TradingRecord.owner_id == owner.id,
                TradingRecord.kind == "fill",
                TradingRecord.deployment_id.in_(deployments),
            )
            .order_by(TradingRecord.created_at, TradingRecord.id)
        ):
            scanner.checked(fill)
            consumed_plans.add(fill.consumes_id)
            if fill.payload["side"] == "buy":
                entries[fill.id] = fill
            else:
                entries.pop(fill.payload["position_id"], None)
        for plan in db.scalars(
            select(TradingRecord).where(
                TradingRecord.owner_id == owner.id,
                TradingRecord.kind == "plan",
                TradingRecord.deployment_id.in_(deployments),
            )
        ):
            scanner.checked(plan)
            if (
                plan.id not in consumed_plans
                and datetime.fromisoformat(plan.payload["expires_at"]) > scanner.now()
            ):
                dep = deployments[plan.deployment_id]
                result.pending_orders.add(
                    (versions.get(dep.payload.get("strategy_revision_id"), "*"), dep.instrument_id)
                )
    for entry in entries.values():
        dep = deployments[entry.deployment_id]
        identity = versions.get(dep.payload.get("strategy_revision_id"), "*")
        result.open_positions.add((identity, dep.instrument_id))
    # Map source-specific broker symbols to the registered instrument identity.
    specs = [market.instrument(db, key) for key in instruments]
    symbols = {(spec.provider, spec.provider_symbol): spec.id for spec in specs}
    positions = {
        p.entry_intent_id: p.status
        for p in db.scalars(
            select(BrokerPositionCursor).where(BrokerPositionCursor.owner_id == owner.id)
        )
    }
    intents = list(
        db.scalars(
            select(BrokerOrderIntent).where(
                BrokerOrderIntent.owner_id == owner.id,
                BrokerOrderIntent.payload["order"]["symbol"]
                .as_string()
                .in_([spec.provider_symbol for spec in specs]),
            )
        )
    )
    observed_orders = {}
    if intents:
        for receipt in db.scalars(
            select(BrokerOrderEvent)
            .where(BrokerOrderEvent.intent_id.in_([i.id for i in intents]))
            .order_by(BrokerOrderEvent.sequence)
        ):
            if broker_checksum(receipt.payload) != receipt.checksum:
                paper.fail("Candidate Gate broker receipt integrity failed.")
            if receipt.payload.get("order"):
                observed_orders[receipt.intent_id] = receipt.payload["order"]
    for intent in intents:
        if broker_checksum(intent.payload) != intent.checksum:
            paper.fail("Candidate Gate broker evidence integrity failed.")
        order = intent.payload["order"]
        if order.get("position_effect", "open") != "open":
            continue
        provider = {"oanda": "oanda_v20", "alpaca": "alpaca_stocks"}.get(
            intent.provider, intent.provider
        )
        instrument = symbols.get((provider, order["symbol"]))
        if not instrument:
            continue
        key = (versions.get(intent.payload.get("strategy_version_id"), "*"), instrument)
        status = positions.get(intent.id)
        entered = Decimal(observed_orders.get(intent.id, {}).get("filled_quantity", "0")) > 0
        if status in {"open", "partially_closed", "unknown"} or (
            (intent.status == "filled" or entered) and status != "closed"
        ):
            result.open_positions.add(key)
        if intent.status not in {"filled", "cancelled", "rejected", "expired"}:
            result.pending_orders.add(key)
    for job in db.scalars(
        select(AnalysisJob).where(
            AnalysisJob.owner_id == owner.id, AnalysisJob.status.in_(["queued", "running"])
        )
    ):
        context = job.request.get("scanner_context") or job.request.get(
            "portfolio_research_context", {}
        )
        candidate_id = context.get("candidate_id") or context.get("scanner_candidate_id")
        profile_id = context.get("profile_version_id") or context.get("scanner_profile_version_id")
        if (
            not candidate_id
            or not profile_id
            or job.id == exclude_job_id
            or candidate_id == exclude_candidate_id
        ):
            continue
        candidate = db.get(ScannerCandidate, candidate_id)
        profile = db.get(ScannerProfileVersion, profile_id)
        if candidate and profile and candidate.instrument_id in instruments:
            identity = versions.get(
                profile.strategy_version_id,
                candidate.payload.get(
                    "detector_id",
                    candidate.payload["evidence"].get(
                        "detector_id", profile.payload["parameters"]["detector_id"]
                    ),
                ),
            )
            result.processing.add((identity, candidate.instrument_id))
            result.processing_candidates[candidate.id] = (identity, candidate.instrument_id)
    for event, _candidate in db.execute(
        select(ScannerEvent, ScannerCandidate)
        .join(ScannerCandidate, ScannerEvent.candidate_id == ScannerCandidate.id)
        .where(
            ScannerEvent.owner_id == owner.id,
            ScannerCandidate.instrument_id.in_(instruments),
            ScannerEvent.payload["action"].as_string() == "published",
            or_(
                ScannerEvent.payload["details"]["candidate"]["dedupe_key"]
                .as_string()
                .in_([c.dedupe_key for c in candidates]),
                ScannerEvent.created_at >= scanner.now() - timedelta(days=1),
            ),
        )
    ):
        if event.payload["action"] != "published":
            continue
        scanner.checked(event)
        saved = event.payload["details"]["candidate"]
        result.published.add(saved["dedupe_key"])
        key = (saved["strategy_id"], saved["instrument_id"])
        result.last_published[key] = max(
            event.created_at, result.last_published.get(key, event.created_at)
        )
    return result


def shortlist(
    db, owner, profile, *, exclude_candidate_id=None, exclude_job_id=None, allow_reuse=False
):
    p = ScannerParameters.model_validate(profile.payload["parameters"])
    stamp = scanner.now()
    candidates = []
    for row in db.scalars(
        select(ScannerCandidate)
        .join(ScannerMatch)
        .where(
            ScannerCandidate.owner_id == owner.id,
            ScannerMatch.profile_version_id == profile.id,
            ScannerMatch.expires_at > stamp,
        )
    ):
        actions = list(
            db.scalars(
                select(ScannerEvent)
                .where(ScannerEvent.candidate_id == row.id)
                .order_by(ScannerEvent.created_at, ScannerEvent.id)
            )
        )
        actions = [
            e.payload["action"] for e in actions if e.payload["action"] in {"watch", "ignore"}
        ]
        if row.instrument_id not in p.disabled_instruments and (
            not actions or actions[-1] != "ignore"
        ):
            candidate = scanner.normalized(db, owner, row, profile)
            scoring = p.strategy_scores.get(profile.strategy_version_id or candidate.strategy_id)
            if candidate.setup_score >= (
                scoring.minimum_score if scoring else p.minimum_setup_score
            ):
                candidates.append(candidate)
    saved = state(
        db,
        owner,
        candidates,
        exclude_candidate_id=exclude_candidate_id,
        exclude_job_id=exclude_job_id,
    )
    passed, rejected = [], {}
    for candidate in candidates:
        reason = gate(
            candidate,
            saved,
            stamp,
            p.cooldown_seconds,
            check_duplicate=False,
            allow_processing_reuse=allow_reuse,
        )
        if reason:
            rejected[candidate.candidate_id] = reason
        else:
            passed.append(candidate)
    selected, ranked_out = rank(passed, p.ranking)
    rejected.update(ranked_out)
    return selected, rejected


def require_admission(db, owner, candidate, profile, *, job_id=None):
    selected, rejected = shortlist(
        db, owner, profile, exclude_candidate_id=candidate.id, exclude_job_id=job_id
    )
    if not any(c.candidate_id == candidate.id for c in selected):
        paper.fail(
            "Candidate Gate: " + rejected.get(candidate.id, "LOW_SCORE") + ". Research suppressed."
        )


def run(db, owner, body, *, commit=True):
    started = perf_counter()
    row = scanner.discover(db, owner, body)
    if row.id:  # Idempotent saved run: do not republish or alter immutable history.
        return scanner.run_view(row)
    profile = scanner.owned(db, ScannerProfileVersion, owner, body.profile_version_id)
    p = ScannerParameters.model_validate(profile.payload["parameters"])
    found = [PotentialCandidate.model_validate(c) for c in row.payload["potential_candidates"]]
    saved = state(db, owner, found)
    passed, rejections = [], {}
    for candidate in found:
        match = db.scalar(
            select(ScannerMatch).where(
                ScannerMatch.candidate_id == candidate.candidate_id,
                ScannerMatch.profile_version_id == profile.id,
            )
        )
        actions = [
            e.payload["action"]
            for e in db.scalars(
                select(ScannerEvent)
                .where(ScannerEvent.candidate_id == candidate.candidate_id)
                .order_by(ScannerEvent.created_at, ScannerEvent.id)
            )
            if e.payload["action"] in {"watch", "ignore"}
        ]
        reason = (
            "EXPIRED"
            if match.expires_at <= scanner.now()
            else (
                "IGNORED"
                if actions and actions[-1] == "ignore"
                else gate(candidate, saved, scanner.now(), p.cooldown_seconds)
            )
        )
        if reason:
            rejections[candidate.candidate_id] = reason
        else:
            passed.append(candidate)
    selected, ranked_out = rank(passed, p.ranking)
    metrics = row.payload["metrics"]
    metrics["candidate_gate_rejections"] = len(rejections)
    metrics["ranking_rejections"] = len(ranked_out)
    metrics["published_candidate_count"] = len(selected)
    metrics["pipeline_duration"] = round(perf_counter() - started, 6)
    ranked_reasons = ranked_out
    for result in row.payload["results"]:
        key = result.get("candidate_id")
        if key in rejections:
            result["gate_reason_code"] = rejections[key]
        if key in ranked_reasons:
            result["ranking_reason_code"] = ranked_reasons[key]
    for candidate in selected:
        scanner.event(
            db,
            owner,
            candidate.candidate_id,
            "published",
            "Candidate Gate and ranking passed.",
            {"candidate": candidate.model_dump(mode="json")},
        )
    row.payload["published_candidates"] = [c.model_dump(mode="json") for c in selected]
    row.checksum = paper.checksum(row.payload)
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "scanner.completed",
        "scanner_run",
        row.id,
        "Deterministic candidate pipeline completed; no model calls.",
        metrics,
    )
    if commit:
        db.commit()
    return scanner.run_view(row)
