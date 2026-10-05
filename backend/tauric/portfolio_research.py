"""Research-only coordinator; atomic bounded steps resume after process interruption."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert

from . import admission_budget, analysis, market, paper, portfolios, research, scanner, services
from .asset_capabilities import research_capabilities
from .market_contracts import AnalysisRequest
from .model_profiles import connection_head
from .models import (
    AnalysisJob,
    Owner,
    PortfolioResearchDigest,
    PortfolioResearchLink,
    PortfolioResearchPolicyVersion,
    PortfolioResearchRun,
    PortfolioResearchSchedule,
    PortfolioResearchStep,
    ScannerCandidate,
    ScannerProfileVersion,
    WorkerStatus,
    identifier,
    now,
)
from .portfolio_research_contracts import (
    PortfolioResearchParameters,
    PortfolioResearchRunView,
    PortfolioResearchState,
)
from .research_contracts import ReplayCreate, ReplayParameters
from .research_models import ResearchRecord
from .scanner_contracts import ScannerRunRequest

LOCK = 913570071
ACTIVE = ("queued", "running", "waiting_native")


def lock(db):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
        raise HTTPException(
            429,
            "Portfolio research coordination is busy. Try again; no duplicate work was started.",
        )


def checked(row):
    if not row or paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Portfolio research evidence integrity failed.")
    return row


def head(db, owner, portfolio_id):
    row = db.scalar(
        select(PortfolioResearchPolicyVersion)
        .where(
            PortfolioResearchPolicyVersion.owner_id == owner.id,
            PortfolioResearchPolicyVersion.portfolio_id == str(portfolio_id),
        )
        .order_by(PortfolioResearchPolicyVersion.revision.desc())
        .limit(1)
    )
    return checked(row) if row else None


def daily_due(day, parameters):
    hour, minute = map(int, parameters.daily_time.split(":"))
    naive = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    zone = ZoneInfo(parameters.timezone)
    # First occurrence during a repeated hour; first valid minute after a skipped hour.
    for _ in range(181):
        local = naive.replace(tzinfo=zone, fold=0)
        utc = local.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            return utc
        naive += timedelta(minutes=1)
    raise ValueError("Daily schedule has no valid local time.")


def next_daily(stamp, parameters):
    day = stamp.astimezone(ZoneInfo(parameters.timezone)).date()
    due = daily_due(day, parameters)
    return due if due > stamp else daily_due(day + timedelta(days=1), parameters)


def latest_daily(stamp, parameters):
    day = stamp.astimezone(ZoneInfo(parameters.timezone)).date()
    due = daily_due(day, parameters)
    return due if due <= stamp else daily_due(day - timedelta(days=1), parameters)


def save(db, owner, portfolio_id, body, *, commit=True, turn_id=None):
    lock(db)
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    portfolio = portfolios.view(db, owner, portfolio_id)
    if not portfolio.preferences:
        raise HTTPException(422, "Save the portfolio mandate before configuring its research.")
    old = db.scalar(
        select(PortfolioResearchPolicyVersion).where(
            PortfolioResearchPolicyVersion.owner_id == owner.id,
            PortfolioResearchPolicyVersion.request_id == str(body.request_id),
        )
    )
    payload = body.model_dump(mode="json")
    if old:
        if checked(old).payload != payload or old.portfolio_id != portfolio.id:
            raise HTTPException(
                409, "This request identifier belongs to a different research policy."
            )
        return state(db, owner, portfolio.id)
    previous = head(db, owner, portfolio.id)
    if body.expected_revision != (previous.revision if previous else 0):
        raise HTTPException(409, "Portfolio research settings changed. Reload before saving.")
    p = body.parameters
    for key in p.universe:
        spec = market.instrument(db, key)
        if spec.asset_type not in p.asset_classes:
            raise HTTPException(
                422, "An instrument is outside the selected research asset classes."
            )
    for profile_id in p.scanner_profile_ids:
        profile = scanner.owned(db, ScannerProfileVersion, owner, profile_id)
        scanner.checked(profile)
        if not set(profile.payload["parameters"]["universe"]).issubset(p.universe):
            raise HTTPException(422, "Scanner universe must fit the portfolio research universe.")
    risk_limits = []
    for account in portfolio.accounts:
        risk_limits.append(
            Decimal(paper.policy(db, paper.account(db, owner, account["id"]))[0].risk_percent)
        )
    for plan in p.experiments:
        data = research.owned(db, owner, plan.dataset_id, "dataset")
        if data.payload["instrument_id"] not in p.universe:
            raise HTTPException(
                422, "Experiment datasets must fit the portfolio research universe."
            )
        if Decimal(plan.parameters.risk_percent) > min(risk_limits):
            raise HTTPException(422, "Experiment risk exceeds a linked account's per-trade limit.")
        if plan.parameters.strategy_version_id:
            scanner.strategies.version(db, owner, plan.parameters.strategy_version_id)
    if p.max_native_jobs_per_day and admission_budget.head(db, owner) is None:
        raise HTTPException(
            422, "Configure the shared AI job allowance before enabling automatic native research."
        )
    root, _ = portfolios.resolve(db, owner, portfolio.id)
    version = PortfolioResearchPolicyVersion(
        id=identifier(),
        owner_id=owner.id,
        portfolio_id=portfolio.id,
        anchor_version_id=root.id,
        revision=body.expected_revision + 1,
        request_id=str(body.request_id),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(version)
    db.flush()
    schedule = db.get(PortfolioResearchSchedule, portfolio.id)
    if not schedule:
        schedule = PortfolioResearchSchedule(
            portfolio_id=portfolio.id, owner_id=owner.id, policy_version_id=version.id
        )
        db.add(schedule)
    schedule.policy_version_id = version.id
    schedule.next_daily_due = next_daily(now(), p) if p.enabled else None
    schedule.next_cadence_due = (
        now() + timedelta(hours=p.additional_cadence_hours)
        if p.enabled and p.additional_cadence_hours
        else None
    )
    db.flush()
    services.audit(
        db,
        owner,
        "portfolio.research_settings_changed",
        "capital_scope",
        portfolio.id,
        body.reason,
        {
            "version_id": version.id,
            "revision": version.revision,
            "turn_id": turn_id,
            "before": previous.payload if previous else None,
            "after": payload,
        },
    )
    if commit:
        db.commit()
    return state(db, owner, portfolio.id)


def tasks(parameters):
    result = [{"kind": "assets"}]
    if parameters.opportunities:
        result.extend(
            {"kind": "scan", "profile_id": str(key)} for key in parameters.scanner_profile_ids
        )
        result.append({"kind": "shortlist"})
    if parameters.strategies:
        plans = []
        for index, plan in enumerate(parameters.experiments):
            values = [plan.parameters]
            if plan.explore_sma_neighbours and plan.parameters.engine == "sma_cross_spot_v1":
                for delta in (-1, 1):
                    fast = plan.parameters.fast_window + delta
                    if 1 <= fast < plan.parameters.slow_window:
                        values.append(
                            ReplayParameters.model_validate(
                                {**plan.parameters.model_dump(), "fast_window": fast}
                            )
                        )
            for variant, params in enumerate(values):
                plans.append(
                    {
                        "kind": "experiment",
                        "dataset_id": str(plan.dataset_id),
                        "hypothesis": plan.hypothesis,
                        "parameters": params.model_dump(mode="json"),
                        "family": index,
                        "variant": variant,
                    }
                )
        result.extend(plans[: parameters.max_experiments_per_run])
        result.append(
            {
                "kind": "strategy_summary",
                "requested": len(plans),
                "planned": min(len(plans), parameters.max_experiments_per_run),
            }
        )
    result.append({"kind": "native"})
    return result


def enqueue(
    db, owner, portfolio_id, body, *, trigger="manual", trigger_key=None, due_at=None, commit=True
):
    lock(db)
    portfolio = portfolios.view(db, owner, portfolio_id)
    policy = head(db, owner, portfolio.id)
    if not policy:
        raise HTTPException(422, "Save portfolio research settings before requesting a run.")
    p = PortfolioResearchParameters.model_validate(policy.payload["parameters"])
    key = trigger_key or (body.event_key if body.event_key else str(body.request_id))
    if body.event_key:
        if not p.enabled or body.event_key not in p.event_keys:
            raise HTTPException(403, "This research event is not enabled in the portfolio policy.")
        trigger = "event"
        key = body.event_key + ":" + str(body.request_id)
    request = body.model_dump(mode="json")
    existing = db.scalar(
        select(PortfolioResearchRun).where(
            PortfolioResearchRun.owner_id == owner.id,
            PortfolioResearchRun.request_id == str(body.request_id),
        )
    )
    if existing:
        if checked(existing).portfolio_id != portfolio.id or existing.payload["request"] != request:
            raise HTTPException(409, "This request identifier belongs to another research run.")
        return run_view(db, existing)
    existing = db.scalar(
        select(PortfolioResearchRun).where(
            PortfolioResearchRun.portfolio_id == portfolio.id,
            PortfolioResearchRun.trigger == trigger,
            PortfolioResearchRun.trigger_key == key,
        )
    )
    if existing:
        return run_view(db, checked(existing))
    if db.scalar(
        select(PortfolioResearchRun.id)
        .where(
            PortfolioResearchRun.portfolio_id == portfolio.id,
            PortfolioResearchRun.status.in_(ACTIVE),
        )
        .limit(1)
    ):
        raise HTTPException(
            409,
            "A portfolio research run is already active. Inspect it before requesting more work.",
        )
    if (
        db.scalar(
            select(func.count())
            .select_from(PortfolioResearchRun)
            .where(PortfolioResearchRun.status.in_(ACTIVE))
        )
        >= 20
    ):
        raise HTTPException(429, "The portfolio research queue is full.")
    payload = {
        "request": request,
        "parameters": p.model_dump(mode="json"),
        "tasks": tasks(p),
        "portfolio_revision": portfolio.revision,
        "research_objective": portfolio.preferences.research_objective,
        "execution_authority": False,
        "due_at": (due_at or now()).isoformat(),
        "missed_run_recovery": bool(due_at and due_at < now() - timedelta(minutes=1)),
    }
    row = PortfolioResearchRun(
        id=identifier(),
        owner_id=owner.id,
        portfolio_id=portfolio.id,
        policy_version_id=policy.id,
        request_id=str(body.request_id),
        trigger=trigger,
        trigger_key=key,
        due_at=due_at or now(),
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "portfolio.research_queued",
        "capital_scope",
        portfolio.id,
        body.reason,
        {"run_id": row.id, "trigger": trigger, "execution_authority": False},
    )
    if commit:
        db.commit()
    return run_view(db, row)


def steps(db, row):
    return [
        checked(s).payload
        for s in db.scalars(
            select(PortfolioResearchStep)
            .where(PortfolioResearchStep.run_id == row.id)
            .order_by(PortfolioResearchStep.step_index)
        )
    ]


def links(db, row):
    return list(
        db.scalars(
            select(PortfolioResearchLink)
            .where(PortfolioResearchLink.run_id == row.id)
            .order_by(PortfolioResearchLink.created_at)
        )
    )


def run_view(db, row):
    checked(row)
    digest = db.scalar(
        select(PortfolioResearchDigest).where(PortfolioResearchDigest.run_id == row.id)
    )
    return PortfolioResearchRunView(
        id=row.id,
        portfolio_id=row.portfolio_id,
        policy_version_id=row.policy_version_id,
        trigger=row.trigger,
        status=row.status,
        due_at=row.due_at,
        created_at=row.created_at,
        finished_at=row.finished_at,
        steps=steps(db, row),
        native_jobs=[
            {
                "charged": checked(link).charged,
                "job": analysis.view(
                    db,
                    analysis.get_job(db, db.get(Owner, row.owner_id), link.job_id),
                ).model_dump(mode="json"),
            }
            for link in links(db, row)
        ],
        digest=checked(digest).payload if digest else None,
    )


def state(db, owner, portfolio_id):
    portfolios.resolve(db, owner, portfolio_id)
    policy = head(db, owner, portfolio_id)
    schedule = db.get(PortfolioResearchSchedule, str(portfolio_id))
    worker = db.get(WorkerStatus, "portfolio_research")
    return PortfolioResearchState(
        portfolio_id=str(portfolio_id),
        revision=policy.revision if policy else 0,
        version_id=policy.id if policy else None,
        parameters=policy.payload["parameters"] if policy else None,
        next_daily_due=schedule.next_daily_due if schedule else None,
        next_cadence_due=schedule.next_cadence_due if schedule else None,
        worker_ready=bool(worker and 0 <= (now() - worker.heartbeat_at).total_seconds() < 90),
        worker_seen_at=worker.heartbeat_at if worker else None,
        asset_capabilities=research_capabilities(db),
        shared_budget=admission_budget.view(db, owner),
        runs=[
            run_view(db, row)
            for row in db.scalars(
                select(PortfolioResearchRun)
                .where(
                    PortfolioResearchRun.owner_id == owner.id,
                    PortfolioResearchRun.portfolio_id == str(portfolio_id),
                )
                .order_by(PortfolioResearchRun.created_at.desc())
                .limit(20)
            )
        ],
    )


def heartbeat(db):
    db.execute(
        insert(WorkerStatus)
        .values(id="portfolio_research", heartbeat_at=now(), version="portfolio-research-v1")
        .on_conflict_do_update(
            index_elements=[WorkerStatus.id],
            set_={"heartbeat_at": now(), "version": "portfolio-research-v1"},
        )
    )
    db.commit()


def cache_replay(db, owner, task):
    # Reuse only exact data/parameters and the same saved implementation sources.
    dataset = research.owned(db, owner, task["dataset_id"], "dataset")
    for row in db.scalars(
        select(ResearchRecord)
        .where(
            ResearchRecord.owner_id == owner.id,
            ResearchRecord.kind == "run",
            ResearchRecord.parent_id == dataset.id,
        )
        .order_by(ResearchRecord.created_at.desc())
        .limit(100)
    ):
        checked(row)
        p = row.payload
        if (
            p["dataset_checksum"] != dataset.checksum
            or ReplayParameters.model_validate(p["request"]["parameters"]).model_dump(mode="json")
            != task["parameters"]
            or p["request"]["hypothesis"] != task["hypothesis"]
        ):
            continue
        from importlib import import_module
        from pathlib import Path

        if not p.get("engine_sources") or any(
            Path(import_module(module).__file__).read_text() != source
            for module, source in p.get("engine_sources", {}).items()
        ):
            continue
        return research.run_view(row)
    return None


def execute_step(db, owner, row, task):
    p = PortfolioResearchParameters.model_validate(row.payload["parameters"])
    kind = task["kind"]
    if kind == "assets":
        selected = [r for r in research_capabilities(db) if r["asset_class"] in p.asset_classes]
        return {
            "track": "scope",
            "status": "deferred"
            if any(
                not r["data_ready"] or not r["screening_ready"] or not r["native_research_ready"]
                for r in selected
            )
            else "complete",
            "capabilities": selected,
            "detail": "Read-only data and retrospective replay readiness are separate from scanner/native research readiness. Unavailable research routes are deferred; no market is substituted.",
        }
    if kind == "scan":
        scan = scanner.run(
            db,
            owner,
            ScannerRunRequest(
                request_id=uuid5(NAMESPACE_URL, row.id + ":" + task["profile_id"]),
                profile_version_id=task["profile_id"],
            ),
            commit=False,
        )
        return {
            "track": "opportunities",
            "status": "complete",
            "scanner_run_id": scan.id,
            "results": scan.results,
            "model_calls": 0,
        }
    if kind == "shortlist":
        from .candidate_pipeline import shortlist

        ranked = []
        seen = set()
        for profile_id in p.scanner_profile_ids:
            profile = scanner.current_profile(db, owner, profile_id)
            selected, _ = shortlist(db, owner, profile, allow_reuse=True)
            for candidate in selected:
                if candidate.dedupe_key not in seen:
                    seen.add(candidate.dedupe_key)
                    ranked.append(
                        {
                            **candidate.model_dump(mode="json"),
                            "expires_at": scanner.candidate_view(
                                db, owner, db.get(ScannerCandidate, candidate.candidate_id)
                            ).expires_at.isoformat(),
                        }
                    )
        ranked.sort(
            key=lambda r: (
                -r["setup_score"],
                -r["strategy_priority"],
                r["instrument_id"],
                r["dedupe_key"],
            )
        )
        return {
            "track": "opportunities",
            "status": "complete" if p.scanner_profile_ids else "deferred",
            "shortlist": ranked[: p.shortlist_size],
            "rank_basis": "deterministic_setup_score_priority_freshness",
            "detail": "No candidates is valid. Configure scanner profiles to screen the selected universe.",
        }
    if kind == "experiment":
        portfolio = portfolios.view(db, owner, row.portfolio_id)
        for account in portfolio.accounts:
            limit = paper.policy(db, paper.account(db, owner, account["id"]))[0].risk_percent
            if Decimal(task["parameters"]["risk_percent"]) > Decimal(limit):
                raise HTTPException(409, "Experiment risk exceeds a current linked account limit.")
        existing = cache_replay(db, owner, task)
        result = existing or research.run(
            db,
            owner,
            ReplayCreate(
                request_id=uuid5(NAMESPACE_URL, row.id + ":" + str(row.step_index)),
                dataset_id=task["dataset_id"],
                name="Portfolio experiment · " + row.portfolio_id[:8],
                hypothesis=task["hypothesis"],
                parameters=task["parameters"],
                reason="Scheduled portfolio research-only experiment",
            ),
            commit=False,
        )
        return {
            "track": "strategies",
            "status": "complete",
            "replay_id": result.id,
            "dataset_id": result.dataset_id,
            "dataset_checksum": result.dataset_checksum,
            "family": task["family"],
            "variant": task["variant"],
            "parameters": task["parameters"],
            "metrics": result.result.metrics.model_dump(mode="json"),
            "return_percent": result.result.return_percent,
            "max_drawdown_percent": result.result.max_drawdown_percent,
            "reused": bool(existing),
            "assumptions": result.assumptions,
            "qualification": "not_qualified",
            "native_strategy_assessment": "unavailable",
            "execution_authority": False,
        }
    if kind == "strategy_summary":
        return {
            "track": "strategies",
            "status": "complete"
            if p.experiments and task["planned"] == task["requested"]
            else "deferred",
            "experiments_requested": task["requested"],
            "experiments_planned": task["planned"],
            "detail": "Configure datasets/hypotheses for experiments. No backtest alone qualifies a deployment.",
        }
    shortlist = next((s.get("shortlist", []) for s in steps(db, row) if "shortlist" in s), [])
    results = []
    from .research_cache import compatible_daily

    for instrument in dict.fromkeys(s["instrument_id"] for s in shortlist):
        from .candidate_pipeline import require_admission

        source = next(s for s in shortlist if s["instrument_id"] == instrument)
        candidate = scanner.owned(db, ScannerCandidate, owner, source["candidate_id"])
        try:
            # Older saved shortlists had shared evidence but no normalized profile field.
            profile_id = source.get("profile_version_id") or next(
                (
                    m["profile_version_id"]
                    for m in scanner.matches(db, owner, candidate)
                    if m["eligible"] and m["profile_version_id"] in map(str, p.scanner_profile_ids)
                ),
                None,
            )
            if not profile_id:
                raise HTTPException(409, "No eligible scanner match remains for this shortlist.")
            profile = scanner.eligible(db, owner, candidate, profile_id)
            require_admission(db, owner, candidate, profile)
        except HTTPException as error:
            results.append(
                {"instrument_id": instrument, "status": "deferred", "reason": str(error.detail)}
            )
            continue
        job = compatible_daily(db, owner, instrument, p.model_profile)
        reused = bool(job)
        if not job:
            start = now().replace(hour=0, minute=0, second=0, microsecond=0)
            charged = db.scalar(
                select(func.count())
                .select_from(PortfolioResearchLink)
                .where(
                    PortfolioResearchLink.owner_id == owner.id,
                    PortfolioResearchLink.portfolio_id == row.portfolio_id,
                    PortfolioResearchLink.charged.is_(True),
                    PortfolioResearchLink.created_at >= start,
                    PortfolioResearchLink.created_at < start + timedelta(days=1),
                )
            )
            if charged >= p.max_native_jobs_per_day:
                results.append(
                    {
                        "instrument_id": instrument,
                        "status": "deferred",
                        "reason": "Portfolio daily native allowance exhausted.",
                    }
                )
                continue
            conn = connection_head(db, p.model_profile)
            try:
                with db.begin_nested():
                    view = analysis.enqueue(
                        db,
                        owner,
                        AnalysisRequest(
                            request_id=uuid5(NAMESPACE_URL, row.id + ":" + instrument),
                            instrument_id=instrument,
                            profile=p.model_profile,
                        ),
                        commit=False,
                        portfolio_research_context={
                            "run_id": row.id,
                            "policy_version_id": row.policy_version_id,
                            "portfolio_id": row.portfolio_id,
                            "connection_revision": conn.revision if conn else 0,
                            "scanner_candidate_id": candidate.id,
                            "scanner_profile_version_id": profile.id,
                            "purpose": "daily_market_research_only",
                            "features_consumed_by_native_graph": False,
                        },
                    )
                    job = db.get(AnalysisJob, view.id)
            except HTTPException as error:
                results.append(
                    {"instrument_id": instrument, "status": "deferred", "reason": str(error.detail)}
                )
                continue
        payload = {
            "scope": "current_daily_research",
            "reused": reused,
            "execution_authority": False,
        }
        db.add(
            PortfolioResearchLink(
                owner_id=owner.id,
                portfolio_id=row.portfolio_id,
                run_id=row.id,
                instrument_id=instrument,
                job_id=job.id,
                charged=not reused,
                payload=payload,
                checksum=paper.checksum(payload),
            )
        )
        db.flush()
        results.append(
            {
                "instrument_id": instrument,
                "status": "reused" if reused else "queued",
                "job_id": job.id,
            }
        )
    return {
        "track": "native_context",
        "status": "deferred" if any(r["status"] == "deferred" for r in results) else "complete",
        "results": results,
        "scope": "current_daily_market_research_not_intraday_setup_analysis",
    }


def finalize(db, owner, row, *, cancelled=False):
    evidence = steps(db, row)
    jobs = [
        analysis.view(db, db.get(AnalysisJob, link.job_id)).model_dump(mode="json")
        for link in links(db, row)
    ]
    partial = any(s["status"] in ("failed", "deferred") for s in evidence) or any(
        j["status"] != "succeeded" for j in jobs
    )
    row.status = "cancelled" if cancelled else "partial" if partial else "succeeded"
    row.finished_at = now()
    db.flush()
    experiments = [s for s in evidence if s.get("replay_id")]
    # Comparison is grouped by dataset; objectives do not create a false cross-market league table.
    objective = row.payload["research_objective"]

    def score(s):
        net = Decimal(s["metrics"]["net_pnl"])
        draw = Decimal(s["max_drawdown_percent"])
        return (
            (draw, -net)
            if objective == "consistency"
            else (-net, draw)
            if objective == "net_return"
            else (-(Decimal(s["return_percent"]) - draw), draw)
        )

    groups = {}
    for experiment in experiments:
        assumptions = {
            k: v
            for k, v in experiment["parameters"].items()
            if k not in ("engine", "strategy_version_id", "fast_window", "slow_window")
        }
        key = experiment["dataset_id"] + ":" + paper.checksum(assumptions)
        groups.setdefault(key, []).append(experiment)
    comparisons = {
        key: [s["replay_id"] for s in sorted(values, key=score)]
        for key, values in sorted(groups.items())
    }
    payload = {
        "run_id": row.id,
        "portfolio_id": row.portfolio_id,
        "status": row.status,
        "trigger": row.trigger,
        "due_at": row.due_at.isoformat(),
        "as_of": now().isoformat(),
        "tracks": evidence,
        "native_jobs": jobs,
        "strategy_comparisons": comparisons,
        "research_objective": objective,
        "qualification": "incomplete",
        "execution_authority": False,
        "detail": "Research findings only. Required exact-version Tauric assessment and qualification remain pending.",
        "model_usage": "Admission counts are measured; token/provider costs are unknown.",
        "missed_run_recovery": row.payload["missed_run_recovery"],
    }
    digest = PortfolioResearchDigest(
        owner_id=owner.id,
        portfolio_id=row.portfolio_id,
        run_id=row.id,
        payload=payload,
        checksum=paper.checksum(payload),
    )
    db.add(digest)
    services.audit(
        db,
        owner,
        "portfolio.research_digest_saved",
        "capital_scope",
        row.portfolio_id,
        "Saved both portfolio research tracks",
        {"run_id": row.id, "status": row.status, "execution_authority": False},
    )


def process_one(db):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(LOCK))):
        return False
    # Each bounded step commits its effects and evidence together. A lost transaction can repeat
    # data/CPU work, but never duplicates a committed experiment or model admission.
    row = None
    for candidate in db.scalars(
        select(PortfolioResearchRun)
        .where(PortfolioResearchRun.status.in_(ACTIVE))
        .order_by(PortfolioResearchRun.created_at)
        .with_for_update(skip_locked=True)
        .limit(20)
    ):
        if candidate.status == "waiting_native":
            candidate_owner = db.get(Owner, candidate.owner_id)
            candidate_policy = head(db, candidate_owner, candidate.portfolio_id)
            if (
                not candidate_policy
                or candidate_policy.id != candidate.policy_version_id
                or portfolios.view(db, candidate_owner, candidate.portfolio_id).revision
                != candidate.payload["portfolio_revision"]
            ):
                row = candidate
                break
            pending = any(
                db.get(AnalysisJob, link.job_id).status in ("queued", "running")
                for link in links(db, candidate)
            )
            deadline = candidate.created_at + timedelta(
                minutes=candidate.payload["parameters"]["native_wait_minutes"]
            )
            if pending and now() < deadline:
                continue
        row = candidate
        break
    if row:
        checked(row)
        owner = db.get(Owner, row.owner_id)
        current = head(db, owner, row.portfolio_id)
        portfolio = portfolios.view(db, owner, row.portfolio_id)
        if (
            not current
            or current.id != row.policy_version_id
            or portfolio.revision != row.payload["portfolio_revision"]
        ):
            finalize(db, owner, row, cancelled=True)
            db.commit()
            return True
        if row.step_index < len(row.payload["tasks"]):
            task = row.payload["tasks"][row.step_index]
            try:
                with db.begin_nested():
                    result = execute_step(db, owner, row, task)
            except HTTPException as error:
                result = {
                    "track": task["kind"],
                    "status": "failed",
                    "reason": str(error.detail),
                    "execution_authority": False,
                }
            step = PortfolioResearchStep(
                run_id=row.id,
                step_index=row.step_index,
                payload=result,
                checksum=paper.checksum(result),
            )
            db.add(step)
            row.step_index += 1
            row.status = "running"
            db.commit()
            return True
        jobs = [db.get(AnalysisJob, link.job_id) for link in links(db, row)]
        if any(j.status in ("queued", "running") for j in jobs):
            if now() < row.created_at + timedelta(
                minutes=row.payload["parameters"]["native_wait_minutes"]
            ):
                row.status = "waiting_native"
                db.commit()
                return True
            result = {
                "track": "native_context",
                "status": "deferred",
                "reason": "Native research exceeded this run's wait deadline. No paid retry or execution was requested.",
            }
            db.add(
                PortfolioResearchStep(
                    run_id=row.id,
                    step_index=row.step_index,
                    payload=result,
                    checksum=paper.checksum(result),
                )
            )
            row.step_index += 1
            db.flush()
        finalize(db, owner, row)
        db.commit()
        return True
    from .portfolio_research_contracts import PortfolioResearchRequest

    schedule = db.scalar(
        select(PortfolioResearchSchedule)
        .where(
            PortfolioResearchSchedule.portfolio_id.not_in(
                select(PortfolioResearchRun.portfolio_id).where(
                    PortfolioResearchRun.status.in_(ACTIVE)
                )
            ),
            or_(
                PortfolioResearchSchedule.next_daily_due <= now(),
                PortfolioResearchSchedule.next_cadence_due <= now(),
            ),
        )
        .order_by(PortfolioResearchSchedule.next_daily_due)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if not schedule:
        db.commit()
        return False
    owner = db.get(Owner, schedule.owner_id)
    policy = checked(db.get(PortfolioResearchPolicyVersion, schedule.policy_version_id))
    p = PortfolioResearchParameters.model_validate(policy.payload["parameters"])
    if schedule.next_daily_due and schedule.next_daily_due <= now():
        due = latest_daily(now(), p)
        trigger = "daily"
        key = due.astimezone(ZoneInfo(p.timezone)).date().isoformat()
        schedule.next_daily_due = next_daily(now(), p)
    else:
        cadence = timedelta(hours=p.additional_cadence_hours)
        due = schedule.next_cadence_due + ((now() - schedule.next_cadence_due) // cadence) * cadence
        trigger = "cadence"
        key = due.isoformat()
        schedule.next_cadence_due = now() + cadence
    enqueue(
        db,
        owner,
        schedule.portfolio_id,
        PortfolioResearchRequest(
            request_id=uuid5(NAMESPACE_URL, schedule.portfolio_id + ":" + trigger + ":" + key),
            reason="Automatic portfolio research schedule",
        ),
        trigger=trigger,
        trigger_key=key,
        due_at=due,
        commit=False,
    )
    db.commit()
    return True


def validate_admission(db, job):
    context = job.request.get("portfolio_research_context")
    if not context:
        return
    if context.get("scanner_candidate_id"):
        from .candidate_pipeline import require_admission

        owner = db.get(Owner, job.owner_id)
        candidate = scanner.owned(db, ScannerCandidate, owner, context["scanner_candidate_id"])
        profile = scanner.eligible(db, owner, candidate, context["scanner_profile_version_id"])
        require_admission(db, owner, candidate, profile, job_id=job.id)
    row = checked(db.get(PortfolioResearchRun, context["run_id"]))
    owner = db.get(Owner, row.owner_id)
    policy = head(db, owner, row.portfolio_id)
    conn = connection_head(db, job.profile)
    if (
        row.owner_id != job.owner_id
        or row.portfolio_id != context["portfolio_id"]
        or not policy
        or policy.id != context["policy_version_id"]
        or row.status not in ACTIVE
        or portfolios.view(db, owner, row.portfolio_id).revision
        != row.payload["portfolio_revision"]
        or context["connection_revision"] != (conn.revision if conn else 0)
    ):
        raise ValueError("Portfolio research policy or route changed before native execution")
