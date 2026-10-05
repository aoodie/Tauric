"""Separate bound OpenClaw demo-entry permission, consumed by one broker intent."""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import func, select

from . import (
    admission_budget,
    broker_execution,
    broker_risk,
    broker_setup,
    governance,
    native_broker_setup,
    review_queue,
    services,
)
from .broker_contracts import (
    BrokerOrderRequest,
    BrokerReviewDecisionView,
    BrokerReviewedEntrySubmit,
    BrokerReviewResult,
    BrokerReviewView,
    BrokerSetupRequest,
)
from .broker_models import (
    BrokerOrderEvent,
    BrokerOrderIntent,
    BrokerReviewDecision,
    BrokerReviewJob,
)
from .models import AnalysisArtifact, GatewayStatus, Owner, identifier, now


def fail(detail, status=409):
    raise HTTPException(status, detail)


def checked(db, owner, job_id, *, lock=False):
    query = select(BrokerReviewJob).where(
        BrokerReviewJob.id == str(job_id), BrokerReviewJob.owner_id == owner.id
    )
    row = db.scalar(query.with_for_update() if lock else query)
    if not row:
        fail("This broker review does not exist.", 404)
    if native_broker_setup.checksum(row.context) != row.checksum:
        fail("Broker review context integrity failed.")
    return row


def decision(db, owner, decision_id):
    row = db.get(BrokerReviewDecision, str(decision_id))
    if not row or row.owner_id != owner.id:
        fail("This broker permission does not exist.", 404)
    job = checked(db, owner, row.job_id)
    if (
        native_broker_setup.checksum(row.payload) != row.checksum
        or job.status != "succeeded"
        or job.decision_id != row.id
        or row.assessment_job_id != job.assessment_job_id
        or row.native_artifact_id != job.context["native_original"]["id"]
        or row.policy_version_id != job.context["setup_evidence"]["policy_id"]
        or row.payload.get("context_checksum") != job.checksum
        or row.payload.get("approved_order") != job.context["approved_order"]
    ):
        fail("Broker permission lineage cannot be verified.")
    return row, job


def view(db, job):
    owner = db.get(Owner, job.owner_id)
    checked(db, owner, job.id)
    outcome, intent_id = None, None
    if job.decision_id:
        row, _ = decision(db, owner, job.decision_id)
        outcome = BrokerReviewDecisionView(
            id=row.id,
            outcome=row.payload["outcome"],
            reason=row.payload["reason"],
            authority=row.payload["authority"],
            approved_order=row.payload["approved_order"],
            expires_at=row.payload["expires_at"],
            created_at=row.created_at,
        )
        intent_id = db.scalar(
            select(BrokerOrderIntent.id).where(BrokerOrderIntent.review_decision_id == row.id)
        )
    return BrokerReviewView(
        id=job.id,
        assessment_job_id=job.assessment_job_id,
        profile=job.profile,
        model=job.context["model"]["model"],
        status=job.status,
        submit_on_allow=job.request["submit_on_allow"],
        context_checksum=job.checksum,
        decision=outcome,
        intent_id=intent_id,
        usage=job.usage,
        error=job.error,
        created_at=job.created_at,
        finished_at=job.finished_at,
    )


def views(db, owner):
    return [
        view(db, row)
        for row in db.scalars(
            select(BrokerReviewJob)
            .where(BrokerReviewJob.owner_id == owner.id)
            .order_by(BrokerReviewJob.created_at.desc())
            .limit(30)
        )
    ]


def fresh_setup(db, owner, assessment_job_id):
    report = broker_setup.report(db, owner, assessment_job_id)
    if (
        not report.evidence_current
        or not report.assessment
        or report.assessment.decision != "approve"
    ):
        fail("A current exact native setup approval is required before broker governance.", 422)
    original_context = report.job.request["broker_context"]
    evidence, _ = native_broker_setup.verify(original_context)
    actual = broker_setup.capture(
        db, owner, BrokerSetupRequest.model_validate(evidence["request"])
    )["assessment_input"]
    if (
        not actual["entry_evidence_ready"]
        or any(
            actual["signal"].get(key) != evidence["signal"].get(key)
            for key in ("action", "bar_close_time", "bars", "stop_price")
        )
        or actual["policy_id"] != evidence["policy_id"]
        or actual["account"]["account_fingerprint"] != evidence["account"]["account_fingerprint"]
    ):
        fail("The exact native-approved closed-bar entry changed or fresh broker evidence failed.")
    order = BrokerOrderRequest.model_validate(evidence["planned_order"])
    try:
        broker_risk.validate_current(evidence, actual)
    except broker_execution.BrokerError as error:
        fail(str(error))
    if (
        not Decimal(order.stop_loss)
        < Decimal(actual["quote"]["bid"])
        <= Decimal(actual["quote"]["ask"])
        < Decimal(order.take_profit)
    ):
        fail("The current broker quote has crossed the reviewed entry's stop or target.")
    # Prices stay exactly as natively reviewed. A quote is an observation, not a fill guarantee.
    return report, evidence, actual, order


def holdings(evidence):
    return sorted(
        (row["symbol"], row["side"], row["quantity"], row["average_entry_price"])
        for row in evidence["existing_positions"]
    )


def enqueue(db, owner, body, *, commit=True):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    db.execute(select(func.pg_advisory_xact_lock(review_queue.LOCK)))
    request = body.model_dump(mode="json")
    old = db.scalar(
        select(BrokerReviewJob).where(
            BrokerReviewJob.owner_id == owner.id, BrokerReviewJob.request_id == str(body.request_id)
        )
    )
    if old:
        if old.request != request:
            fail("This broker review request ID already belongs to other inputs.")
        return view(db, old)
    if db.scalar(
        select(BrokerReviewJob.id).where(
            BrokerReviewJob.assessment_job_id == str(body.assessment_job_id),
            BrokerReviewJob.status.in_(["queued", "running"]),
        )
    ) or db.scalar(
        select(BrokerReviewDecision.id).where(
            BrokerReviewDecision.assessment_job_id == str(body.assessment_job_id)
        )
    ):
        fail("This native setup already has an active or finalized broker review.")
    report, evidence, actual, order = fresh_setup(db, owner, str(body.assessment_job_id))
    current = governance.status(db, owner)
    gateway = db.get(GatewayStatus, "openclaw")
    if not gateway or "broker_entry_review" not in gateway.capabilities:
        fail("The private Gateway has not activated the bound demo broker review tool.", 503)
    if not next(route.ready for route in current.routes if route.id == body.profile):
        fail(
            "Configure the selected OpenClaw connection and start its private review services.", 503
        )
    if body.submit_on_allow and not broker_execution.state(db, owner).worker_ready:
        fail(
            "The demo broker worker must be ready for automatic delivery of this approved entry.",
            503,
        )
    if review_queue.active_count(db) >= governance.QUEUE_LIMIT:
        fail("The shared OpenClaw review queue is full. Wait for a review to finish.", 429)
    config = governance.routing(db, owner)
    artifact = db.get(AnalysisArtifact, report.native_artifact_id)
    context = {
        "authority": "read_only_demo_broker_entry",
        "assessment_job_id": report.job.id,
        "setup_evidence": evidence,
        "setup_evidence_checksum": report.job.request["broker_context"]["evidence_checksum"],
        "native_assessment": report.assessment.model_dump(mode="json"),
        "native_original": {
            "id": artifact.id,
            "checksum": artifact.checksum,
            "payload": artifact.payload,
        },
        "fresh_broker_evidence": actual,
        "approved_order": order.model_dump(mode="json"),
        "model": {
            key: value for key, value in config["routes"][body.profile].items() if key != "api_key"
        },
        "settings": config["settings"],
        "routing_fingerprint": config["fingerprint"],
        "expires_at": evidence["expires_at"],
        "limitations": "One exact OANDA practice or Alpaca paper long entry. Native reports and rating are unchanged. Native exact setup approval is required; OpenClaw may veto or defer. Owner requests no extra capital, per-trade, exposure, position or daily-loss caps. Paper simulator capital/risk values are not broker caps. Broker increments, margin/buying power, account restrictions and fresh quote/rule checks remain authoritative. Quote observations are not guaranteed fills. This is not full strategy qualification or real-money permission. OpenClaw has no order tool; one validated allow can be consumed by one durable platform intent.",
    }
    if len(json.dumps(context).encode()) > 1_000_000:
        fail("Original research exceeds this broker review's context budget.", 422)
    admission_budget.check(db, owner)
    row = BrokerReviewJob(
        id=identifier(),
        owner_id=owner.id,
        request_id=str(body.request_id),
        assessment_job_id=report.job.id,
        profile=body.profile,
        request=request,
        context=context,
        checksum=native_broker_setup.checksum(context),
        status="queued",
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "broker.review_queued",
        "broker_review",
        row.id,
        body.reason,
        {
            "assessment_job_id": report.job.id,
            "profile": body.profile,
            "submit_on_allow": body.submit_on_allow,
        },
    )
    if commit:
        db.commit()
    return view(db, row)


def static_context(db, job):
    owner = db.get(Owner, job.owner_id)
    checked(db, owner, job.id)
    if datetime.fromisoformat(job.context["expires_at"]) <= now():
        fail("The exact broker review context expired. A new native setup is required.")
    report = broker_setup.report(db, owner, job.assessment_job_id)
    if (
        not report.evidence_current
        or not report.assessment
        or report.assessment.decision != "approve"
        or report.native_artifact_id != job.context["native_original"]["id"]
        or report.assessment.model_dump(mode="json") != job.context["native_assessment"]
        or report.job.request["broker_context"]["evidence_checksum"]
        != job.context["setup_evidence_checksum"]
        or governance.routing(db, owner)["fingerprint"] != job.context["routing_fingerprint"]
    ):
        fail("The broker setup, policy, native evidence or model routing changed.")
    return owner


def valid_context(db, job, *, require_gateway=True):
    owner = static_context(db, job)
    if require_gateway:
        gateway = governance.status(db, owner)
        capabilities = db.get(GatewayStatus, "openclaw")
        if (
            not gateway.gateway_ready
            or not capabilities
            or "broker_entry_review" not in capabilities.capabilities
        ):
            fail("The private Gateway is unavailable or has not applied these model settings.")
    _, _, actual, _ = fresh_setup(db, owner, job.assessment_job_id)
    captured = job.context["fresh_broker_evidence"]
    if (
        holdings(actual) != holdings(captured)
        or actual["account"]["cash"] != captured["account"]["cash"]
    ):
        fail("Broker holdings or cash changed since the review was captured.")
    return owner, actual


def claim(db):
    return review_queue.claim(db, "broker", now())


def renew(db, job_id, lease):
    return review_queue.renew(db, BrokerReviewJob, job_id, lease, now())


def result_binding(job):
    evidence = job.context["setup_evidence"]
    return {
        "job_id": job.id,
        "context_checksum": job.checksum,
        "assessment_job_id": job.assessment_job_id,
        "native_artifact_id": job.context["native_original"]["id"],
        "strategy_version_id": evidence["request"]["strategy_version_id"],
        "policy_id": evidence["policy_id"],
        "provider": evidence["request"]["provider"],
        "instrument_id": evidence["request"]["instrument_id"],
        "side": "buy",
        "quantity": evidence["request"]["quantity"],
    }


def finish(db, job_id, lease, raw=None, usage=None, error=None):
    job = db.scalar(select(BrokerReviewJob).where(BrokerReviewJob.id == job_id).with_for_update())
    if (
        not job
        or job.status != "running"
        or job.lease_id != lease
        or not job.heartbeat_at
        or not 0 <= (now() - job.heartbeat_at).total_seconds() < review_queue.LEASE_SECONDS
    ):
        return False
    job.usage = {
        key: value
        for key, value in (usage or {}).items()
        if key in ("prompt_tokens", "completion_tokens", "total_tokens")
        and type(value) is int
        and 0 <= value <= 10_000_000
    }
    try:
        if error:
            fail(error)
        if now() - job.started_at > timedelta(seconds=job.context["settings"]["timeout_seconds"]):
            fail("The broker review reached its time budget. No automatic retry was made.")

        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate broker review field")
                result[key] = value
            return result

        if not isinstance(raw, str) or len(raw.encode()) > 10_000:
            raise ValueError("Invalid broker review result budget")
        result = BrokerReviewResult.model_validate(json.loads(raw, object_pairs_hook=unique_fields))
        if not job.context_read_at or not job.started_at <= job.context_read_at <= now():
            fail("OpenClaw did not retrieve this bound broker context. No permission was issued.")
        if any(
            result.model_dump(mode="json")[key] != value
            for key, value in result_binding(job).items()
        ):
            fail("The broker review returned different context identifiers or quantity.")
        owner, _ = valid_context(db, job)
        evidence = job.context["setup_evidence"]
        payload = {
            "job_id": job.id,
            "context_checksum": job.checksum,
            "outcome": result.outcome,
            "reason": result.reason,
            "authority": "demo_broker_entry",
            "source": "openclaw",
            "approved_order": job.context["approved_order"],
            "account_fingerprint": evidence["account"]["account_fingerprint"],
            "connection_revision": evidence["connection_revision"],
            "expires_at": job.context["expires_at"],
            "model": job.context["model"],
            "settings": job.context["settings"],
            "usage": job.usage,
            "openclaw_version": "2026.9.7",
        }
        row = BrokerReviewDecision(
            id=identifier(),
            owner_id=owner.id,
            job_id=job.id,
            assessment_job_id=job.assessment_job_id,
            native_artifact_id=job.context["native_original"]["id"],
            policy_version_id=evidence["policy_id"],
            provider=evidence["request"]["provider"],
            payload=payload,
            checksum=native_broker_setup.checksum(payload),
        )
        db.add(row)
        db.flush()
        job.decision_id, job.status = row.id, "succeeded"
    except HTTPException as failure:
        job.status, job.error = "failed", str(failure.detail)[:500]
    except (ValueError, TypeError, KeyError):
        job.status, job.error = (
            "failed",
            "The model did not return the required bound broker schema. No permission was issued.",
        )
    job.finished_at = now()
    db.commit()
    return True


def prepare_entry(db, owner, decision_id, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    review, job = decision(db, owner, decision_id)
    if review.payload["outcome"] != "allow":
        fail("This broker review did not permit an entry.", 422)
    existing = db.scalar(
        select(BrokerOrderIntent).where(BrokerOrderIntent.review_decision_id == review.id)
    )
    if existing:
        return broker_execution.view(db, broker_execution.owned(db, owner, existing.id))
    if db.scalar(
        select(BrokerOrderIntent.id).where(
            BrokerOrderIntent.owner_id == owner.id,
            BrokerOrderIntent.request_id == str(body.request_id),
        )
    ):
        fail("This entry request ID already belongs to another broker intent.")
    # Record even an expired permission as an intent; submission checks reject it
    # durably. A crash after review finalization therefore has a recoverable receipt.
    row_id = identifier()
    order = BrokerOrderRequest.model_validate(review.payload["approved_order"]).model_copy(
        update={"client_order_id": "tauric_" + row_id.replace("-", "")}
    )
    evidence = job.context["setup_evidence"]
    request = {
        "request_id": str(body.request_id),
        "reason": body.reason,
        "expected_connection_revision": review.payload["connection_revision"],
        **order.model_dump(mode="json", exclude={"client_order_id"}),
    }
    payload = {
        "request": request,
        "order": order.model_dump(mode="json"),
        "source": "openclaw_broker_review",
        "review_decision_id": review.id,
        "review_job_id": job.id,
        "native_artifact_id": review.native_artifact_id,
        "assessment_job_id": job.assessment_job_id,
        "strategy_version_id": evidence["request"]["strategy_version_id"],
    }
    row = BrokerOrderIntent(
        id=row_id,
        owner_id=owner.id,
        provider=review.provider,
        request_id=str(body.request_id),
        client_order_id=order.client_order_id,
        policy_version_id=review.policy_version_id,
        connection_revision=review.payload["connection_revision"],
        account_fingerprint=review.payload["account_fingerprint"],
        review_decision_id=review.id,
        payload=payload,
        checksum=broker_execution.checksum(payload),
        status="prepared",
    )
    db.add(row)
    db.flush()
    broker_execution.receipt(
        db,
        row,
        "prepared",
        "Exact OpenClaw-reviewed demo entry recorded; no broker request submitted yet.",
    )
    services.audit(
        db,
        owner,
        "broker.reviewed_entry_prepared",
        "broker_order",
        row.id,
        body.reason,
        {"review_decision_id": review.id, "strategy_version_id": payload["strategy_version_id"]},
    )
    db.commit()
    return broker_execution.submit(db, owner, row.id)


def validate_submission(db, row):
    owner = db.get(Owner, row.owner_id)
    review, job = decision(db, owner, row.review_decision_id)
    if review.payload["outcome"] != "allow" or row.payload["source"] != "openclaw_broker_review":
        fail("This broker entry has no verified allow permission.")
    _, actual = valid_context(db, job, require_gateway=False)
    history = broker_execution.events(db, row)
    payload = {
        "detail": "Native setup, bound OpenClaw permission and fresh broker/rule evidence verified before submission.",
        "review_decision_id": review.id,
        "evidence": actual,
        "evidence_checksum": native_broker_setup.checksum(actual),
    }
    db.add(
        BrokerOrderEvent(
            owner_id=owner.id,
            intent_id=row.id,
            sequence=(history[-1].sequence if history else 0) + 1,
            kind="review_execution_checked",
            payload=payload,
            checksum=broker_execution.checksum(payload),
        )
    )
    db.flush()
    return job.id, actual["quote"]["observed_at"]


def final_effect_guard(db, owner, job_id, quote_time):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    job = checked(db, owner, job_id)
    static_context(db, job)
    if not 0 <= (now() - datetime.fromisoformat(quote_time)).total_seconds() <= 60:
        fail("The broker quote expired before the submission effect.")


def deliver_one(db):
    row = db.scalar(
        select(BrokerReviewDecision)
        .join(BrokerReviewJob, BrokerReviewJob.id == BrokerReviewDecision.job_id)
        .outerjoin(
            BrokerOrderIntent, BrokerOrderIntent.review_decision_id == BrokerReviewDecision.id
        )
        .where(
            BrokerReviewJob.request["submit_on_allow"].as_boolean() == True,  # noqa: E712
            BrokerReviewDecision.payload["outcome"].as_string() == "allow",
            BrokerOrderIntent.id.is_(None),
        )
        .order_by(BrokerReviewDecision.created_at)
        .limit(1)
    )
    if not row:
        return False
    owner = db.get(Owner, row.owner_id)
    prepare_entry(
        db,
        owner,
        row.id,
        BrokerReviewedEntrySubmit(
            request_id=uuid5(NAMESPACE_URL, "tauric-broker-review:" + row.id),
            reason="Deliver the exact demo entry permitted by the requested OpenClaw review",
        ),
    )
    return True
