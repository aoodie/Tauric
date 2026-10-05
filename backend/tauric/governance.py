"""Leased paper governance. Only validated, current results become decisions."""

import hashlib
import hmac
import json
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from . import model_profiles, paper, services
from .config import runtime_config
from .governance_contracts import GovernanceJobView, GovernanceResult, GovernanceStatusView
from .models import (
    AnalysisArtifact,
    AnalysisJob,
    GatewayStatus,
    GovernanceJob,
    Owner,
    TradingRecord,
    WorkerStatus,
    identifier,
    now,
)
from .trading_contracts import TradingRecordView

QUEUE_LOCK = 830603
LEASE_SECONDS = 45
QUEUE_LIMIT = 4
SETTING_KEYS = ["cloud_model_tier", "local_model_tier", "timeout_seconds", "max_output_tokens"]


def private_tokens():
    try:
        value = json.loads(Path(runtime_config().openclaw_private_file).read_text())
        if any(
            not isinstance(value.get(k), str) or len(value[k]) < 40
            for k in ("platform_token", "gateway_token")
        ):
            raise ValueError()
        return value
    except (OSError, ValueError, TypeError):
        paper.fail("Private OpenClaw service access is not configured.", 503)


def routing(db, owner):
    """Server-only credential export. Fingerprints also detect key rotation."""
    settings = {
        k: services.setting_view(db, owner, "governance." + k).effective_value for k in SETTING_KEYS
    }
    profiles, _ = model_profiles.load_profiles(db)
    routes = {}
    for key, profile in profiles.items():
        if not profile.configured():
            continue
        head = model_profiles.connection_head(db, key)
        tier = settings[key + "_model_tier"]
        routes[key] = {
            **profile.public_config(),
            "api_key": profile.api_key.get_secret_value(),
            "model": getattr(profile, tier + "_model"),
            "tier": tier,
            "revision": head.revision if head else 0,
        }
    from .chat import load_config

    chat_config, chat_revision = load_config(db)
    export = {
        "broker_review_protocol": "tauric_broker_review_v1",
        "routes": routes,
        "settings": settings,
        "chat": chat_config.model_dump(),
        "chat_revision": chat_revision,
    }
    export["fingerprint"] = hmac.new(
        private_tokens()["platform_token"].encode(),
        json.dumps(export, sort_keys=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    return export


def proposal_context(db, owner, prop, required=False):
    acct = paper.account(db, owner, prop.account_id)
    dep = paper.deployment(db, owner, prop.deployment_id)
    from .autonomous import guard_proposal

    guard_proposal(db, owner, dep, prop)
    paper.strategies.validate_proposal(db, dep, prop)
    paper.capital.validate_proposal(db, acct, dep, prop)
    _, pol = paper.policy(db, acct)
    original = None
    if prop.payload["native_artifact_id"]:
        artifact = db.get(AnalysisArtifact, prop.payload["native_artifact_id"])
        job = db.get(AnalysisJob, artifact.job_id) if artifact else None
        if (
            not artifact
            or artifact.owner_id != owner.id
            or artifact.checksum != prop.payload["native_checksum"]
            or hashlib.sha256(
                json.dumps(artifact.payload, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            != artifact.checksum
            or not job
            or job.owner_id != owner.id
            or job.status != "succeeded"
            or job.instrument_id != dep.instrument_id
        ):
            paper.fail("Original native evidence cannot be verified.")
        original = {"id": artifact.id, "checksum": artifact.checksum, "payload": artifact.payload}
    if required and not original:
        paper.fail(
            "OpenClaw review needs a linked, successful original native report. Run native analysis first.",
            422,
        )
    if original and (
        job.request.get("mode") == "strategy_qualification"
        or "strategy_assessment" in artifact.payload
    ):
        paper.fail("Strategy qualification evidence cannot substitute for entry research.", 422)
    if original and (
        job.request.get("mode") == "broker_entry_assessment"
        or "broker_setup_assessment" in artifact.payload
    ):
        paper.fail(
            "Broker setup assessment must use its separate bound broker governance path.", 422
        )
    if required and (
        job.request.get("trade_date") != now().date().isoformat()
        or not isinstance(artifact.payload.get("reports", {}).get("final_trade_decision"), str)
        or not artifact.payload["reports"]["final_trade_decision"].strip()
        or artifact.payload.get("execution_authority") is not False
        or artifact.payload.get("governance") is not None
    ):
        paper.fail(
            "Native research is missing required evidence or its current daily context has expired."
        )
    return {
        "authority": "read_only_paper_context",
        "proposal": TradingRecordView.model_validate(prop).model_dump(mode="json"),
        "account": paper.account_view(db, acct).model_dump(mode="json"),
        "positions": [
            paper.position_view(db, entry).model_dump(mode="json")
            for entry in paper.ledger(db, acct)[2].values()
        ],
        "deployment": paper.deployment_view(dep, db).model_dump(mode="json"),
        "strategy_version": paper.strategies.view(
            paper.strategies.version(db, owner, dep.payload["strategy_revision_id"])
        ).model_dump(mode="json")
        if dep.payload.get("strategy_revision_id")
        else None,
        "policy_id": pol.id,
        "capital_allocations": [v.model_dump(mode="json") for v in paper.capital.views(db, acct)],
        "native_original": original,
        "limitations": (
            "Autonomous long spot paper proposal. Original Tauric BUY context is required; OpenClaw may reject or defer but cannot reverse an adverse native rating. "
            if prop.payload.get("autonomous_context")
            else "Owner-authored long spot paper proposal. "
        )
        + "Native daily USD research is daily context, not a certified intraday setup assessment or exact strategy-version evaluation. USDT entry timing is determined by separately linked closed-candle rules. No live order authority. Stops are sizing references; enabled periodic rule exits are paper actions rather than protective broker orders. Assigned portfolios and nested pots enforce fixed shared budgets in the deterministic paper planner. Legacy labels are unallocated and cannot open new entries once this account has shared allocations.",
    }


def view(job):
    if paper.checksum(job.context) != job.checksum:
        paper.fail("Review context integrity failed.")
    return GovernanceJobView(
        id=job.id,
        proposal_id=job.proposal_id,
        profile=job.profile,
        model=job.context["model"]["model"],
        status=job.status,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        decision_id=job.decision_id,
        error=job.error,
        context_checksum=job.checksum,
        usage=job.usage,
    )


def status(db, owner):
    worker = db.get(WorkerStatus, "governance")
    worker_ready = bool(worker and now() - worker.heartbeat_at < timedelta(seconds=LEASE_SECONDS))
    gateway = db.get(GatewayStatus, "openclaw")
    try:
        config = routing(db, owner)
        gateway_ready = bool(
            gateway
            and now() - gateway.heartbeat_at < timedelta(seconds=30)
            and gateway.fingerprint == config["fingerprint"]
        )
        routes = config["routes"]
    except HTTPException:
        gateway_ready, routes = False, {}
    return GovernanceStatusView(
        worker_ready=worker_ready,
        gateway_ready=gateway_ready,
        routes=[
            dict(
                id=k,
                configured=k in routes,
                ready=k in routes and worker_ready and gateway_ready,
                model=routes[k]["model"] if k in routes else None,
                detail="Ready for requested reviews; model access is verified only by a successful run."
                if k in routes and worker_ready and gateway_ready
                else "Configure this connection in Settings → AI connections."
                if k not in routes
                else "Waiting for the private Gateway and review worker to apply your settings.",
            )
            for k in ("cloud", "local")
        ],
        jobs=[
            view(j)
            for j in db.scalars(
                select(GovernanceJob)
                .where(GovernanceJob.owner_id == owner.id)
                .order_by(GovernanceJob.created_at.desc())
                .limit(100)
            )
        ],
    )


def pending(db, proposal_id):
    return db.scalar(
        select(GovernanceJob.id).where(
            GovernanceJob.proposal_id == proposal_id,
            GovernanceJob.status.in_(["queued", "running"]),
        )
    )


def enqueue(db, owner, body, *, commit=True):
    db.execute(select(func.pg_advisory_xact_lock(QUEUE_LOCK)))
    request = body.model_dump(mode="json")
    old = db.scalar(
        select(GovernanceJob).where(
            GovernanceJob.owner_id == owner.id, GovernanceJob.request_id == str(body.request_id)
        )
    )
    if old:
        if old.request != request:
            paper.fail("This request identifier was already used for different inputs.")
        return view(old)
    prop = paper.record(db, owner, body.proposal_id, "proposal")
    acct = paper.account(db, owner, prop.account_id)
    context = proposal_context(db, owner, prop, required=True)
    if acct.halted or context["policy_id"] != prop.payload["policy_id"]:
        paper.fail("The account is halted or policy changed. Create a new proposal.")
    if pending(db, prop.id) or db.scalar(
        select(TradingRecord.id).where(
            TradingRecord.kind == "governance", TradingRecord.consumes_id == prop.id
        )
    ):
        paper.fail("This proposal already has a review or an active review request.")
    limits, _ = paper.policy(db, acct)
    observed, snapshot = paper.quote(db, prop.payload["instrument_id"], limits)
    context["market_observation"] = {
        "snapshot_id": snapshot.snapshot_id,
        "checksum": snapshot.checksum,
        "observed_price": paper.decimal_text(observed),
        "currency": "USDT",
        "received_at": snapshot.received_at.isoformat(),
        "provider_time": snapshot.provider_time.isoformat(),
        "source": snapshot.source,
        "price_model": "observed_at_receipt_candle_close",
    }
    current = status(db, owner)
    if not next(r.ready for r in current.routes if r.id == body.profile):
        paper.fail(
            "This model route is not ready. Configure AI connections and start the private governance services.",
            503,
        )
    from . import review_queue

    if review_queue.active_count(db) >= QUEUE_LIMIT:
        paper.fail("The review queue is full. Wait for a review to finish.", 429)
    config = routing(db, owner)
    context["model"] = {k: v for k, v in config["routes"][body.profile].items() if k != "api_key"}
    context["routing_fingerprint"] = config["fingerprint"]
    context["settings"] = config["settings"]
    context["ledger_digest"] = paper.ledger(db, acct)[3]
    context["expires_at"] = (now() + timedelta(seconds=limits.decision_ttl_seconds)).isoformat()
    if len(json.dumps(context).encode()) > 1_000_000:
        paper.fail("Original research exceeds this review's context budget.", 422)
    from . import admission_budget

    admission_budget.check(db, owner)
    row = GovernanceJob(
        id=identifier(),
        owner_id=owner.id,
        request_id=str(body.request_id),
        proposal_id=prop.id,
        profile=body.profile,
        request=request,
        context=context,
        checksum=paper.checksum(context),
        status="queued",
    )
    db.add(row)
    services.audit(
        db,
        owner,
        "governance.queued",
        "governance_job",
        row.id,
        body.reason,
        {"profile": body.profile, "proposal_id": prop.id, "mode": "paper"},
    )
    db.flush()
    if commit:
        db.commit()
    return view(row)


def heartbeat(db):
    db.execute(
        insert(WorkerStatus)
        .values(id="governance", heartbeat_at=now(), version="2026.9.7")
        .on_conflict_do_update(
            index_elements=["id"], set_={"heartbeat_at": now(), "version": "2026.9.7"}
        )
    )
    db.commit()


def claim(db):
    from . import review_queue

    return review_queue.claim(db, "paper", now())


def valid_context(db, job):
    if paper.checksum(job.context) != job.checksum:
        paper.fail("Review context integrity failed.")
    if datetime.fromisoformat(job.context["expires_at"]) <= now():
        paper.fail("The captured proposal review context expired. Create a new request.")
    owner = db.get(Owner, job.owner_id)
    prop = paper.record(db, owner, job.proposal_id, "proposal")
    acct = paper.account(db, owner, prop.account_id)
    actual = proposal_context(db, owner, prop, required=True)
    if (
        acct.halted
        or acct.revision != job.context["account"]["revision"]
        or actual["policy_id"] != job.context["policy_id"]
        or paper.ledger(db, acct)[3] != job.context["ledger_digest"]
        or prop.checksum != job.context["proposal"]["checksum"]
        or routing(db, owner)["fingerprint"] != job.context["routing_fingerprint"]
    ):
        paper.fail("Account, policy, research or model routing changed. Request a new review.")
    gateway = db.get(GatewayStatus, "openclaw")
    if (
        not gateway
        or gateway.fingerprint != job.context["routing_fingerprint"]
        or now() - gateway.heartbeat_at >= timedelta(seconds=30)
    ):
        paper.fail(
            "The private Gateway is unavailable or has not applied this model configuration."
        )
    if db.scalar(
        select(TradingRecord.id).where(
            TradingRecord.kind == "governance", TradingRecord.consumes_id == prop.id
        )
    ):
        paper.fail("This proposal already has a finalized review.")
    return owner, prop, acct


def renew(db, job_id, lease):
    from . import review_queue

    return review_queue.renew(db, GovernanceJob, job_id, lease, now())


def finish(db, job_id, lease, raw=None, usage=None, error=None):
    job = db.scalar(select(GovernanceJob).where(GovernanceJob.id == job_id).with_for_update())
    if (
        not job
        or job.status != "running"
        or job.lease_id != lease
        or now() - job.heartbeat_at >= timedelta(seconds=LEASE_SECONDS)
    ):
        return False
    job.usage = {
        k: v
        for k, v in (usage or {}).items()
        if k in ("prompt_tokens", "completion_tokens", "total_tokens")
        and type(v) is int
        and 0 <= v <= 10_000_000
    }
    try:
        if error:
            paper.fail(error)
        if now() - job.started_at > timedelta(seconds=job.context["settings"]["timeout_seconds"]):
            paper.fail("The review reached its time budget. No automatic retry was made.")

        def unique_fields(pairs):
            values = {}
            for key, value in pairs:
                if key in values:
                    raise ValueError("Duplicate governance field")
                values[key] = value
            return values

        if not isinstance(raw, str) or len(raw.encode()) > 10_000:
            raise ValueError("Governance result exceeds its budget")
        result = GovernanceResult.model_validate(json.loads(raw, object_pairs_hook=unique_fields))
        if not job.context_read_at or job.context_read_at < job.started_at:
            paper.fail(
                "OpenClaw did not retrieve the bound review context. No permission was issued."
            )
        if (
            str(result.job_id) != job.id
            or result.context_checksum != job.checksum
            or str(result.proposal_id) != job.proposal_id
            or str(result.policy_id) != job.context["policy_id"]
        ):
            paper.fail("The review returned different context identifiers.")
        owner, prop, acct = valid_context(db, job)
        limits, pol = paper.policy(db, acct)
        row = paper.append(
            db,
            owner,
            acct,
            "governance",
            job.request_id,
            {
                "request": job.request,
                "reason": result.reason,
                "proposal_id": prop.id,
                "proposal_checksum": prop.checksum,
                "outcome": result.outcome,
                "source": "openclaw",
                "authority": "paper_only",
                "policy_id": pol.id,
                "account_revision": acct.revision,
                "ledger_digest": job.context["ledger_digest"],
                "expires_at": (now() + timedelta(seconds=limits.decision_ttl_seconds)).isoformat(),
                "native_artifact_id": prop.payload["native_artifact_id"],
                "job_id": job.id,
                "context_checksum": job.checksum,
                "model": job.context["model"],
                "settings": job.context["settings"],
                "usage": job.usage,
                "openclaw_version": "2026.9.7",
            },
            prop.deployment_id,
            prop.id,
        )
        job.decision_id, job.status = row.id, "succeeded"
    except HTTPException as exc:
        job.status, job.error = "failed", exc.detail
    except (ValueError, TypeError):
        job.status, job.error = (
            "failed",
            "The model did not return the required governance schema. No permission was issued.",
        )
    job.finished_at = now()
    db.commit()
    return True
