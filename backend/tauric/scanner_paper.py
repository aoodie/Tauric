"""Candidate-to-paper navigation with the assistant's original evidence gates."""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select

from . import assistant, paper, scanner
from .models import Owner, PaperDeployment, ScannerCandidate, TradingRecord, now
from .research_models import ResearchRecord
from .scanner_contracts import CandidatePaperReadiness, PaperPrerequisite
from .trading_contracts import PaperAction


def context(db, owner, candidate_id):
    candidate = scanner.owned(db, ScannerCandidate, owner, candidate_id)
    view = scanner.candidate_view(db, owner, candidate)
    versions = {
        m["strategy_version_id"]
        for m in view.matches
        if m["eligible"] and m.get("strategy_version_id")
    }
    deployments = [
        d
        for d in db.scalars(
            select(PaperDeployment).where(
                PaperDeployment.owner_id == owner.id,
                PaperDeployment.instrument_id == candidate.instrument_id,
            )
        )
        if d.payload.get("strategy_revision_id") in versions
    ]
    artifact_id = view.analysis.artifact_id if view.analysis else None
    return view, deployments, artifact_id


def bind(db, owner, candidate_id, decision_id, backtest_id):
    candidate, deployments, artifact_id = context(db, owner, candidate_id)
    if candidate.status not in {"candidate", "watch"} or candidate.direction != "long":
        raise HTTPException(
            409, "An active long candidate is required by the current paper entry service."
        )
    decision = paper.record(db, owner, decision_id, "governance")
    dep = next((d for d in deployments if d.id == decision.deployment_id), None)
    if not dep:
        raise HTTPException(
            409, "Use the candidate's linked immutable strategy deployment and governance decision."
        )
    assistant.matching_backtest(db, owner, dep, backtest_id)
    proposal = paper.record(db, owner, decision.payload["proposal_id"], "proposal")
    if not artifact_id or proposal.payload.get("native_artifact_id") != artifact_id:
        raise HTTPException(
            409, "The decision must use this candidate's completed native research evidence."
        )
    assistant.native_paper_evidence(db, owner, dep, artifact_id)
    return decision


def readiness(db, owner, candidate_id):
    candidate, deployments, artifact_id = context(db, owner, candidate_id)
    active = candidate.status in {"candidate", "watch"} and candidate.direction == "long"
    native = False
    tests = []
    decisions = []
    for dep in deployments:
        if artifact_id:
            try:
                assistant.native_paper_evidence(db, owner, dep, artifact_id)
                native = True
            except HTTPException:
                pass
        for run in db.scalars(
            select(ResearchRecord)
            .where(ResearchRecord.owner_id == owner.id, ResearchRecord.kind == "run")
            .order_by(ResearchRecord.created_at.desc())
            .limit(100)
        ):
            try:
                assistant.matching_backtest(db, owner, dep, run.id)
                tests.append((dep.id, run.id))
                break
            except HTTPException:
                continue
        for row in db.scalars(
            select(TradingRecord)
            .where(
                TradingRecord.owner_id == owner.id,
                TradingRecord.deployment_id == dep.id,
                TradingRecord.kind == "governance",
            )
            .order_by(TradingRecord.created_at.desc())
            .limit(30)
        ):
            decision = paper.record(db, owner, row.id, "governance")
            payload = decision.payload
            if (
                payload.get("outcome") != "allow"
                or datetime.fromisoformat(payload["expires_at"]) <= now()
            ):
                continue
            proposal = paper.record(db, owner, payload["proposal_id"], "proposal")
            if artifact_id and proposal.payload.get("native_artifact_id") == artifact_id:
                backtest = next((identity for dep_id, identity in tests if dep_id == dep.id), None)
                if backtest:
                    decisions.append((decision.id, backtest))
    chosen = decisions[0] if decisions else (None, None)
    prerequisites = [
        PaperPrerequisite(
            name="Active long setup",
            ready=active,
            detail="Current paper entries support long setups from an enabled scanner.",
        ),
        PaperPrerequisite(
            name="Linked paper strategy",
            ready=bool(deployments),
            detail="Use a saved executable strategy linked to this scanner and deployed to a paper account.",
        ),
        PaperPrerequisite(
            name="Matching backtest",
            ready=bool(tests),
            detail="Backtest the exact deployed strategy version, instrument and rule timeframe.",
        ),
        PaperPrerequisite(
            name="Current native BUY evidence",
            ready=native,
            detail="Complete today's original native research for this candidate under the current AI settings.",
        ),
        PaperPrerequisite(
            name="Eligible governance decision",
            ready=bool(decisions),
            detail="A current allow decision must reference the same deployment and native evidence.",
        ),
    ]
    return CandidatePaperReadiness(
        candidate_id=candidate.id,
        ready=all(p.ready for p in prerequisites),
        prerequisites=prerequisites,
        decision_id=chosen[0],
        backtest_id=chosen[1],
        detail="Paper staging rechecks account, pause, balances, policy, quote, allocation and decision validity. It creates a plan; execution is a separate governed action.",
    )


def stage(db, owner, candidate_id, body):
    db.scalar(select(Owner.id).where(Owner.id == owner.id).with_for_update())
    # Reconcile this exact request before reevaluating expiring source evidence.
    prior = db.scalar(
        select(TradingRecord).where(
            TradingRecord.owner_id == owner.id,
            TradingRecord.kind == "plan",
            TradingRecord.request_id == str(body.request_id),
        )
    )
    if prior:
        prior = paper.record(db, owner, prior.id, "plan")
        request = prior.payload["request"]
        if (
            request.get("decision_id") != str(body.decision_id)
            or request.get("reason") != body.reason
        ):
            raise HTTPException(409, "This paper request belongs to different inputs.")
        # The candidate binding was saved with the plan's atomic audit event.
        from .models import AuditEvent

        if not db.scalar(
            select(AuditEvent.id).where(
                AuditEvent.owner_id == owner.id,
                AuditEvent.action == "scanner.paper_plan",
                AuditEvent.resource_id == prior.id,
                AuditEvent.details["candidate_id"].as_string() == candidate_id,
                AuditEvent.details["backtest_id"].as_string() == str(body.backtest_id),
            )
        ):
            raise HTTPException(409, "This request was staged outside this candidate workflow.")
        return prior
    decision = bind(db, owner, candidate_id, str(body.decision_id), str(body.backtest_id))
    plan = paper.plan(
        db,
        owner,
        decision.id,
        PaperAction(request_id=body.request_id, reason=body.reason),
        commit=False,
    )
    from .services import audit

    audit(
        db,
        owner,
        "scanner.paper_plan",
        "trading_record",
        plan.id,
        body.reason,
        {
            "candidate_id": candidate_id,
            "backtest_id": str(body.backtest_id),
            "decision_id": decision.id,
        },
    )
    db.commit()
    return plan
