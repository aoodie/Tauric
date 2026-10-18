"""Cheap closed-bar screening and bounded escalation; never trading permission."""

from datetime import datetime, timedelta
from decimal import Decimal
from time import perf_counter

from fastapi import HTTPException
from sqlalchemy import func, select

from . import market, paper, provider_requests, services, strategies
from .models import (
    AnalysisJob,
    MarketSnapshot,
    ScannerAnalysis,
    ScannerCandidate,
    ScannerEvent,
    ScannerMatch,
    ScannerProfileVersion,
    ScannerRun,
    StrategyVersion,
    WorkerStatus,
    identifier,
    now,
)
from .scanner_contracts import (
    CandidateView,
    PotentialCandidate,
    ScannerParameters,
    ScannerProfileView,
    ScannerRunView,
    ScannerState,
)
from .scanner_discovery import DETECTOR, detect, prefilter, setup_score

LOCK = 913570040


def lock(db):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
        paper.fail("Another scanner change or run is in progress. Try again shortly.", 429)


def checked(row):
    if paper.checksum(row.payload) != row.checksum:
        paper.fail("Scanner evidence integrity failed.")
    return row


def owned(db, model, owner, row_id):
    row = db.get(model, str(row_id))
    if not row or row.owner_id != owner.id:
        paper.fail("This scanner resource does not exist.", 404)
    return checked(row)


def head(db, profile_id):
    return db.scalar(
        select(ScannerProfileVersion)
        .where(ScannerProfileVersion.profile_id == profile_id)
        .order_by(ScannerProfileVersion.revision.desc())
        .limit(1)
    )


def charged_today(db, profile_id):
    day = now().replace(hour=0, minute=0, second=0, microsecond=0)
    return db.scalar(
        select(func.count())
        .select_from(ScannerAnalysis)
        .join(ScannerProfileVersion, ScannerAnalysis.profile_version_id == ScannerProfileVersion.id)
        .where(
            ScannerProfileVersion.profile_id == profile_id,
            ScannerAnalysis.charged.is_(True),
            ScannerAnalysis.created_at >= day,
        )
    )


def profile_view(db, owner, row):
    checked(row)
    version = (
        strategies.version(db, owner, row.strategy_version_id) if row.strategy_version_id else None
    )
    name = row.payload["request"].get("name") or (version.name if version else "Scanner")
    return ScannerProfileView(
        id=row.id,
        profile_id=row.profile_id,
        name=name,
        strategy_version_id=row.strategy_version_id,
        strategy_name=version.name if version else None,
        strategy_label=version.payload["parameters"]["label"] if version else None,
        revision=row.revision,
        parameters=row.payload["parameters"],
        reason=row.payload["request"]["reason"],
        checksum=row.checksum,
        created_at=row.created_at,
        charged_analyses_today=charged_today(db, row.profile_id),
    )


def save(db, owner, body):
    lock(db)
    # Preserve idempotency for requests saved before optional profile fields existed.
    request = body.model_dump(mode="json", exclude_unset=True)
    previous = db.scalar(
        select(ScannerProfileVersion).where(
            ScannerProfileVersion.owner_id == owner.id,
            ScannerProfileVersion.request_id == str(body.request_id),
        )
    )
    if previous:
        if checked(previous).payload["request"] != request:
            paper.fail("This request identifier was already used for different scanner settings.")
        return profile_view(db, owner, previous)
    if body.strategy_version_id:
        strategies.version(db, owner, body.strategy_version_id)
    for instrument_id in body.parameters.universe:
        spec = market.instrument(db, instrument_id)
        if (
            spec.asset_type not in {"crypto_spot", "fx", "equity", "etf"}
            or spec.provider not in market.PROVIDERS
        ):
            paper.fail("Choose a supported crypto, Forex, stock or ETF data source.", 422)
    # Older clients identify their single linked scanner by its strategy version.
    legacy_id = body.strategy_version_id if not body.name else None
    profile_id = str(body.profile_id or legacy_id or identifier())
    current = head(db, profile_id)
    if current and current.owner_id != owner.id:
        paper.fail("This scanner resource does not exist.", 404)
    revision = current.revision if current else 0
    if revision != body.expected_revision:
        paper.fail("This scanner profile changed in another window. Reload before saving.")
    payload = {"request": request, "parameters": body.parameters.model_dump(mode="json")}
    row = ScannerProfileVersion(
        owner_id=owner.id,
        profile_id=profile_id,
        strategy_version_id=str(body.strategy_version_id) if body.strategy_version_id else None,
        revision=revision + 1,
        request_id=str(body.request_id),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "scanner.profile_saved",
        "scanner_profile",
        row.id,
        body.reason,
        {
            "profile_id": row.profile_id,
            "strategy_version_id": row.strategy_version_id,
            "revision": row.revision,
        },
    )
    db.commit()
    return profile_view(db, owner, row)


def current_profile(db, owner, profile_id):
    row = owned(db, ScannerProfileVersion, owner, profile_id)
    if head(db, row.profile_id).id != row.id or not row.payload["parameters"]["enabled"]:
        paper.fail("This scanner profile is disabled or superseded. Reload its current settings.")
    return row


def event(db, owner, candidate_id, action, reason, details=None, request_id=None):
    payload = {"action": action, "reason": reason, "details": details or {}}
    row = ScannerEvent(
        owner_id=owner.id,
        candidate_id=candidate_id,
        request_id=request_id or identifier(),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    return row


def run_view(row):
    checked(row)
    return ScannerRunView(
        id=row.id,
        profile_version_id=row.profile_version_id,
        created_at=row.created_at,
        results=row.payload["results"],
        checksum=row.checksum,
        metrics=row.payload.get("metrics", {}),
        potential_candidates=row.payload.get("potential_candidates", []),
        published_candidates=row.payload.get("published_candidates", []),
    )


def normalized(db, owner, candidate, profile, trace_id=None):
    checked(candidate)
    checked(profile)
    p = ScannerParameters.model_validate(profile.payload["parameters"])
    version = (
        db.get(StrategyVersion, profile.strategy_version_id)
        if profile.strategy_version_id
        else None
    )
    detector_id = candidate.payload.get(
        "detector_id", candidate.payload["evidence"].get("detector_id", DETECTOR)
    )
    strategy_id = version.strategy_id if version else detector_id
    scoring = p.strategy_scores.get(profile.strategy_version_id or detector_id)
    return PotentialCandidate(
        candidate_id=candidate.id,
        created_at=candidate.created_at,
        symbol=candidate.payload.get("symbol")
        or market.instrument(db, candidate.instrument_id).symbol,
        instrument_id=candidate.instrument_id,
        asset_class=candidate.payload["asset_class"],
        strategy_id=strategy_id,
        strategy_version=str(version.revision) if version else "1",
        direction=candidate.payload.get("direction", "long"),
        timeframe=candidate.payload["interval"],
        setup_score=candidate.payload["setup_score"]
        if "setup_score" in candidate.payload
        else setup_score(
            candidate.payload["features"], candidate.payload["evidence"]["minimum_volume_ratio"]
        ),
        features=candidate.payload["features"],
        market_data_timestamp=datetime.fromisoformat(candidate.payload["bar_close_time"]),
        scanner_id=profile.profile_id,
        scanner_version=candidate.payload.get("scanner_version", "1"),
        profile_version_id=profile.id,
        dedupe_key=paper.checksum(
            {
                "instrument": candidate.instrument_id,
                "strategy": strategy_id,
                "detector": detector_id,
                "timeframe": candidate.payload["interval"],
                "bar_close_time": candidate.payload["bar_close_time"],
                "direction": candidate.payload.get("direction", "long"),
            }
        ),
        trace_id=trace_id or candidate.id,
        strategy_priority=scoring.priority if scoring else 0,
        assessment=candidate.payload.get("assessment"),
    )


def run(db, owner, body, *, commit=True):
    from .candidate_pipeline import run as run_pipeline

    return run_pipeline(db, owner, body, commit=commit)


def discover(db, owner, body):
    started = perf_counter()
    lock(db)
    previous = db.scalar(
        select(ScannerRun).where(
            ScannerRun.owner_id == owner.id, ScannerRun.request_id == str(body.request_id)
        )
    )
    if previous:
        if (
            previous.profile_version_id != str(body.profile_version_id)
            or previous.payload["request"].get("trigger", "manual") != body.trigger
        ):
            paper.fail("This request identifier was already used for a different scan.")
        return previous
    profile = current_profile(db, owner, body.profile_version_id)
    parameters = ScannerParameters.model_validate(profile.payload["parameters"])
    last_evaluated = {}
    if body.trigger == "scheduled" and parameters.evaluate_on_candle_close:
        last_run = db.scalar(
            select(ScannerRun)
            .where(ScannerRun.profile_version_id == profile.id)
            .order_by(ScannerRun.created_at.desc())
            .limit(1)
        )
        if last_run:
            last_evaluated = {
                (r["instrument_id"], r.get("interval", parameters.interval)): r.get(
                    "bar_close_time"
                )
                for r in checked(last_run).payload["results"]
            }
    from . import scanner_context, scanner_scoring, scanner_universe

    universe, universe_metrics = scanner_universe.resolve(db, owner, profile, parameters)
    results = []
    context_cache = {}
    metrics = {
        **universe_metrics,
        "prefilter_pass_count": 0,
        "strategy_detection_count": 0,
        "strategy_evaluation_count": 0,
        "candidate_count": 0,
        "candidate_gate_rejections": 0,
        "published_candidate_count": 0,
    }
    budget_exhausted = False
    for instrument_id, interval in [(i, t) for i in universe for t in parameters.scan_intervals]:
        if budget_exhausted:
            results.append(
                {
                    "instrument_id": instrument_id,
                    "interval": interval,
                    "status": "unavailable",
                    "reason_code": "BUDGET_DEFERRED",
                }
            )
            continue
        if instrument_id in parameters.disabled_instruments:
            results.append(
                {
                    "instrument_id": instrument_id,
                    "interval": interval,
                    "status": "filtered",
                    "reason_code": "DISABLED_INSTRUMENT",
                }
            )
            continue
        try:
            feed = market.refresh(db, instrument_id, interval, commit=False)
            rejected = prefilter(feed, parameters, now())
            if rejected:
                results.append(
                    {
                        "instrument_id": instrument_id,
                        "status": "filtered",
                        "reason_code": rejected,
                        "interval": interval,
                        "snapshot_id": feed.snapshot_id,
                    }
                )
                continue
            metrics["prefilter_pass_count"] += 1
            latest_bar = [
                b for b in feed.candles if b.closed and b.close_time <= feed.provider_time
            ][-1]
            bar_time = latest_bar.close_time.isoformat()
            if last_evaluated.get((instrument_id, interval)) == bar_time:
                results.append(
                    {
                        "instrument_id": instrument_id,
                        "status": "filtered",
                        "reason_code": "CANDLE_UNCHANGED",
                        "bar_close_time": bar_time,
                        "interval": interval,
                    }
                )
                continue
        except HTTPException as error:
            if error.status_code == 429:
                budget_exhausted = True
                metrics["retry_after"] = int((error.headers or {}).get("Retry-After", "60"))
            results.append(
                {
                    "instrument_id": instrument_id,
                    "status": "unavailable",
                    "reason": error.detail,
                    "reason_code": "DATA_UNAVAILABLE",
                    "interval": interval,
                }
            )
            continue
        for detector_id in parameters.detectors:
            detector_parameters = parameters.model_copy(
                update={"detector_id": detector_id, "interval": interval}
            )
            metrics["strategy_evaluation_count"] += 1
            found, detail = detect(feed, detector_parameters)
            if not found:
                results.append(
                    {
                        "instrument_id": instrument_id,
                        "detector_id": detector_id,
                        "status": "filtered",
                        "reason": detail,
                        "reason_code": "NO_SETUP",
                        "interval": interval,
                        "snapshot_id": feed.snapshot_id,
                        "bar_close_time": bar_time,
                    }
                )
                continue
            metrics["strategy_detection_count"] += 1
            score = setup_score(found["features"], parameters.minimum_volume_ratio)
            assessment = None
            if parameters.scoring_version == "multifactor_v1":
                contexts = {}
                for higher in scanner_scoring.HIGHER[interval]:
                    cache_key = (instrument_id, higher)
                    if cache_key not in context_cache:
                        try:
                            context_cache[cache_key] = market.refresh(
                                db, instrument_id, higher, commit=False
                            )
                        except HTTPException as failure:
                            context_cache[cache_key] = None
                            if failure.status_code == 429:
                                budget_exhausted = True
                                metrics["retry_after"] = int(
                                    (failure.headers or {}).get("Retry-After", "60")
                                )
                    if context_cache[cache_key]:
                        contexts[higher] = context_cache[cache_key]
                assessment = scanner_scoring.calculate_setup_score(
                    feed,
                    found["features"],
                    detector_parameters,
                    higher_feeds=contexts,
                    macro=scanner_context.evidence(
                        db, owner, feed.instrument, latest_bar.close_time
                    ),
                    direction=found.get("direction", "long"),
                    stamp=latest_bar.close_time,
                )
                score = float(assessment["score"])
                found["evidence"]["assessment"] = assessment
            scoring = parameters.strategy_scores.get(profile.strategy_version_id or detector_id)
            exact_score = Decimal(assessment["score"]) if assessment else Decimal(str(score))
            if exact_score < Decimal(
                str(scoring.minimum_score if scoring else parameters.minimum_setup_score)
            ):
                results.append(
                    {
                        "instrument_id": instrument_id,
                        "detector_id": detector_id,
                        "status": "filtered",
                        "reason_code": "LOW_SCORE",
                        "setup_score": score,
                        "interval": interval,
                        "bar_close_time": bar_time,
                    }
                )
                continue
            metrics["candidate_count"] += 1
            key = paper.checksum(found["evidence"])
            candidate = db.scalar(
                select(ScannerCandidate).where(
                    ScannerCandidate.owner_id == owner.id, ScannerCandidate.context_key == key
                )
            )
            reused = bool(candidate)
            if not candidate:
                payload = {
                    **found,
                    "asset_class": feed.instrument.asset_type,
                    "interval": interval,
                    "snapshot_checksum": feed.checksum,
                    "received_at": feed.received_at.isoformat(),
                    "provider_time": feed.provider_time.isoformat(),
                    "availability": feed.availability,
                    "execution_authority": False,
                    "setup_score": score,
                    "bar_close_time": bar_time,
                    "symbol": feed.instrument.symbol,
                    "direction": found.get("direction", "long"),
                    "detector_id": detector_id,
                    "scanner_version": "3",
                }
                if assessment:
                    payload["assessment"] = assessment
                candidate = ScannerCandidate(
                    owner_id=owner.id,
                    instrument_id=instrument_id,
                    snapshot_id=feed.snapshot_id,
                    context_key=key,
                    payload=payload,
                    checksum=paper.checksum(payload),
                    created_at=now(),
                )
                db.add(candidate)
                db.flush()
                event(
                    db,
                    owner,
                    candidate.id,
                    "discovered",
                    "Deterministic closed-bar filters passed.",
                )
            else:
                checked(candidate)
            matched = db.scalar(
                select(ScannerMatch).where(
                    ScannerMatch.candidate_id == candidate.id,
                    ScannerMatch.profile_version_id == profile.id,
                )
            )
            if not matched:
                db.add(
                    ScannerMatch(
                        candidate_id=candidate.id,
                        profile_version_id=profile.id,
                        expires_at=candidate.created_at
                        + timedelta(seconds=parameters.candidate_ttl_seconds),
                    )
                )
                event(
                    db,
                    owner,
                    candidate.id,
                    "profile_matched",
                    "Saved scanner profile matched this evidence.",
                    {
                        "profile_version_id": profile.id,
                        "strategy_version_id": profile.strategy_version_id,
                    },
                )
            results.append(
                {
                    "instrument_id": instrument_id,
                    "detector_id": detector_id,
                    "status": "matched",
                    "candidate_id": candidate.id,
                    "reused": reused,
                    "snapshot_id": feed.snapshot_id,
                    "setup_score": score,
                    "bar_close_time": bar_time,
                    "interval": interval,
                }
            )
    scanner_universe.finish(metrics, results, parameters.scan_intervals)
    if parameters.catalog:
        provider = "oanda_v20" if parameters.catalog.provider == "oanda" else "alpaca_stocks"
        metrics["request_budget_remaining"] = provider_requests.remaining(db, provider)
    if metrics.get("retry_after") and not metrics.get("next_check_at"):
        metrics["next_check_at"] = (now() + timedelta(seconds=metrics["retry_after"])).isoformat()
    payload = {
        "results": results,
        "profile_checksum": profile.checksum,
        "request": body.model_dump(mode="json"),
        "potential_candidates": [
            normalized(
                db,
                owner,
                db.get(ScannerCandidate, r["candidate_id"]),
                profile,
                str(body.request_id),
            ).model_dump(mode="json")
            for r in results
            if r["status"] == "matched"
        ],
        "model_calls": 0,
        "metrics": {**metrics, "scan_duration": round(perf_counter() - started, 6)},
    }
    row = ScannerRun(
        owner_id=owner.id,
        request_id=str(body.request_id),
        profile_version_id=profile.id,
        created_at=now(),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    return row


def matches(db, owner, candidate):
    result = []
    for match in db.scalars(
        select(ScannerMatch)
        .where(ScannerMatch.candidate_id == candidate.id)
        .order_by(ScannerMatch.created_at)
    ):
        profile = owned(db, ScannerProfileVersion, owner, match.profile_version_id)
        view = profile_view(db, owner, profile)
        eligible = (
            head(db, profile.profile_id).id == profile.id
            and profile.payload["parameters"]["enabled"]
            and now() < match.expires_at
        )
        result.append(
            {
                "profile_version_id": profile.id,
                "profile_id": profile.profile_id,
                "profile_name": view.name,
                "strategy_version_id": profile.strategy_version_id,
                "strategy_name": view.strategy_name,
                "strategy_label": view.strategy_label,
                "profile_revision": profile.revision,
                "expires_at": match.expires_at.isoformat(),
                "eligible": bool(eligible),
                "model_profile": profile.payload["parameters"]["model_profile"],
            }
        )
    return result


def candidate_view(db, owner, row):
    checked(row)
    matched = matches(db, owner, row)
    events = list(
        db.scalars(
            select(ScannerEvent)
            .where(ScannerEvent.candidate_id == row.id)
            .order_by(ScannerEvent.created_at, ScannerEvent.id)
        )
    )
    history = [
        {"id": e.id, "created_at": e.created_at.isoformat(), **checked(e).payload} for e in events
    ]
    actions = [e.payload["action"] for e in events if e.payload["action"] in {"watch", "ignore"}]
    status = (
        "ignored" if actions and actions[-1] == "ignore" else "watch" if actions else "candidate"
    )
    if not any(m["eligible"] for m in matched):
        if status != "ignored":
            status = (
                "expired"
                if all(now() >= datetime.fromisoformat(m["expires_at"]) for m in matched)
                else "inactive"
            )
    last = db.scalar(
        select(ScannerAnalysis)
        .where(ScannerAnalysis.candidate_id == row.id)
        .order_by(ScannerAnalysis.created_at.desc())
        .limit(1)
    )
    from .candidate_research import analysis

    job = analysis.view(db, db.get(AnalysisJob, checked(last).job_id)) if last else None
    potential = [
        normalized(db, owner, row, db.get(ScannerProfileVersion, m["profile_version_id"]))
        for m in matched
    ]
    return CandidateView(
        id=row.id,
        asset_class=row.payload["asset_class"],
        instrument_id=row.instrument_id,
        interval=row.payload["interval"],
        detector_id=row.payload.get(
            "detector_id", row.payload["evidence"].get("detector_id", DETECTOR)
        ),
        direction=row.payload.get("direction", "long"),
        detected_at=row.created_at,
        expires_at=max(datetime.fromisoformat(m["expires_at"]) for m in matched),
        status=status,
        features=row.payload["features"],
        reasons=row.payload["reasons"],
        snapshot_id=row.snapshot_id,
        checksum=row.checksum,
        matches=matched,
        analysis=job,
        history=history,
        setup_score=potential[0].setup_score,
        assessment=row.payload.get("assessment"),
        potential_candidates=potential,
    )


def candidate_action(db, owner, candidate_id, body):
    lock(db)
    candidate = owned(db, ScannerCandidate, owner, candidate_id)
    request = {**body.model_dump(mode="json"), "candidate_id": candidate.id}
    previous = db.scalar(
        select(ScannerEvent).where(
            ScannerEvent.owner_id == owner.id, ScannerEvent.request_id == str(body.request_id)
        )
    )
    if previous:
        if checked(previous).payload["details"] != request:
            paper.fail("This request identifier was used for another candidate action.")
        return candidate_view(db, owner, candidate)
    event(db, owner, candidate.id, body.action, body.reason, request, str(body.request_id))
    services.audit(
        db, owner, "scanner." + body.action, "scanner_candidate", candidate.id, body.reason, {}
    )
    db.commit()
    return candidate_view(db, owner, candidate)


def eligible(db, owner, candidate, profile_id):
    view = candidate_view(db, owner, candidate)
    if view.status in {"expired", "inactive", "ignored"} or not any(
        m["eligible"] and m["profile_version_id"] == str(profile_id) for m in view.matches
    ):
        paper.fail(
            "This candidate is expired, ignored or its scanner profile changed. Scan fresh evidence."
        )
    snapshot = db.get(MarketSnapshot, candidate.snapshot_id)
    if not snapshot or snapshot.checksum != candidate.payload["snapshot_checksum"]:
        paper.fail("The candidate market evidence cannot be verified.")
    return current_profile(db, owner, profile_id)


def escalate(db, owner, candidate_id, body):
    """Compatibility entry point. Research is owned by the downstream service."""
    from .candidate_research import escalate as admit

    return admit(db, owner, candidate_id, body)


def validate_admission(db, job):
    from .candidate_research import validate_admission as validate

    return validate(db, job)


def state(db, owner):
    rows = list(
        db.scalars(
            select(ScannerProfileVersion)
            .where(ScannerProfileVersion.owner_id == owner.id)
            .order_by(ScannerProfileVersion.revision.desc())
        )
    )
    seen, profiles = set(), []
    for row in rows:
        if row.profile_id not in seen:
            seen.add(row.profile_id)
            profiles.append(profile_view(db, owner, row))
    candidates = [
        candidate_view(db, owner, row)
        for row in db.scalars(
            select(ScannerCandidate)
            .where(ScannerCandidate.owner_id == owner.id)
            .order_by(ScannerCandidate.created_at.desc())
            .limit(50)
        )
    ]
    runs = [
        run_view(row)
        for row in db.scalars(
            select(ScannerRun)
            .where(ScannerRun.owner_id == owner.id)
            .order_by(ScannerRun.created_at.desc())
            .limit(20)
        )
    ]
    worker = db.get(WorkerStatus, "candidate_research")
    return ScannerState(
        automatic_research_ready=bool(
            worker and 0 <= (now() - worker.heartbeat_at).total_seconds() < 30
        ),
        profiles=profiles,
        candidates=candidates,
        runs=runs,
        scan_mode="scheduled"
        if any(p.parameters.automatic_enabled for p in profiles)
        else "manual",
    )
