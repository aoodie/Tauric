"""Opt-in paper execution. Durable stages never retry interrupted model calls.

Queue locks precede owner/account locks. A stage, its receipts and any paper
records commit together; provider/storage failures never leave a partial fill.
"""

import hashlib
import json
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from . import analysis, governance, market, model_profiles, paper, services, strategy_execution
from .autonomy_contracts import (
    AutonomyEventView,
    AutonomyHistory,
    AutonomyPolicyChange,
    AutonomyPolicyView,
    AutonomyState,
    AutonomyWorkflowView,
)
from .governance_contracts import GovernanceEnqueue
from .market_contracts import AnalysisRequest
from .models import (
    AnalysisArtifact,
    AnalysisJob,
    AutonomyEvent,
    AutonomyPolicyVersion,
    AutonomyWorkflow,
    GovernanceJob,
    Owner,
    StrategyRun,
    StrategySchedule,
    StrategyScheduleVersion,
    TradingRecord,
    WorkerStatus,
    identifier,
    now,
)
from .native_runner import REPORT_FIELDS
from .trading_contracts import PaperAction, ProposalCreate

WORKER_ID = "autonomous_paper"
TERMINAL = {"closed", "blocked", "cancelled"}
REPORTS = (*REPORT_FIELDS, "research_debate", "risk_debate")


def checked(row):
    if not row or paper.checksum(row.payload) != row.checksum:
        paper.fail("Autonomous paper evidence integrity failed.")
    return row


def latest(db, deployment_id):
    return db.scalar(
        select(AutonomyPolicyVersion)
        .where(AutonomyPolicyVersion.deployment_id == deployment_id)
        .order_by(AutonomyPolicyVersion.revision.desc())
        .limit(1)
    )


def enabled_monitor(db, dep):
    schedule = db.get(StrategySchedule, dep.id)
    version = db.get(StrategyScheduleVersion, schedule.version_id) if schedule else None
    return bool(version and checked(version).payload["enabled"])


def usage(db, dep_id):
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    counts = dict(
        db.execute(
            select(AutonomyEvent.kind, func.count())
            .where(AutonomyEvent.deployment_id == dep_id, AutonomyEvent.created_at >= start)
            .group_by(AutonomyEvent.kind)
        ).all()
    )
    entries = db.scalar(
        select(func.count())
        .select_from(TradingRecord)
        .where(
            TradingRecord.deployment_id == dep_id,
            TradingRecord.kind == "fill",
            TradingRecord.payload["side"].as_string() == "buy",
            TradingRecord.payload["autonomous_context"]["workflow_id"].as_string().is_not(None),
            TradingRecord.created_at >= start,
        )
    )
    return {
        "research": counts.get("research_requested", 0),
        "reviews": counts.get("review_requested", 0),
        "entries": entries,
    }


def policy_view(db, row):
    checked(row)
    return AutonomyPolicyView(
        id=row.id,
        deployment_id=row.deployment_id,
        revision=row.revision,
        settings=AutonomyPolicyChange.model_validate(row.payload),
        created_at=row.created_at,
        usage_today=usage(db, row.deployment_id),
    )


def state(db, owner):
    policies = db.scalars(
        select(AutonomyPolicyVersion)
        .where(AutonomyPolicyVersion.owner_id == owner.id)
        .order_by(AutonomyPolicyVersion.revision.desc())
    ).all()
    heads = {}
    for row in policies:
        heads.setdefault(row.deployment_id, row)
    worker = db.get(WorkerStatus, WORKER_ID)
    return AutonomyState(
        worker_ready=bool(worker and 0 <= (now() - worker.heartbeat_at).total_seconds() <= 60),
        policies=[policy_view(db, p) for p in heads.values()],
        workflows=[
            AutonomyWorkflowView.model_validate(checked(w))
            for w in db.scalars(
                select(AutonomyWorkflow)
                .where(AutonomyWorkflow.owner_id == owner.id)
                .order_by(AutonomyWorkflow.created_at.desc())
                .limit(200)
            )
        ],
    )


def save(db, owner, deployment_id, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    dep = paper.deployment(db, owner, deployment_id)
    paper.account(db, owner, dep.account_id)
    strategy_execution.rules_for(db, owner, dep)
    request = body.model_dump(mode="json")
    previous = db.scalar(
        select(AutonomyPolicyVersion).where(
            AutonomyPolicyVersion.owner_id == owner.id,
            AutonomyPolicyVersion.request_id == str(body.request_id),
        )
    )
    if previous:
        if previous.deployment_id != dep.id or checked(previous).payload != request:
            paper.fail("This request identifier belongs to another autonomy change.")
        return policy_view(db, previous)
    head = latest(db, dep.id)
    if body.expected_revision != (checked(head).revision if head else 0):
        paper.fail("Autonomous paper settings changed. Reload before saving.")
    if body.enabled and not enabled_monitor(db, dep):
        paper.fail("Enable this deployment's scheduled monitor first.", 422)
    if body.enabled and body.auto_entries:
        spec = market.instrument(db, dep.instrument_id)
        if spec.asset_type == "fx":
            paper.fail(
                "Forex reports have REVIEW status; use owner-reviewed paper entries. Scheduled attributed exits remain available.",
                422,
            )
        if spec.asset_type in {"equity", "etf"}:
            paper.fail(
                "Stock automatic entries await exact-version strategy assessment. Bound reviewed paper entries and attributed exits are available separately.",
                422,
            )
        profiles, _ = model_profiles.load_profiles(db)
        if not profiles.get(body.profile) or not profiles[body.profile].configured():
            paper.fail("Configure the selected AI connection before enabling paper entries.", 422)
    row = AutonomyPolicyVersion(
        owner_id=owner.id,
        deployment_id=dep.id,
        revision=(head.revision if head else 0) + 1,
        request_id=str(body.request_id),
        payload=request,
        checksum=paper.checksum(request),
        created_at=now(),
    )
    db.add(row)
    services.audit(
        db,
        owner,
        "paper.autonomy_changed",
        "deployment",
        dep.id,
        body.reason,
        {
            "enabled": body.enabled,
            "auto_entries": body.auto_entries,
            "auto_exits": body.auto_exits,
            "revision": row.revision,
        },
    )
    db.commit()
    return policy_view(db, row)


def history(db, owner, deployment_id):
    dep = paper.deployment(db, owner, deployment_id)
    return AutonomyHistory(
        policies=[
            policy_view(db, p)
            for p in db.scalars(
                select(AutonomyPolicyVersion)
                .where(AutonomyPolicyVersion.deployment_id == dep.id)
                .order_by(AutonomyPolicyVersion.revision.desc())
                .limit(100)
            )
        ],
        events=[
            AutonomyEventView.model_validate(checked(e))
            for e in db.scalars(
                select(AutonomyEvent)
                .where(AutonomyEvent.deployment_id == dep.id)
                .order_by(AutonomyEvent.created_at.desc())
                .limit(200)
            )
        ],
    )


def event(db, workflow, kind, **detail):
    payload = {"status": workflow.status, **detail}
    db.add(
        AutonomyEvent(
            owner_id=workflow.owner_id,
            deployment_id=workflow.deployment_id,
            workflow_id=workflow.id,
            kind=kind,
            payload=payload,
            checksum=paper.checksum(payload),
            created_at=now(),
        )
    )


def context(workflow):
    return {"workflow_id": workflow.id, "policy_version_id": workflow.policy_version_id}


def settings_snapshot(db, owner, profile):
    profiles, _ = model_profiles.load_profiles(db)
    connection = profiles.get(profile)
    if not connection or not connection.configured():
        paper.fail("The selected AI connection is unavailable.")
    return {
        "profile_fingerprint": connection.fingerprint(),
        "analysis_settings": {
            **{
                key: services.setting_view(db, owner, "analysis." + key).effective_value
                for key in ("max_debate_rounds", "max_risk_discuss_rounds", "max_tool_rounds")
            },
            "max_recur_limit": 200,
        },
        "routing_fingerprint": governance.routing(db, owner)["fingerprint"],
    }


def guard_financial(db, owner, dep, ctx, *, exit_action=False):
    workflow = checked(db.get(AutonomyWorkflow, ctx.get("workflow_id")))
    head = latest(db, dep.id)
    if (
        workflow.owner_id != owner.id
        or workflow.deployment_id != dep.id
        or ctx != context(workflow)
        or not head
        or checked(head).id != workflow.policy_version_id
        or not head.payload["enabled"]
        or not enabled_monitor(db, dep)
        or workflow.status in TERMINAL
        or datetime.fromisoformat(workflow.payload["deadline"]) <= now()
    ):
        paper.fail("Autonomous paper authority was disabled, replaced or expired.")
    if exit_action:
        if workflow.kind != "exit" or not head.payload["auto_exits"]:
            paper.fail("Automatic paper exits are not enabled for this workflow.")
        return workflow
    if workflow.kind != "entry" or not head.payload["auto_entries"]:
        paper.fail("Automatic paper entries are not enabled for this workflow.")
    acct = paper.account(db, owner, dep.account_id)
    _, pol = paper.policy(db, acct)
    nodes, _ = paper.capital.assigned(db, acct, dep)
    captured = workflow.payload
    if (
        acct.halted
        or acct.revision != captured["account_revision"]
        or pol.id != captured["policy_id"]
        or paper.ledger(db, acct)[3] != captured["ledger_digest"]
        or paper.strategies.control(db, dep).revision != captured["control_revision"]
        or not paper.strategies.control(db, dep).enabled
        or paper.capital.digest(nodes) != captured["capital_digest"]
        or settings_snapshot(db, owner, head.payload["profile"]) != captured["models"]
    ):
        paper.fail("Account, capital, deployment controls or model configuration changed.")
    if usage(db, dep.id)["entries"] >= head.payload["daily_entry_limit"]:
        paper.fail("The daily autonomous paper entry limit has been reached.")
    return workflow


def original(db, owner, dep, workflow):
    pol = checked(db.get(AutonomyPolicyVersion, workflow.policy_version_id)).payload
    job = db.get(AnalysisJob, workflow.analysis_job_id)
    artifact = db.scalar(
        select(AnalysisArtifact).where(AnalysisArtifact.job_id == workflow.analysis_job_id)
    )
    if (
        not job
        or job.owner_id != owner.id
        or job.instrument_id != dep.instrument_id
        or job.status != "succeeded"
        or job.profile != pol["profile"]
        or job.request.get("mode") != "current_daily_research"
        or job.request.get("trade_date") != now().date().isoformat()
        or job.request.get("upstream_revision") != analysis.UPSTREAM_REVISION
        or job.request.get("profile_fingerprint")
        != workflow.payload["models"]["profile_fingerprint"]
        or job.request.get("settings") != workflow.payload["models"]["analysis_settings"]
        or not job.finished_at
        or not 0 <= (now() - job.finished_at).total_seconds() <= pol["report_max_age_seconds"]
        or not artifact
        or artifact.owner_id != owner.id
        or hashlib.sha256(
            json.dumps(artifact.payload, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
        != artifact.checksum
        or artifact.payload.get("execution_authority") is not False
        or artifact.payload.get("governance") is not None
        or artifact.payload.get("request") != job.request
        or not isinstance(artifact.payload.get("native_rating"), str)
        or artifact.payload.get("native_rating", "").strip().upper() != "BUY"
        or any(
            not isinstance(artifact.payload.get("reports", {}).get(k), str)
            or not artifact.payload["reports"][k].strip()
            for k in REPORTS
        )
    ):
        paper.fail("Native daily research is missing, stale, incomplete or cannot be verified.")
    return artifact


def guard_proposal(db, owner, dep, prop):
    ctx = prop.payload.get("autonomous_context")
    if not ctx:
        return
    workflow = guard_financial(db, owner, dep, ctx)
    artifact = original(db, owner, dep, workflow)
    if prop.payload["native_artifact_id"] != artifact.id or (
        workflow.proposal_id and workflow.proposal_id != prop.id
    ):
        paper.fail("Autonomous proposal does not match the saved research workflow.")


def validate_admission(db, job):
    ctx = job.request.get("autonomous_context")
    if ctx:
        workflow = db.get(AutonomyWorkflow, ctx["workflow_id"])
        owner = db.get(Owner, job.owner_id)
        dep = paper.deployment(db, owner, workflow.deployment_id)
        guard_financial(db, owner, dep, ctx)


def key(workflow, step):
    return uuid5(NAMESPACE_URL, f"tauric/autonomy/{workflow.id}/{step}")


def refresh(db, owner, dep, workflow):
    _, rules = strategy_execution.rules_for(db, owner, dep)
    market.refresh(db, dep.instrument_id, rules.interval, commit=False)
    if rules.interval != "1m":
        market.refresh(db, dep.instrument_id, "1m", commit=False)
    signal = db.get(StrategyRun, workflow.initial_signal_id)
    if signal.checksum != workflow.payload["signal_checksum"]:
        paper.fail("The admitted strategy evidence changed.")
    strategy_execution.validate(
        db, owner, dep, signal, "enter" if workflow.kind == "entry" else "exit"
    )
    return signal


def advance(db, owner, workflow):
    dep = paper.deployment(db, owner, workflow.deployment_id)
    acct = paper.account(db, owner, dep.account_id)
    if workflow.status == "waiting_review":
        # Explicit API actions may consume the same bound plan; adopt their saved
        # records instead of duplicating them or treating an existing fill as new.
        existing_fill = db.scalar(
            select(TradingRecord).where(
                TradingRecord.kind == "fill",
                TradingRecord.deployment_id == dep.id,
                TradingRecord.payload["side"].as_string() == "buy",
                TradingRecord.payload["autonomous_context"]["workflow_id"].as_string()
                == workflow.id,
            )
        )
        if existing_fill:
            paper.record(db, owner, existing_fill.id, "fill")
            workflow.entry_fill_id = existing_fill.id
            workflow.plan_id = existing_fill.payload["plan_id"]
            workflow.decision_id = existing_fill.payload["decision_id"]
            workflow.status = "opened"
            event(db, workflow, "entry_reconciled", entry_fill_id=existing_fill.id)
    if workflow.status == "opened":
        # Reconcile owner closes and autonomous closes even after authority is revoked.
        closed = db.scalar(
            select(TradingRecord).where(
                TradingRecord.kind == "fill",
                TradingRecord.payload["position_id"].as_string() == workflow.entry_fill_id,
            )
        )
        if closed:
            workflow.close_fill_id, workflow.status, workflow.next_check_at = (
                closed.id,
                "closed",
                None,
            )
            workflow.reason = "Attributed paper position closed and ledger reconciled."
            paper.ledger(db, acct)
            event(db, workflow, "position_closed", close_fill_id=closed.id)
        else:
            workflow.next_check_at = now() + timedelta(seconds=30)
        return
    guard_financial(db, owner, dep, context(workflow), exit_action=workflow.kind == "exit")
    signal = refresh(db, owner, dep, workflow)
    pol = checked(db.get(AutonomyPolicyVersion, workflow.policy_version_id)).payload
    if workflow.kind == "exit":
        row = paper.close(
            db,
            owner,
            signal.payload["position_id"],
            PaperAction(
                request_id=key(workflow, "close"), reason="Automatic attributed paper rule exit"
            ),
            strategy_run=signal,
            commit=False,
            autonomous_context=context(workflow),
        )
        workflow.close_fill_id, workflow.status, workflow.next_check_at = row.id, "closed", None
        workflow.reason = "Attributed paper rule exit filled and ledger reconciled."
        event(db, workflow, "exit_filled", close_fill_id=row.id, strategy_run_id=signal.id)
        return
    if not workflow.proposal_id and db.scalar(
        select(TradingRecord.id).where(
            TradingRecord.deployment_id == dep.id,
            TradingRecord.kind == "proposal",
            TradingRecord.payload["strategy_signal_key"].as_string()
            == signal.payload["signal_key"],
        )
    ):
        paper.fail("This scheduled entry signal already has a proposal; no model request was made.")
    if not workflow.analysis_job_id:
        jobs = db.scalars(
            select(AnalysisJob)
            .where(
                AnalysisJob.owner_id == owner.id,
                AnalysisJob.instrument_id == dep.instrument_id,
                AnalysisJob.profile == pol["profile"],
            )
            .order_by(AnalysisJob.created_at.desc())
        ).all()
        job = next(
            (
                j
                for j in jobs
                if j.request.get("mode") == "current_daily_research"
                and j.request.get("upstream_revision") == analysis.UPSTREAM_REVISION
                and j.request.get("trade_date") == now().date().isoformat()
                and j.request.get("profile_fingerprint")
                == workflow.payload["models"]["profile_fingerprint"]
                and j.request.get("settings") == workflow.payload["models"]["analysis_settings"]
            ),
            None,
        )
        if job:
            workflow.analysis_job_id = job.id
            event(db, workflow, "research_reused", analysis_job_id=job.id)
        else:
            if usage(db, dep.id)["research"] >= pol["daily_research_limit"]:
                paper.fail(
                    "The daily native research limit has been reached; no matching report is available."
                )
            queued = analysis.enqueue(
                db,
                owner,
                AnalysisRequest(
                    request_id=key(workflow, "research"),
                    instrument_id=dep.instrument_id,
                    profile=pol["profile"],
                    snapshot_id=signal.snapshot_id,
                ),
                commit=False,
                autonomous_context=context(workflow),
            )
            workflow.analysis_job_id = queued.id
            event(db, workflow, "research_requested", analysis_job_id=queued.id)
        db.flush()
    job = db.get(AnalysisJob, workflow.analysis_job_id)
    if job.status in ("failed", "interrupted"):
        paper.fail("Native research failed or was interrupted. No automatic model retry was made.")
    if job.status in ("queued", "running"):
        workflow.next_check_at = now() + timedelta(seconds=5)
        workflow.reason = "Waiting for original native daily research."
        return
    artifact = original(db, owner, dep, workflow)
    if workflow.status == "waiting_research":
        if usage(db, dep.id)["reviews"] >= pol["daily_review_limit"]:
            paper.fail("The daily OpenClaw review limit has been reached.")
        workflow.native_artifact_id = artifact.id
        prop = paper.propose(
            db,
            owner,
            ProposalCreate(
                request_id=key(workflow, "proposal"),
                deployment_id=dep.id,
                strategy_run_id=signal.id,
                stop_price=signal.payload["stop_price"],
                native_artifact_id=artifact.id,
                reason="Automatic paper entry from saved scheduled rules and original research",
            ),
            commit=False,
            autonomous_context=context(workflow),
        )
        workflow.proposal_id = prop.id
        db.flush()
        job_view = governance.enqueue(
            db,
            owner,
            GovernanceEnqueue(
                request_id=key(workflow, "review"),
                proposal_id=prop.id,
                profile=pol["profile"],
                reason="Bounded OpenClaw review for autonomous paper entry",
            ),
            commit=False,
        )
        db.flush()
        workflow.review_job_id = job_view.id
        workflow.status = "waiting_review"
        workflow.reason = "Waiting for a bound OpenClaw paper decision."
        event(
            db,
            workflow,
            "review_requested",
            proposal_id=prop.id,
            review_job_id=job_view.id,
            native_artifact_id=artifact.id,
        )
    else:
        review = db.get(GovernanceJob, workflow.review_job_id)
        if review.status in ("failed", "interrupted"):
            paper.fail("OpenClaw review failed or was interrupted. No automatic retry was made.")
        if review.status == "succeeded":
            decision = paper.record(db, owner, review.decision_id, "governance")
            workflow.decision_id = decision.id
            if (
                decision.payload["outcome"] != "allow"
                or decision.payload["source"] != "openclaw"
                or decision.payload.get("job_id") != review.id
            ):
                paper.fail("OpenClaw rejected or deferred this paper entry.")
            planned = db.scalar(
                select(TradingRecord).where(
                    TradingRecord.kind == "plan", TradingRecord.consumes_id == decision.id
                )
            )
            if planned:
                paper.record(db, owner, planned.id, "plan")
                if planned.payload.get("autonomous_context") != context(workflow):
                    paper.fail("The existing paper plan belongs to a different workflow.")
            else:
                planned = paper.plan(
                    db,
                    owner,
                    decision.id,
                    PaperAction(
                        request_id=key(workflow, "plan"),
                        reason="Automatic bounded paper entry plan",
                    ),
                    commit=False,
                )
            db.flush()
            filled = paper.execute(
                db,
                owner,
                planned.id,
                PaperAction(
                    request_id=key(workflow, "fill"),
                    reason="Automatic paper entry after bound OpenClaw permission",
                ),
                commit=False,
            )
            workflow.plan_id, workflow.entry_fill_id = planned.id, filled.id
            workflow.status, workflow.reason = (
                "opened",
                "Paper entry filled; scheduled rules monitor attributed exits.",
            )
            workflow.next_check_at = now() + timedelta(seconds=30)
            event(
                db,
                workflow,
                "entry_filled",
                plan_id=planned.id,
                entry_fill_id=filled.id,
                decision_id=decision.id,
            )
            return
    workflow.next_check_at = now() + timedelta(seconds=5)


def admit(db, owner):
    policies = db.scalars(
        select(AutonomyPolicyVersion)
        .where(AutonomyPolicyVersion.owner_id == owner.id)
        .order_by(AutonomyPolicyVersion.revision.desc())
    ).all()
    seen = set()
    for pol in policies:
        if pol.deployment_id in seen:
            continue
        seen.add(pol.deployment_id)
        checked(pol)
        dep = paper.deployment(db, owner, pol.deployment_id)
        if not pol.payload["enabled"] or not enabled_monitor(db, dep):
            continue
        signal = db.scalar(
            select(StrategyRun)
            .where(
                StrategyRun.deployment_id == dep.id,
                StrategyRun.payload["run_mode"].as_string() == "scheduled",
            )
            .order_by(StrategyRun.created_at.desc(), StrategyRun.id.desc())
            .limit(1)
        )
        if not signal or signal.action not in ("enter", "exit"):
            continue
        kind = "entry" if signal.action == "enter" else "exit"
        if (
            not pol.payload["auto_entries" if kind == "entry" else "auto_exits"]
            or datetime.fromisoformat(signal.payload["expires_at"]) <= now()
        ):
            continue
        signal_key = (
            signal.payload["signal_key"]
            if kind == "entry"
            else paper.checksum(
                {
                    "signal_key": signal.payload["signal_key"],
                    "ledger_digest": signal.payload["ledger_digest"],
                }
            )
        )
        if db.scalar(
            select(AutonomyWorkflow.id).where(
                AutonomyWorkflow.deployment_id == dep.id,
                AutonomyWorkflow.signal_key == signal_key,
                AutonomyWorkflow.kind == kind,
            )
        ):
            continue
        checked_signal = strategy_execution.checked(signal)
        acct = paper.account(db, owner, dep.account_id)
        _, policy = paper.policy(db, acct)
        nodes = paper.capital.current(db, acct)
        payload = {
            "deadline": min(
                datetime.fromisoformat(checked_signal.payload["expires_at"]),
                now() + timedelta(seconds=pol.payload["workflow_timeout_seconds"]),
            ).isoformat(),
            "signal_checksum": signal.checksum,
            "account_revision": acct.revision,
            "policy_id": policy.id,
            "ledger_digest": paper.ledger(db, acct)[3],
            "control_revision": paper.strategies.control(db, dep).revision,
            "capital_digest": paper.capital.digest(nodes),
        }
        model_error = None
        if kind == "entry":
            try:
                payload["models"] = settings_snapshot(db, owner, pol.payload["profile"])
            except HTTPException as failure:
                model_error = str(failure.detail)
        workflow = AutonomyWorkflow(
            id=identifier(),
            owner_id=owner.id,
            deployment_id=dep.id,
            policy_version_id=pol.id,
            initial_signal_id=signal.id,
            signal_key=signal_key,
            kind=kind,
            status="waiting_research" if kind == "entry" else "waiting_exit",
            payload=payload,
            checksum=paper.checksum(payload),
            next_check_at=now(),
            reason="Saved scheduled signal admitted for autonomous paper execution.",
            created_at=now(),
        )
        db.add(workflow)
        db.flush()
        event(db, workflow, "signal_admitted", strategy_run_id=signal.id, policy_version_id=pol.id)
        if model_error:
            workflow.status, workflow.reason, workflow.next_check_at = "blocked", model_error, None
            event(db, workflow, "blocked", reason=model_error)
        return workflow
    return None


def process_one(db):
    # Serialize queue admission first, preserving the review queue's lock order.
    if not db.scalar(select(func.pg_try_advisory_xact_lock(governance.QUEUE_LOCK))):
        return False
    if not db.scalar(select(func.pg_try_advisory_xact_lock(analysis.QUEUE_LOCK))):
        return False
    owners = db.scalars(
        select(Owner).where(Owner.id.in_(select(AutonomyPolicyVersion.owner_id))).order_by(Owner.id)
    ).all()
    for candidate in owners:
        owner = db.scalar(
            select(Owner).where(Owner.id == candidate.id).with_for_update(skip_locked=True)
        )
        if not owner:
            continue
        fresh = admit(db, owner)
        workflow = (
            fresh
            if fresh and (fresh.kind == "exit" or fresh.status in TERMINAL)
            else db.scalar(
                select(AutonomyWorkflow)
                .where(
                    AutonomyWorkflow.owner_id == owner.id, AutonomyWorkflow.next_check_at <= now()
                )
                .order_by(
                    AutonomyWorkflow.kind.desc(),
                    AutonomyWorkflow.next_check_at,
                    AutonomyWorkflow.created_at,
                )
                .with_for_update()
                .limit(1)
            )
        )
        if not workflow:
            continue
        if workflow.status not in TERMINAL:
            try:
                with db.begin_nested():
                    advance(db, owner, workflow)
                    db.flush()
            except HTTPException as failure:
                workflow.status, workflow.reason, workflow.next_check_at = (
                    "blocked",
                    str(failure.detail)[:500],
                    None,
                )
                event(db, workflow, "blocked", reason=workflow.reason)
        db.commit()
        return True
    db.rollback()
    return False


def heartbeat(db):
    db.execute(
        insert(WorkerStatus)
        .values(id=WORKER_ID, heartbeat_at=now(), version="paper-autonomy-v1")
        .on_conflict_do_update(
            index_elements=[WorkerStatus.id],
            set_={"heartbeat_at": now(), "version": "paper-autonomy-v1"},
        )
    )
    db.commit()
