"""Durable native-analysis admission, leases and immutable result artifacts."""

import hashlib
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select

from . import market, native_scope, services
from .market_contracts import AnalysisStatus, AnalysisView
from .model_profiles import load_profiles, public_profiles
from .models import AnalysisArtifact, AnalysisJob, MarketSnapshot, WorkerStatus, identifier, now

UPSTREAM_REVISION = "8b22d43d01d9ddda5d686d093d5385884622f3de"
LEASE_SECONDS = 45
QUEUE_LOCK = 913570021


def status(db):
    worker = db.get(WorkerStatus, "native")
    fresh = bool(worker and (now() - worker.heartbeat_at).total_seconds() < LEASE_SECONDS)
    return AnalysisStatus(
        profiles=public_profiles(db),
        worker_ready=fresh,
        worker_seen_at=worker.heartbeat_at if worker else None,
        runtime_version=worker.version if worker else None,
        supported_scope="Current daily crypto, stock and ETF research, plus Forex daily evidence with primary central-bank headlines and date-only policy calendars. Source gaps remain explicit; economic releases, policy benchmarks and financing depend on configured provider access. Historical, intraday and futures analysis are not enabled.",
    )


def view(db, job):
    artifact_id = db.scalar(select(AnalysisArtifact.id).where(AnalysisArtifact.job_id == job.id))
    return AnalysisView(
        id=job.id,
        instrument_id=job.instrument_id,
        profile=job.profile,
        status=job.status,
        request=job.request,
        progress=job.progress,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
        artifact_id=artifact_id,
    )


def get_job(db, owner, job_id):
    job = db.get(AnalysisJob, job_id)
    if not job or job.owner_id != owner.id:
        raise HTTPException(404, "This analysis run does not exist.")
    return job


def enqueue(
    db,
    owner,
    body,
    *,
    commit=True,
    scanner_context=None,
    chat_context=None,
    autonomous_context=None,
    portfolio_research_context=None,
    qualification_context=None,
    broker_context=None,
    prepared_fx_context=None,
):
    if broker_context is not None and any(
        context is not None
        for context in (
            qualification_context,
            autonomous_context,
            scanner_context,
            chat_context,
            portfolio_research_context,
        )
    ):
        raise HTTPException(422, "A broker setup assessment requires its own research context.")
    spec = market.instrument(db, body.instrument_id)
    if not native_scope.supported(spec):
        raise HTTPException(
            422,
            "Native research for this asset is not enabled; chart data and retrospective replay are available separately.",
        )
    if spec.asset_type == "fx" and any(
        context is not None
        for context in (
            autonomous_context,
            portfolio_research_context,
        )
    ):
        raise HTTPException(
            422,
            "Forex supports owner-requested daily research through chat or the analysis screen, scanner and exact-strategy research. Automated portfolio research is not enabled.",
        )
    existing = db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.owner_id == owner.id, AnalysisJob.request_id == str(body.request_id)
        )
    )
    if existing:
        if (existing.instrument_id, existing.profile, existing.snapshot_id) != (
            body.instrument_id,
            body.profile,
            body.snapshot_id,
        ):
            raise HTTPException(
                409, "This request identifier was already used for a different run."
            )
        if (
            existing.request.get("scanner_context") != scanner_context
            or existing.request.get("chat_context") != chat_context
            or existing.request.get("autonomous_context") != autonomous_context
            or existing.request.get("portfolio_research_context") != portfolio_research_context
            or existing.request.get("qualification_context") != qualification_context
            or existing.request.get("broker_context") != broker_context
        ):
            raise HTTPException(
                409, "This request identifier belongs to a different research context."
            )
        return view(db, existing)
    if not status(db).worker_ready:
        raise HTTPException(503, "Start the native Tauric worker before requesting analysis.")
    profiles, _ = load_profiles(db)
    profile = profiles.get(body.profile)
    if not profile or not profile.configured():
        raise HTTPException(
            422, "Configure this AI connection in Settings before requesting analysis."
        )
    if body.snapshot_id:
        snapshot = db.get(MarketSnapshot, body.snapshot_id)
        if not snapshot or snapshot.instrument_id != spec.id:
            raise HTTPException(
                422, "This chart snapshot belongs to a different instrument or does not exist."
            )
    # Limit queue growth across tabs; this is a resource budget, not a trading rule.
    db.execute(select(func.pg_advisory_xact_lock(QUEUE_LOCK)))
    existing = db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.owner_id == owner.id, AnalysisJob.request_id == str(body.request_id)
        )
    )
    if existing:
        if (existing.instrument_id, existing.profile, existing.snapshot_id) != (
            body.instrument_id,
            body.profile,
            body.snapshot_id,
        ):
            raise HTTPException(
                409, "This request identifier was already used for a different run."
            )
        if (
            existing.request.get("scanner_context") != scanner_context
            or existing.request.get("chat_context") != chat_context
            or existing.request.get("autonomous_context") != autonomous_context
            or existing.request.get("portfolio_research_context") != portfolio_research_context
            or existing.request.get("qualification_context") != qualification_context
            or existing.request.get("broker_context") != broker_context
        ):
            raise HTTPException(
                409, "This request identifier belongs to a different research context."
            )
        return view(db, existing)
    from . import admission_budget

    admission_budget.check(db, owner)
    active = db.scalar(
        select(func.count())
        .select_from(AnalysisJob)
        .where(AnalysisJob.status.in_(["queued", "running"]))
    )
    if active >= 4:
        raise HTTPException(429, "The analysis queue is full. Wait for a run to finish.")
    fx_context = None
    if spec.asset_type == "fx":
        from .native_fx import prepare

        if prepared_fx_context is None:
            fx_context = prepare(db, spec, supplemental_budget_seconds=40)
        else:
            from .native_fx import evidence

            evidence(db, spec.model_dump(), prepared_fx_context)
            fx_context = prepared_fx_context
    job = AnalysisJob(
        id=identifier(),
        owner_id=owner.id,
        request_id=str(body.request_id),
        instrument_id=spec.id,
        snapshot_id=body.snapshot_id,
        profile=body.profile,
        request={
            "instrument": spec.model_dump(),
            "trade_date": now().date().isoformat(),
            "mode": "current_daily_research",
            "profile_fingerprint": profile.fingerprint(),
            "models": profile.public_config(),
            "upstream_revision": UPSTREAM_REVISION,
            "analysts": ["fx_market", "fx_macro", "fx_risk"]
            if spec.asset_type == "fx"
            else ["market", "social", "news"]
            + ([] if spec.asset_type == "etf" else ["fundamentals"]),
            "settings": {
                key: services.setting_view(db, owner, "analysis." + key).effective_value
                for key in ["max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds"]
            },
        },
    )
    job.request["settings"]["max_recur_limit"] = 200
    if fx_context:
        job.request["forex_context"] = fx_context
    if scanner_context:
        job.request["scanner_context"] = scanner_context
    if chat_context:
        job.request["chat_context"] = chat_context
    if autonomous_context:
        job.request["autonomous_context"] = autonomous_context
    if portfolio_research_context:
        job.request["portfolio_research_context"] = portfolio_research_context
    if qualification_context:
        job.request["qualification_context"] = qualification_context
        job.request["mode"] = "strategy_qualification"
    if broker_context:
        job.request["broker_context"] = broker_context
        job.request["mode"] = "broker_entry_assessment"
    db.add(job)
    services.audit(
        db,
        owner,
        "analysis.requested",
        "analysis",
        job.id,
        "Owner requested daily native research",
        {"profile": body.profile, "instrument_id": spec.id},
    )
    db.flush()
    if commit:
        db.commit()
    return view(db, job)


def heartbeat(db, version):
    from sqlalchemy.dialects.postgresql import insert

    db.execute(
        insert(WorkerStatus)
        .values(id="native", heartbeat_at=now(), version=version)
        .on_conflict_do_update(
            index_elements=["id"], set_={"heartbeat_at": now(), "version": version}
        )
    )
    db.commit()


def claim(db):
    db.execute(select(func.pg_advisory_xact_lock(QUEUE_LOCK)))
    stale = db.scalars(
        select(AnalysisJob)
        .where(
            AnalysisJob.status == "running",
            AnalysisJob.heartbeat_at < now() - timedelta(seconds=LEASE_SECONDS),
        )
        .with_for_update()
    ).all()
    for job in stale:
        job.status = "interrupted"
        job.finished_at = now()
        job.error = "The worker stopped responding. This run was not automatically retried; review before requesting another run."
    if db.scalar(select(AnalysisJob.id).where(AnalysisJob.status == "running").limit(1)):
        db.commit()
        return None
    job = db.scalar(
        select(AnalysisJob)
        .where(AnalysisJob.status == "queued")
        .order_by(AnalysisJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job:
        job.status = "running"
        job.started_at = job.heartbeat_at = now()
    db.commit()
    return job.id if job else None


def renew(db, job_id, progress):
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
    if not job or job.status != "running":
        return False
    job.heartbeat_at = now()
    job.progress = progress
    db.commit()
    return True


def finish(db, job_id, payload=None, error=None):
    job = db.scalar(select(AnalysisJob).where(AnalysisJob.id == job_id).with_for_update())
    if not job or job.status != "running":
        return False
    if payload is not None:
        serialized = json.dumps(payload, sort_keys=True, allow_nan=False)
        if len(serialized.encode()) > 2_000_000:
            raise ValueError("Native result exceeds artifact size budget")
        db.add(
            AnalysisArtifact(
                id=identifier(),
                job_id=job.id,
                owner_id=job.owner_id,
                payload=payload,
                checksum=hashlib.sha256(serialized.encode()).hexdigest(),
            )
        )
    job.status = "succeeded" if payload is not None else "failed"
    job.finished_at = now()
    job.error = error
    db.commit()
    return True
