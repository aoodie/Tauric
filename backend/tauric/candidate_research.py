"""Downstream Tauric research admission; scanner discovery never invokes this module."""

from sqlalchemy import func, select

from . import analysis, paper, scanner, services
from .market_contracts import AnalysisRequest
from .model_profiles import connection_head, load_profiles
from .models import AnalysisJob, ScannerAnalysis, ScannerCandidate


def connection_revision(db, profile):
    saved = connection_head(db, profile)
    return saved.revision if saved else 0


def escalate(db, owner, candidate_id, body, *, commit=True, automatic=False):
    scanner.lock(db)
    candidate = scanner.owned(db, ScannerCandidate, owner, candidate_id)
    request = {**body.model_dump(mode="json"), "candidate_id": candidate.id}
    previous = db.scalar(
        select(ScannerAnalysis).where(
            ScannerAnalysis.owner_id == owner.id, ScannerAnalysis.request_id == str(body.request_id)
        )
    )
    if previous:
        if scanner.checked(previous).payload["request"] != request:
            paper.fail("This request identifier was used for another research request.")
        return analysis.view(db, db.get(AnalysisJob, previous.job_id))
    profile = scanner.eligible(db, owner, candidate, body.profile_version_id)
    if automatic and not profile.payload["parameters"].get("automatic_research_enabled", False):
        paper.fail("Automatic scanner research is disabled.")
    from .candidate_pipeline import require_admission

    require_admission(db, owner, candidate, profile)
    p = profile.payload["parameters"]
    # Pin the model route and effective budgets through atomic queue admission.
    db.execute(
        select(
            func.pg_advisory_xact_lock(913570030 if p["model_profile"] == "cloud" else 913570031)
        )
    )
    budget_keys = ["max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds"]
    for budget_key in sorted(budget_keys):
        for scope in ("global", "user"):
            services.setting_head(db, owner, "analysis." + budget_key, scope, lock=True)
    profiles, _ = load_profiles(db)
    model = profiles.get(p["model_profile"])
    if not model or not model.configured():
        paper.fail(
            "Configure this AI connection in Settings before requesting daily research.", 422
        )
    settings = {
        key: services.setting_view(db, owner, "analysis." + key).effective_value
        for key in ["max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds"]
    }
    key = paper.checksum(
        {
            "candidate_evidence": candidate.context_key,
            "profile_fingerprint": model.fingerprint(),
            "connection_revision": connection_revision(db, p["model_profile"]),
            "settings": settings,
            "trade_date": scanner.now().date().isoformat(),
            "upstream_revision": analysis.UPSTREAM_REVISION,
        }
    )
    from .research_cache import compatible_daily

    job = compatible_daily(db, owner, candidate.instrument_id, p["model_profile"])
    reused = bool(job)
    if reused:
        result = analysis.view(db, job)
    else:
        if scanner.charged_today(db, profile.profile_id) >= p["max_analyses_per_day"]:
            paper.fail("This scanner profile has used its daily research allowance.", 429)
        context = {
            "candidate_id": candidate.id,
            "candidate_checksum": candidate.checksum,
            "profile_version_id": profile.id,
            "profile_id": profile.profile_id,
            "profile_checksum": profile.checksum,
            "strategy_version_id": profile.strategy_version_id,
            "purpose": "daily_research_only",
            "features_consumed_by_native_graph": False,
            "automatic": automatic,
            "connection_revision": connection_revision(db, p["model_profile"]),
        }
        result = analysis.enqueue(
            db,
            owner,
            AnalysisRequest(
                request_id=body.request_id,
                instrument_id=candidate.instrument_id,
                profile=p["model_profile"],
                snapshot_id=candidate.snapshot_id,
            ),
            commit=False,
            scanner_context=context,
        )
    payload = {
        "request": request,
        "reused": bool(reused),
        "research_scope": "current_daily_research",
        "execution_authority": False,
        "automatic": automatic,
    }
    db.add(
        ScannerAnalysis(
            owner_id=owner.id,
            candidate_id=candidate.id,
            profile_version_id=profile.id,
            strategy_version_id=profile.strategy_version_id,
            job_id=result.id,
            request_id=str(body.request_id),
            research_key=key,
            charged=not bool(reused),
            payload=payload,
            checksum=paper.checksum(payload),
            created_at=scanner.now(),
        )
    )
    scanner.event(
        db,
        owner,
        candidate.id,
        "research_reused" if reused else "analysis_requested",
        body.reason,
        {"job_id": result.id, "profile_version_id": profile.id, "scope": "current_daily_research"},
    )
    services.audit(
        db,
        owner,
        "scanner.research_requested",
        "scanner_candidate",
        candidate.id,
        body.reason,
        {"job_id": result.id, "reused": bool(reused), "execution_authority": False},
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return result


def validate_admission(db, job):
    context = job.request.get("scanner_context")
    if not context:
        return
    from .models import Owner

    owner = db.get(Owner, job.owner_id)
    candidate = scanner.owned(db, ScannerCandidate, owner, context["candidate_id"])
    profile = scanner.eligible(db, owner, candidate, context["profile_version_id"])
    if context.get("automatic") and not profile.payload["parameters"].get(
        "automatic_research_enabled", False
    ):
        paper.fail("Automatic scanner research is disabled. No model request was started.")
    from .candidate_pipeline import require_admission

    require_admission(db, owner, candidate, profile, job_id=job.id)
    if context["connection_revision"] != connection_revision(db, job.profile):
        paper.fail("The scanner research AI connection changed. No model request was started.")
    if (
        context["candidate_checksum"] != candidate.checksum
        or context["profile_checksum"] != profile.checksum
    ):
        paper.fail("Scanner admission context changed. No model request was started.")
