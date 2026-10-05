"""Bounded preview rollout checks. Run inside the API container; no trade authority.

Actions: enable one deterministic profile, inspect durable cycles, or explicitly
admit at most one current ranked candidate to the configured native research route.
Existing trading, budget and connection settings are never changed.
"""

import argparse
import hashlib
import json
from collections import Counter
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tauric import analysis, candidate_pipeline, candidate_research, market, scanner
from tauric.broker_models import BrokerPolicyVersion, BrokerRouteVersion
from tauric.database import engine
from tauric.models import (
    AnalysisArtifact,
    AnalysisJob,
    AutonomyPolicyVersion,
    CapitalScopeVersion,
    MarketConnectionVersion,
    ModelConnectionVersion,
    Owner,
    PortfolioPolicyVersion,
    PortfolioResearchPolicyVersion,
    ScannerAnalysis,
    ScannerCandidate,
    ScannerEvent,
    ScannerProfileVersion,
    ScannerRun,
    SettingVersion,
    SharedAdmissionBudgetVersion,
    StrategyScheduleVersion,
    StrategyVersion,
    now,
)
from tauric.scanner_contracts import (
    CandidateEscalate,
    CandidateRanking,
    ScannerParameters,
    ScannerSave,
)

PROFILE_ID = str(uuid5(NAMESPACE_URL, "tauric:scanner-rollout:btc-eth:2026-10-05"))
ADMISSION_ID = uuid5(NAMESPACE_URL, "tauric:scanner-rollout:research:2026-10-05")


def configuration_summary(db):
    result = {}
    for model in (
        ModelConnectionVersion,
        MarketConnectionVersion,
        StrategyVersion,
        SettingVersion,
        SharedAdmissionBudgetVersion,
        PortfolioResearchPolicyVersion,
        AutonomyPolicyVersion,
        StrategyScheduleVersion,
        CapitalScopeVersion,
        PortfolioPolicyVersion,
        BrokerPolicyVersion,
        BrokerRouteVersion,
    ):
        rows = [
            {c.name: getattr(row, c.name) for c in model.__table__.columns}
            for row in db.scalars(select(model).order_by(model.id))
        ]
        result[model.__tablename__] = {
            "count": len(rows),
            "checksum": hashlib.sha256(
                json.dumps(rows, sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
    return result


def inspect(db, owner, profile):
    runs = list(
        db.scalars(
            select(ScannerRun)
            .where(ScannerRun.profile_version_id == profile.id)
            .order_by(ScannerRun.created_at.desc())
            .limit(30)
        )
    )
    admissions = list(
        db.scalars(
            select(ScannerAnalysis).where(
                ScannerAnalysis.profile_version_id == profile.id
            )
        )
    )
    jobs = []
    for admission in admissions:
        job = db.get(AnalysisJob, admission.job_id)
        artifact = db.scalar(
            select(AnalysisArtifact).where(AnalysisArtifact.job_id == job.id)
        )
        candidate = db.get(ScannerCandidate, admission.candidate_id)
        normalized = scanner.normalized(db, owner, candidate, profile)
        repeat_reason = candidate_pipeline.gate(
            normalized,
            candidate_pipeline.state(db, owner, [normalized]),
            now(),
            profile.payload["parameters"]["cooldown_seconds"],
        )
        if artifact:
            assert (
                artifact.checksum
                == hashlib.sha256(
                    json.dumps(
                        artifact.payload, sort_keys=True, allow_nan=False
                    ).encode()
                ).hexdigest()
            ), "Native artifact checksum failed."
            assert artifact.payload["request"] == job.request
            assert artifact.payload["execution_authority"] is False
            assert artifact.payload["governance"] is None
            assert artifact.payload["upstream_revision"] == analysis.UPSTREAM_REVISION
            assert all(
                artifact.payload["reports"].get(k, "").strip()
                for k in (
                    "market_report",
                    "sentiment_report",
                    "news_report",
                    "final_trade_decision",
                )
            )
        jobs.append(
            {
                "id": job.id,
                "status": job.status,
                "reused": admission.payload["reused"],
                "automatic": admission.payload.get("automatic", False),
                "charged": admission.charged,
                "artifact_id": artifact.id if artifact else None,
                "artifact_checksum": artifact.checksum if artifact else None,
                "execution_authority": artifact.payload.get("execution_authority")
                if artifact
                else False,
                "error": job.error,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "heartbeat_at": job.heartbeat_at.isoformat()
                if job.heartbeat_at
                else None,
                "finished_at": job.finished_at.isoformat() if job.finished_at else None,
                "progress_count": len(job.progress),
                "progress_latest": job.progress[-1:] if job.progress else [],
                "candidate_id": admission.candidate_id,
                "candidate": normalized.model_dump(mode="json"),
                "duration_seconds": round(
                    (job.finished_at - job.started_at).total_seconds(), 3
                )
                if job.finished_at and job.started_at
                else None,
                "repeat_gate_reason": repeat_reason,
                "scanner_context_verified": bool(
                    job.request.get("scanner_context", {}).get("candidate_id")
                    == candidate.id
                ),
                "artifact_verified": bool(artifact),
                "report_fields": list(artifact.payload["reports"]) if artifact else [],
                "runtime_version": artifact.payload.get("version")
                if artifact
                else None,
            }
        )
    reason_counts = Counter(
        r.get("reason_code", "CANDIDATE")
        for run in runs
        for r in run.payload["results"]
    )
    feeds = []
    for instrument_id in profile.payload["parameters"]["universe"]:
        feed = market.view(db, instrument_id, profile.payload["parameters"]["interval"])
        feeds.append(
            {
                "instrument_id": instrument_id,
                "state": feed.state,
                "gaps": feed.gaps,
                "provider_time": feed.provider_time.isoformat()
                if feed.provider_time
                else None,
                "received_at": feed.received_at.isoformat()
                if feed.received_at
                else None,
                "provider_age_seconds": round(
                    (now() - feed.provider_time).total_seconds(), 3
                )
                if feed.provider_time
                else None,
                "closed_candles": sum(c.closed for c in feed.candles),
                "snapshot_id": feed.snapshot_id,
            }
        )
    return {
        "checked_at": now().isoformat(),
        "profile_id": profile.profile_id,
        "profile_version_id": profile.id,
        "parameters": profile.payload["parameters"],
        "charged_today": scanner.charged_today(db, profile.profile_id),
        "automatic_research_ready": scanner.state(db, owner).automatic_research_ready,
        "automatic_receipts": [
            {"id": row.id, "created_at": row.created_at.isoformat(), **row.payload}
            for row in db.scalars(
                select(ScannerEvent)
                .where(
                    ScannerEvent.payload["action"]
                    .as_string()
                    .in_(
                        [
                            "automatic_research_admitted",
                            "automatic_research_deferred",
                            "automatic_research_skipped",
                        ]
                    ),
                    ScannerEvent.payload["details"]["profile_version_id"].as_string()
                    == profile.id,
                )
                .order_by(ScannerEvent.created_at.desc())
                .limit(30)
            )
        ],
        "scheduled_runs_total": db.scalar(
            select(func.count())
            .select_from(ScannerRun)
            .where(ScannerRun.profile_version_id == profile.id)
        ),
        "recent_reason_counts": dict(reason_counts),
        "recent_runs": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat(),
                "metrics": r.payload.get("metrics", {}),
                "results": r.payload["results"],
                "published_candidates": r.payload.get("published_candidates", []),
            }
            for r in runs
        ],
        "research": jobs,
        "feeds": feeds,
        "protected_configuration": configuration_summary(db),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=("enable", "enable-automatic", "inspect", "admit")
    )
    args = parser.parse_args()
    with Session(engine()) as db:
        owner = db.scalar(select(Owner))
        assert owner
        profile = scanner.head(db, PROFILE_ID)
        if args.action == "enable" and profile is None:
            saved = scanner.save(
                db,
                owner,
                ScannerSave(
                    request_id=uuid4(),
                    profile_id=PROFILE_ID,
                    name="BTC / ETH rollout",
                    expected_revision=0,
                    reason="Owner-authorized scanner rollout: real data, deterministic discovery, bounded explicit research.",
                    parameters=ScannerParameters(
                        enabled=True,
                        automatic_enabled=True,
                        universe=["binance:spot:BTCUSDT", "binance:spot:ETHUSDT"],
                        interval="1m",
                        detector_id="closed_bar_breakout_v1",
                        lookback=20,
                        minimum_volume_ratio="2",
                        candidate_ttl_seconds=1800,
                        max_analyses_per_day=1,
                        model_profile="local",
                        ranking=CandidateRanking(
                            minimum_score=0.65,
                            max_candidates_per_cycle=1,
                            max_candidates_per_asset_class=1,
                            max_candidates_per_strategy=1,
                        ),
                        cooldown_seconds=300,
                        max_data_age_seconds=90,
                    ),
                ),
            )
            profile = db.get(ScannerProfileVersion, saved.id)
        assert profile is not None, "Enable the rollout profile first."
        if args.action == "enable-automatic" and (
            not profile.payload["parameters"].get("automatic_research_enabled", False)
            or len(profile.payload["parameters"].get("detector_ids", [])) != 3
        ):
            parameters = ScannerParameters.model_validate(
                profile.payload["parameters"]
            ).model_copy(
                update={
                    "automatic_enabled": True,
                    "automatic_research_enabled": True,
                    "detector_ids": [
                        "closed_bar_breakout_v1",
                        "closed_bar_breakdown_v1",
                        "trend_pullback_v1",
                    ],
                }
            )
            saved = scanner.save(
                db,
                owner,
                ScannerSave(
                    request_id=uuid4(),
                    profile_id=profile.profile_id,
                    name="BTC / ETH rollout",
                    expected_revision=profile.revision,
                    strategy_version_id=profile.strategy_version_id,
                    reason="Owner requested multiple setup families and automatic downstream research within the existing allowance.",
                    parameters=parameters,
                ),
            )
            profile = db.get(ScannerProfileVersion, saved.id)
        if args.action == "admit":
            previous = db.scalar(
                select(ScannerAnalysis).where(
                    ScannerAnalysis.owner_id == owner.id,
                    ScannerAnalysis.request_id == str(ADMISSION_ID),
                )
            )
            if not previous:
                selected, _ = candidate_pipeline.shortlist(db, owner, profile)
                if selected:
                    candidate_research.escalate(
                        db,
                        owner,
                        selected[0].candidate_id,
                        CandidateEscalate(
                            request_id=ADMISSION_ID,
                            profile_version_id=profile.id,
                            reason="Owner-authorized bounded real-data acceptance: one current gate-approved ranked candidate, research only.",
                        ),
                    )
                else:
                    print(
                        json.dumps(
                            {
                                "admission": "NO_CURRENT_RANKED_CANDIDATE",
                                **inspect(db, owner, profile),
                            }
                        )
                    )
                    return
        print(json.dumps(inspect(db, owner, profile)))


if __name__ == "__main__":
    main()
