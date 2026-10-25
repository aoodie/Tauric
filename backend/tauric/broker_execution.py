"""Broker execution coordinator: commit intent before I/O, reconcile uncertain writes."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from . import services
from .broker_adapters import BrokerError, adapter
from .broker_contracts import (
    BrokerConnectionStatus,
    BrokerEventView,
    BrokerExecutionState,
    BrokerIntentView,
    BrokerOrderRequest,
    BrokerOrderView,
    BrokerPolicyView,
)
from .broker_models import BrokerOrderEvent, BrokerOrderIntent, BrokerPolicyVersion
from .market_connections import stored_connection
from .models import Owner, WorkerStatus, identifier, now

TERMINAL = {"filled", "cancelled", "rejected", "expired"}


def checksum(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def latest_policy(db, owner, provider):
    row = db.scalar(
        select(BrokerPolicyVersion)
        .where(BrokerPolicyVersion.owner_id == owner.id, BrokerPolicyVersion.provider == provider)
        .order_by(BrokerPolicyVersion.revision.desc())
        .limit(1)
    )
    if row and row.checksum != checksum(row.payload):
        raise HTTPException(409, "Broker execution policy integrity failed.")
    return row


def policy_view(row):
    return BrokerPolicyView(
        id=row.id,
        provider=row.provider,
        revision=row.revision,
        settings=row.payload,
        created_at=row.created_at,
    )


def connection(db, provider):
    config, revision, _ = stored_connection(db, provider)
    if not revision:
        raise HTTPException(
            409, "Save this demo broker connection in Settings before enabling execution."
        )
    return config, revision


def check_account(db, provider):
    config, revision, _ = stored_connection(db, provider)
    configured = (
        bool(config.token.get_secret_value())
        if provider == "oanda"
        else bool(config.key.get_secret_value() and config.secret.get_secret_value())
    )
    result = BrokerConnectionStatus(
        provider=provider,
        configured=configured,
        environment=config.environment,
        demo_environment=config.environment == ("practice" if provider == "oanda" else "paper"),
        connection_revision=revision,
    )
    try:
        with adapter(provider, config) as broker:
            result.account = broker.account()
            result.positions = broker.positions()
    except BrokerError as error:
        result.error = str(error)
    return result


def save_policy(db, owner, provider, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    payload = body.model_dump(mode="json")
    existing = db.scalar(
        select(BrokerPolicyVersion).where(
            BrokerPolicyVersion.owner_id == owner.id,
            BrokerPolicyVersion.request_id == str(body.request_id),
        )
    )
    if existing:
        if existing.provider != provider or existing.payload.get("request") != payload:
            raise HTTPException(
                409, "This broker policy request ID was already used for another change."
            )
        return policy_view(existing)
    current = latest_policy(db, owner, provider)
    if body.expected_revision != (current.revision if current else 0):
        raise HTTPException(409, "Reload the current broker policy before changing it.")
    account = None
    revision = None
    if body.enabled:
        config, revision = connection(db, provider)
        try:
            with adapter(provider, config) as broker:
                account = broker.account()
        except BrokerError as error:
            raise HTTPException(409, str(error)) from None
        if not account.trading_enabled:
            raise HTTPException(409, "The demo broker account is not enabled for trading.")
        if account.environment != config.environment:
            raise HTTPException(
                409, "The broker account environment differs from its saved connection."
            )
        if config.environment == "live":
            from . import live_trading

            if not body.live_acknowledged or not body.forward_report_id:
                raise HTTPException(
                    409,
                    "Live activation requires acknowledgment and a passing forward-validation report.",
                )
            report = live_trading.eligible_report(db, owner, body.forward_report_id, provider)
            protocol = live_trading.owned(db, owner, report.parent_id, "forward_protocol")
            if account.currency != protocol.payload["currency"]:
                raise HTTPException(
                    409, "Live account currency must match the validated demo account currency."
                )
    snapshot = {
        "request": payload,
        "enabled": body.enabled,
        "no_owner_limits": body.no_owner_limits,
        "maximum_risk_percent": body.maximum_risk_percent,
        "maximum_drawdown_percent": body.maximum_drawdown_percent,
        "connection_revision": revision,
        "account_fingerprint": account.account_fingerprint if account else None,
        "environment": account.environment if account else None,
        "automatic_entries_enabled": False,
        "forward_report_id": str(body.forward_report_id) if body.forward_report_id else None,
        "risk_authority": "tauric_native_ceiling_openclaw_reduce",
    }
    row = BrokerPolicyVersion(
        owner_id=owner.id,
        provider=provider,
        revision=body.expected_revision + 1,
        request_id=str(body.request_id),
        payload=snapshot,
        checksum=checksum(snapshot),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "broker.policy_changed",
        "broker_policy",
        row.id,
        body.reason,
        {
            "provider": provider,
            "revision": row.revision,
            "enabled": body.enabled,
            "no_owner_limits": body.no_owner_limits,
        },
    )
    db.commit()
    return policy_view(row)


def owned(db, owner, intent_id, *, lock=False):
    query = select(BrokerOrderIntent).where(
        BrokerOrderIntent.id == intent_id, BrokerOrderIntent.owner_id == owner.id
    )
    row = db.scalar(
        query.with_for_update().execution_options(populate_existing=True) if lock else query
    )
    if not row:
        raise HTTPException(404, "This broker order does not exist.")
    if row.checksum != checksum(row.payload):
        raise HTTPException(409, "Broker order intent integrity failed.")
    return row


def events(db, row):
    result = db.scalars(
        select(BrokerOrderEvent)
        .where(BrokerOrderEvent.intent_id == row.id)
        .order_by(BrokerOrderEvent.sequence)
    ).all()
    for event in result:
        if event.checksum != checksum(event.payload):
            raise HTTPException(409, "Broker order receipt integrity failed.")
    return result


def view(db, row):
    history = events(db, row)
    last = next((e.payload["order"] for e in reversed(history) if e.payload.get("order")), None)
    return BrokerIntentView(
        id=row.id,
        provider=row.provider,
        client_order_id=row.client_order_id,
        status=row.status,
        request=row.payload["request"],
        latest_order=BrokerOrderView.model_validate(last) if last else None,
        detail=history[-1].payload.get("detail", "") if history else "",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def receipt(db, row, kind, detail, order=None):
    history = events(db, row)
    payload = {"detail": detail, "order": order.model_dump(mode="json") if order else None}
    if history and history[-1].kind == kind:
        previous = history[-1].payload
        current_order = dict(payload["order"] or {})
        previous_order = dict(previous.get("order") or {})
        current_order.pop("observed_at", None)
        previous_order.pop("observed_at", None)
        if previous.get("detail") == detail and current_order == previous_order:
            row.updated_at = now()
            db.flush()
            return
    sequence = (history[-1].sequence if history else 0) + 1
    db.add(
        BrokerOrderEvent(
            owner_id=row.owner_id,
            intent_id=row.id,
            sequence=sequence,
            kind=kind,
            payload=payload,
            checksum=checksum(payload),
        )
    )
    row.updated_at = now()
    db.flush()


def create_order(db, owner, provider, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    existing = db.scalar(
        select(BrokerOrderIntent).where(
            BrokerOrderIntent.owner_id == owner.id,
            BrokerOrderIntent.request_id == str(body.request_id),
        )
    )
    request = body.model_dump(mode="json")
    if existing:
        if existing.provider != provider or existing.payload["request"] != request:
            raise HTTPException(409, "This order request ID was already used for another order.")
        # A repeated request never re-enters the submission path, even after a crash.
        return view(db, existing)
    policy = latest_policy(db, owner, provider)
    config, revision = connection(db, provider)
    if config.environment == "live":
        raise HTTPException(
            409,
            "Live orders require Tauric assessment followed by a bound OpenClaw review. Close existing positions through their attributed journal.",
        )
    if not policy or not policy.payload["enabled"]:
        raise HTTPException(409, "Enable demo broker execution before submitting an order.")
    if body.position_effect == "open":
        from .live_trading import operating_guard

        operating_guard(db, owner, policy)
    if (
        revision != body.expected_connection_revision
        or revision != policy.payload["connection_revision"]
    ):
        raise HTTPException(
            409, "The broker connection changed; verify and re-enable its execution policy."
        )
    try:
        with adapter(provider, config) as broker:
            account = broker.account()
            if (
                not account.trading_enabled
                or account.account_fingerprint != policy.payload["account_fingerprint"]
            ):
                raise BrokerError("The broker account changed or is unavailable for trading.")
            row_id = identifier()
            client_id = "tauric_" + row_id.replace("-", "")
            order = BrokerOrderRequest(
                client_order_id=client_id,
                **body.model_dump(exclude={"request_id", "expected_connection_revision", "reason"}),
            )
            broker.validate(order)
    except BrokerError as error:
        raise HTTPException(409, str(error)) from None
    snapshot = {"request": request, "order": order.model_dump(mode="json"), "source": "owner"}
    row = BrokerOrderIntent(
        id=row_id,
        owner_id=owner.id,
        provider=provider,
        request_id=str(body.request_id),
        client_order_id=client_id,
        policy_version_id=policy.id,
        connection_revision=revision,
        account_fingerprint=account.account_fingerprint,
        payload=snapshot,
        checksum=checksum(snapshot),
        status="prepared",
    )
    db.add(row)
    db.flush()
    receipt(db, row, "prepared", "Owner demo order recorded; no broker request submitted yet.")
    services.audit(
        db,
        owner,
        "broker.order_prepared",
        "broker_order",
        row.id,
        body.reason,
        {"provider": provider, "client_order_id": client_id},
    )
    db.commit()
    return submit(db, owner, row.id)


def matching_broker(db, row, *, for_submission=False):
    config, revision = connection(db, row.provider)
    bound_policy = db.get(BrokerPolicyVersion, row.policy_version_id)
    if (
        not bound_policy
        or checksum(bound_policy.payload) != bound_policy.checksum
        or config.environment != bound_policy.payload.get("environment")
    ):
        raise BrokerError(
            "The broker environment differs from the original intent; restore its original account connection to reconcile."
        )
    if for_submission and revision != row.connection_revision:
        raise BrokerError(
            "The connection changed; verify and re-enable the demo connection before new submission."
        )
    broker = adapter(row.provider, config)
    if row.provider == "oanda" and row.payload["order"]["order_type"] == "market":
        prior = next(
            (
                event.payload.get("order")
                for event in reversed(events(db, row))
                if event.payload.get("order")
            ),
            None,
        )
        if prior:
            broker.known_market_order_ids = {row.client_order_id: prior["broker_order_id"]}
    return broker


def verify_order(row, order):
    planned = row.payload["order"]
    if (
        order.client_order_id != row.client_order_id
        or order.symbol != planned["symbol"]
        or order.side != planned["side"]
        or order.requested_quantity is None
        or Decimal(order.requested_quantity) != Decimal(planned["quantity"])
        or Decimal(order.filled_quantity) > Decimal(planned["quantity"])
    ):
        raise BrokerError("The broker order does not match the recorded intent.")


def apply_observation(db, owner, intent_id, order):
    # Keep owner-before-intent ordering across submission, pause and fill recording.
    # NO KEY UPDATE also permits receipt/position owner foreign-key checks.
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    row = owned(db, owner, intent_id, lock=True)
    verify_order(row, order)
    history = events(db, row)
    prior = next((e.payload["order"] for e in reversed(history) if e.payload.get("order")), None)
    if prior and Decimal(order.filled_quantity) < Decimal(prior["filled_quantity"]):
        raise BrokerError("Broker cumulative fill quantity decreased; reconciliation is required.")
    if row.status in TERMINAL and order.status != row.status:
        raise BrokerError("Broker terminal state changed; reconciliation is required.")
    row.status = order.status
    from . import broker_positions

    broker_positions.register_entry(db, row, order)
    receipt(db, row, "observed", "Actual broker order and cumulative fills recorded.", order)
    db.commit()
    return view(db, row)


def failed_observation(db, owner, intent_id, detail, *, rejected=False):
    db.rollback()
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    row = owned(db, owner, intent_id, lock=True)
    if row.status not in TERMINAL:
        row.status = "rejected" if rejected else "unknown"
    receipt(db, row, "rejected" if rejected else "reconciliation_required", detail)
    db.commit()
    return view(db, row)


def submit(db, owner, intent_id):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    row = owned(db, owner, intent_id, lock=True)
    if row.status != "prepared":
        return view(db, row)
    current = latest_policy(db, owner, row.provider)
    if not current or current.id != row.policy_version_id or not current.payload["enabled"]:
        row.status = "rejected"
        receipt(db, row, "authority_revoked", "Execution authority changed before submission.")
        db.commit()
        return view(db, row)
    try:
        guard = None
        if row.payload["order"]["position_effect"] == "open":
            from .live_trading import operating_guard

            operating_guard(db, owner, current, row.id)
        if row.review_decision_id:
            from . import broker_review

            guard = broker_review.validate_submission(db, row)
        broker = matching_broker(db, row, for_submission=True)
        account = broker.account()
        if account.environment == "live" and (
            not guard or not row.payload["order"].get("stop_loss")
        ):
            raise BrokerError(
                "Live submission requires current Tauric/OpenClaw approval and a protective stop.",
                not_submitted=True,
            )
        if account.account_fingerprint != row.account_fingerprint or not account.trading_enabled:
            raise BrokerError("The demo account identity or trading status changed.")
    except (BrokerError, HTTPException) as error:
        return failed_observation(
            db,
            owner,
            intent_id,
            str(error.detail) if isinstance(error, HTTPException) else str(error),
            rejected=True,
        )
    with broker:
        if guard:
            broker.before_mutation = lambda: broker_review.final_effect_guard(db, owner, *guard)
        # Durable pre-network marker is the at-most-once submission boundary.
        row.status = "submitting"
        receipt(
            db,
            row,
            "submission_started",
            "Broker request may have been submitted; retries only reconcile.",
        )
        order_request = BrokerOrderRequest.model_validate(row.payload["order"])
        db.commit()
        try:
            observed = broker.submit(order_request)
        except BrokerError as error:
            return failed_observation(
                db, owner, intent_id, str(error), rejected=error.not_submitted
            )
        finally:
            if guard:
                broker.before_mutation = None
        try:
            return apply_observation(db, owner, intent_id, observed)
        except BrokerError as error:
            # A mismatch after a broker submission remains uncertain.
            return failed_observation(db, owner, intent_id, str(error))


def reconcile(db, owner, intent_id):
    row = owned(db, owner, intent_id)
    # No network mutation happens here; a not-found after timeout is not a retry permit.
    try:
        with matching_broker(db, row) as broker:
            if broker.account().account_fingerprint != row.account_fingerprint:
                raise BrokerError("The current demo account differs from the recorded intent.")
            observed = broker.order(row.client_order_id)
        return apply_observation(db, owner, intent_id, observed)
    except BrokerError as error:
        return failed_observation(db, owner, intent_id, str(error))


def cancel(db, owner, intent_id):
    row = owned(db, owner, intent_id, lock=True)
    if row.status in TERMINAL:
        return view(db, row)
    if row.status in {"unknown", "submitting", "cancelling"}:
        db.rollback()
        return reconcile(db, owner, intent_id)
    if row.status == "prepared":
        row.status = "cancelled"
        receipt(
            db,
            row,
            "cancelled_before_submission",
            "Prepared order cancelled without contacting the broker.",
        )
        db.commit()
        return view(db, row)
    try:
        broker = matching_broker(db, row)
        if broker.account().account_fingerprint != row.account_fingerprint:
            raise BrokerError("The current demo account differs from the recorded intent.")
    except BrokerError as error:
        return failed_observation(db, owner, intent_id, str(error))
    with broker:
        row.status = "cancelling"
        receipt(
            db,
            row,
            "cancellation_started",
            "Cancellation requested; fills may still occur until the broker confirms.",
        )
        client_id = row.client_order_id
        db.commit()
        try:
            return apply_observation(db, owner, intent_id, broker.cancel(client_id))
        except BrokerError as error:
            return failed_observation(db, owner, intent_id, str(error))


def state(db, owner):
    policies = []
    for provider in ("oanda", "alpaca"):
        policy = latest_policy(db, owner, provider)
        if policy:
            policies.append(policy_view(policy))
    rows = db.scalars(
        select(BrokerOrderIntent)
        .where(BrokerOrderIntent.owner_id == owner.id)
        .order_by(BrokerOrderIntent.created_at.desc())
        .limit(100)
    ).all()
    worker = db.get(WorkerStatus, "broker_execution")
    return BrokerExecutionState(
        worker_ready=bool(worker and (now() - worker.heartbeat_at).total_seconds() < 60),
        policies=policies,
        orders=[view(db, row) for row in rows],
        detail="Broker-connected demo orders. Automatic entry scheduling remains separate from manual order authority.",
    )


def history(db, owner, intent_id):
    return [
        BrokerEventView.model_validate(event) for event in events(db, owned(db, owner, intent_id))
    ]


def heartbeat(db):
    row = db.get(WorkerStatus, "broker_execution")
    if row:
        row.heartbeat_at = now()
        row.version = "0.12.0"
    else:
        db.add(WorkerStatus(id="broker_execution", version="0.12.0", heartbeat_at=now()))
    db.commit()


def process_one(db):
    from . import broker_positions, broker_review

    if broker_review.deliver_one(db):
        return True
    if reconcile_one(db):
        return True
    return broker_positions.process_one(db)


def reconcile_one(db):

    row = db.scalar(
        select(BrokerOrderIntent)
        .where(
            BrokerOrderIntent.status.not_in(TERMINAL),
            BrokerOrderIntent.updated_at < now() - timedelta(seconds=15),
        )
        .order_by(BrokerOrderIntent.updated_at)
        .limit(1)
    )
    if not row:
        return False
    owner = db.get(Owner, row.owner_id)
    if row.status == "prepared":
        submit(db, owner, row.id)
    else:
        reconcile(db, owner, row.id)
    return True
