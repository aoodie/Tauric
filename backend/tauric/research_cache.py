"""Compatible daily native work can be shared without rewriting its original context."""

from fastapi import HTTPException
from sqlalchemy import func, select

from . import analysis, services
from .models import AnalysisJob, now


def compatible_daily(db, owner, instrument_id, profile):
    if not db.scalar(select(func.pg_try_advisory_xact_lock(analysis.QUEUE_LOCK))):
        raise HTTPException(429, "Native research admission is busy; no duplicate was admitted.")
    profiles, _ = analysis.load_profiles(db)
    route = profiles.get(profile)
    if not route or not route.configured():
        return None
    settings = {
        key: services.setting_view(db, owner, "analysis." + key).effective_value
        for key in ("max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds")
    }
    settings["max_recur_limit"] = 200
    rows = db.scalars(
        select(AnalysisJob)
        .where(
            AnalysisJob.owner_id == owner.id,
            AnalysisJob.instrument_id == instrument_id,
            AnalysisJob.profile == profile,
        )
        .order_by(AnalysisJob.created_at.desc())
        .limit(50)
    )
    for job in rows:
        r = job.request
        if (
            r.get("mode") != "current_daily_research"
            or r.get("trade_date") != now().date().isoformat()
            or r.get("profile_fingerprint") != route.fingerprint()
            or r.get("settings") != settings
            or r.get("upstream_revision") != analysis.UPSTREAM_REVISION
        ):
            continue
        if job.status in ("queued", "running"):
            from . import autonomous, candidate_research, chat_research, portfolio_research

            try:
                for module in (autonomous, candidate_research, chat_research, portfolio_research):
                    module.validate_admission(db, job)
            except (HTTPException, ValueError, KeyError):
                continue
        return job  # Failed/interrupted work is also reused, never silently billed again.
    return None
