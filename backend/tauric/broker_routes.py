"""Opt-in demo signal discovery and restartable native → OpenClaw → broker lineage.

No model or order retry is inferred from a failed/unknown result. Each owned
strategy/instrument/closed-bar signal has one permanent workflow across routes
and revisions. Current checks coalesce missed intervals instead of replaying.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from . import (
    analysis,
    broker_execution,
    broker_positions,
    broker_review,
    broker_setup,
    governance,
    market,
    native_broker_setup,
    services,
    strategies,
    strategy_rules,
)
from .broker_adapters import BrokerError
from .broker_contracts import (
    BrokerCloseAction,
    BrokerReviewRequest,
    BrokerRouteChange,
    BrokerRouteEventView,
    BrokerRoutesState,
    BrokerRouteView,
    BrokerRouteWorkflowView,
    BrokerSetupRequest,
)
from .broker_models import (
    BrokerOrderIntent,
    BrokerPositionCursor,
    BrokerReviewJob,
    BrokerRoute,
    BrokerRouteEvent,
    BrokerRouteVersion,
    BrokerRouteWorkflow,
)
from .model_profiles import load_profiles
from .models import AnalysisJob, Owner, identifier, now
from .strategy_contracts import BreakoutRules

TERMINAL = {"closed", "blocked", "cancelled"}


def fail(detail, status=409):
    raise HTTPException(status, detail)


def checked(row):
    if not row or native_broker_setup.checksum(row.payload) != row.checksum:
        fail("Saved broker route evidence failed its integrity check.")
    return row


def owned(db, owner, route_id):
    row = db.get(BrokerRoute, str(route_id))
    if not row or row.owner_id != owner.id:
        fail("This demo strategy route does not exist.", 404)
    return row


def view(db, row):
    version = checked(db.get(BrokerRouteVersion, row.version_id))
    event = checked(db.get(BrokerRouteEvent, row.last_event_id)) if row.last_event_id else None
    return BrokerRouteView(
        id=row.id,
        version_id=version.id,
        revision=version.revision,
        settings=version.payload["request"],
        next_due_at=row.next_due_at,
        last_event=event.payload if event else None,
        last_checked_at=event.created_at if event else None,
    )


def state(db, owner):
    routes = db.scalars(
        select(BrokerRoute).where(BrokerRoute.owner_id == owner.id).order_by(BrokerRoute.id)
    ).all()
    flows = db.scalars(
        select(BrokerRouteWorkflow)
        .where(BrokerRouteWorkflow.owner_id == owner.id)
        .order_by(BrokerRouteWorkflow.created_at.desc())
        .limit(50)
    ).all()
    return BrokerRoutesState(
        worker_ready=broker_execution.state(db, owner).worker_ready,
        routes=[view(db, row) for row in routes],
        workflows=[
            BrokerRouteWorkflowView(
                id=w.id,
                route_id=w.route_id,
                version_id=w.version_id,
                status=w.status,
                assessment_job_id=w.assessment_job_id,
                review_job_id=w.review_job_id,
                intent_id=w.intent_id,
                sizing=checked(w).payload["sizing"],
                signal_bar_close=w.payload["signal_bar_close"],
                created_at=w.created_at,
            )
            for w in flows
        ],
        detail="Opt-in OANDA practice / Alpaca paper long routes. Risk sizing uses account equity and observed stop distance; losses can exceed the estimate. Pending entries stop on pause/change; existing positions retain broker protection and the original channel-exit monitoring. No extra owner capital, exposure, position or daily-loss caps.",
    )


def history(db, owner, route_id):
    owned(db, owner, route_id)
    return [
        BrokerRouteEventView(
            id=e.id,
            version_id=e.version_id,
            workflow_id=e.workflow_id,
            payload=checked(e).payload,
            created_at=e.created_at,
        )
        for e in db.scalars(
            select(BrokerRouteEvent)
            .where(BrokerRouteEvent.route_id == str(route_id))
            .order_by(BrokerRouteEvent.created_at.desc())
            .limit(100)
        )
    ]


def save(db, owner, route_id, body):
    route_id = str(UUID(str(route_id)))
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    request = body.model_dump(mode="json")
    previous = db.scalar(
        select(BrokerRouteVersion).where(
            BrokerRouteVersion.owner_id == owner.id,
            BrokerRouteVersion.request_id == str(body.request_id),
        )
    )
    if previous:
        if previous.route_id != route_id or checked(previous).payload["request"] != request:
            fail("This request ID already belongs to another route change.")
        return view(db, owned(db, owner, route_id))
    row = db.get(BrokerRoute, route_id)
    if row and row.owner_id != owner.id:
        fail("This demo strategy route does not exist.", 404)
    old = checked(db.get(BrokerRouteVersion, row.version_id)) if row else None
    if body.expected_revision != (old.revision if old else 0):
        fail("Reload the route before changing it.")
    spec = market.instrument(db, body.instrument_id)
    from . import execution_scope

    if body.provider != execution_scope.provider(spec):
        raise HTTPException(422, "Select an account-verified product for the matching broker.")
    version = strategies.version(db, owner, body.strategy_version_id)
    raw_rules = version.payload["parameters"].get("rules")
    if not raw_rules:
        fail("Save executable strategy rules first.", 422)
    rules = BreakoutRules.model_validate(raw_rules)
    if body.cadence_seconds > min(
        market.INTERVAL_SECONDS[rules.interval], rules.signal_ttl_seconds
    ):
        fail("The check interval must fit within the strategy candle and signal lifetime.", 422)
    binding = {
        "policy_id": None,
        "policy_checksum": None,
        "connection_revision": None,
        "account_fingerprint": None,
        "native_fingerprint": None,
        "routing_fingerprint": None,
    }
    if body.enabled:
        policy = broker_execution.latest_policy(db, owner, body.provider)
        config, revision = broker_execution.connection(db, body.provider)
        profiles, _ = load_profiles(db)
        profile = profiles.get(body.native_profile)
        review = governance.status(db, owner)
        if (
            not policy
            or not policy.payload["enabled"]
            or policy.payload["connection_revision"] != revision
        ):
            fail("Verify and enable the selected demo broker first.")
        if not analysis.status(db).worker_ready or not profile or not profile.configured():
            fail("The selected native AI connection and research worker must be ready.", 503)
        from . import live_trading

        live_trading.require_live_strategy(
            db, owner, policy, version.id, spec.id, body.risk_percent
        )
        if (
            not next(r.ready for r in review.routes if r.id == body.review_profile)
            or not broker_execution.state(db, owner).worker_ready
        ):
            fail("The selected OpenClaw route and demo execution worker must be ready.", 503)
        try:
            with broker_execution.adapter(body.provider, config) as broker:
                account, instrument = broker.account(), broker.instrument(spec.provider_symbol)
                if (
                    not account.trading_enabled
                    or not instrument.tradable
                    or account.account_fingerprint != policy.payload["account_fingerprint"]
                ):
                    raise BrokerError("The saved demo account or instrument is unavailable.")
        except BrokerError as error:
            fail(str(error))
        binding.update(
            policy_id=policy.id,
            policy_checksum=policy.checksum,
            connection_revision=revision,
            account_fingerprint=account.account_fingerprint,
            native_fingerprint=profile.fingerprint(),
            routing_fingerprint=governance.routing(db, owner)["fingerprint"],
        )
    payload = {"request": request, "strategy_checksum": version.checksum, **binding}
    saved = BrokerRouteVersion(
        id=identifier(),
        route_id=route_id,
        owner_id=owner.id,
        revision=body.expected_revision + 1,
        request_id=str(body.request_id),
        payload=payload,
        checksum=native_broker_setup.checksum(payload),
    )
    db.add(saved)
    db.flush()
    if row is None:
        row = BrokerRoute(id=route_id, owner_id=owner.id, version_id=saved.id)
        db.add(row)
    row.version_id, row.next_due_at, row.last_event_id = (
        saved.id,
        now() if body.enabled else None,
        None,
    )
    services.audit(
        db,
        owner,
        "broker.route_changed",
        "broker_route",
        route_id,
        body.reason,
        {
            "revision": saved.revision,
            "enabled": body.enabled,
            "risk_percent": body.risk_percent,
            "provider": body.provider,
        },
    )
    db.commit()
    return view(db, row)


def current(db, owner, version):
    checked(version)
    row = owned(db, owner, version.route_id)
    body = BrokerRouteChange.model_validate(version.payload["request"])
    policy = broker_execution.latest_policy(db, owner, body.provider)
    _, revision = broker_execution.connection(db, body.provider)
    profiles, _ = load_profiles(db)
    profile = profiles.get(body.native_profile)
    if (
        row.version_id != version.id
        or not body.enabled
        or not policy
        or not policy.payload["enabled"]
        or policy.id != version.payload["policy_id"]
        or policy.checksum != version.payload["policy_checksum"]
        or revision != version.payload["connection_revision"]
        or not profile
        or not profile.configured()
        or profile.fingerprint() != version.payload["native_fingerprint"]
        or governance.routing(db, owner)["fingerprint"] != version.payload["routing_fingerprint"]
    ):
        fail(
            "The automatic route was paused or its strategy, broker policy, account connection or AI settings changed. Save a new route version to resume."
        )
    return body


def guard(db, owner, evidence):
    binding = evidence["route_context"]
    version = db.get(BrokerRouteVersion, binding["version_id"])
    if (
        not version
        or version.owner_id != owner.id
        or version.route_id != binding["route_id"]
        or checked(version).checksum != binding["version_checksum"]
    ):
        fail("Automatic route authority cannot be verified.")
    body = current(db, owner, version)
    request = evidence["request"]
    if (
        any(
            request[key] != value
            for key, value in {
                "provider": body.provider,
                "instrument_id": body.instrument_id,
                "strategy_version_id": str(body.strategy_version_id),
                "profile": body.native_profile,
            }.items()
        )
        or evidence["policy_id"] != version.payload["policy_id"]
        or evidence["account"]["account_fingerprint"] != version.payload["account_fingerprint"]
        or evidence.get("automatic_sizing", {}).get("risk_percent") != body.risk_percent
    ):
        fail("Automatic route and exact assessed entry disagree.")


def receipt(db, route, version, action, detail, workflow=None, **facts):
    payload = {"action": action, "detail": detail, **facts}
    event = BrokerRouteEvent(
        owner_id=route.owner_id,
        route_id=route.id,
        version_id=version.id,
        workflow_id=workflow.id if workflow else None,
        payload=payload,
        checksum=native_broker_setup.checksum(payload),
    )
    db.add(event)
    db.flush()
    # Old workflows keep their own receipts without overwriting a newer route's status.
    if route.version_id == version.id:
        route.last_event_id = event.id
    return event


def owner_lock(db, owner_id):
    return db.scalar(
        select(Owner).where(Owner.id == owner_id).with_for_update(key_share=True, skip_locked=True)
    )


def discover_one(db):
    candidate = db.scalar(
        select(BrokerRoute)
        .where(BrokerRoute.next_due_at <= now())
        .order_by(BrokerRoute.next_due_at, BrokerRoute.id)
        .limit(1)
    )
    if not candidate or not (owner := owner_lock(db, candidate.owner_id)):
        db.rollback()
        return False
    route = db.scalar(
        select(BrokerRoute)
        .where(BrokerRoute.id == candidate.id, BrokerRoute.next_due_at <= now())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not route:
        db.rollback()
        return False
    version = checked(db.get(BrokerRouteVersion, route.version_id))
    body = BrokerRouteChange.model_validate(version.payload["request"])
    try:
        with db.begin_nested():
            current(db, owner, version)
            workflow_id = identifier()
            request = BrokerSetupRequest(
                request_id=uuid5(NAMESPACE_URL, "tauric-route-native:" + workflow_id),
                provider=body.provider,
                instrument_id=body.instrument_id,
                strategy_version_id=body.strategy_version_id,
                quantity="1",
                profile=body.native_profile,
                reason="Automatically assess one current closed-bar demo signal with calculated stop risk",
            )
            context = broker_setup.capture(db, owner, request, risk_percent=body.risk_percent)
            evidence = context["assessment_input"]
            signal = evidence["signal"]
            if not evidence["entry_evidence_ready"]:
                receipt(
                    db,
                    route,
                    version,
                    signal["action"],
                    evidence.get("quote_error") or " ".join(signal.get("reasons", [])),
                    sizing=evidence.get("automatic_sizing"),
                    signal_bar_close=signal.get("bar_close_time"),
                )
            else:
                key = native_broker_setup.checksum(
                    {
                        "owner": owner.id,
                        "strategy": str(body.strategy_version_id),
                        "instrument": body.instrument_id,
                        "bar_close": signal["bar_close_time"],
                    }
                )
                existing = db.scalar(
                    select(BrokerRouteWorkflow).where(
                        BrokerRouteWorkflow.owner_id == owner.id,
                        BrokerRouteWorkflow.signal_key == key,
                    )
                )
                if existing:
                    receipt(
                        db,
                        route,
                        version,
                        "already_seen",
                        "This closed-bar signal already has a permanent workflow; it is not repeated.",
                        existing
                        if existing.route_id == route.id and existing.version_id == version.id
                        else None,
                        signal_bar_close=signal["bar_close_time"],
                    )
                else:
                    evidence["route_context"] = {
                        "authority": "owner_enabled_demo_strategy_route",
                        "route_settings": body.model_dump(mode="json"),
                        "permission_scope": "This enabled owned route version authorizes scheduled demo entries and attributed channel exits. The historical manual broker policy's automatic_entries_enabled field predates route scheduling; the bound route version is the separate opt-in scheduling authority.",
                        "route_id": route.id,
                        "version_id": version.id,
                        "version_checksum": version.checksum,
                        "workflow_id": workflow_id,
                        "signal_key": key,
                    }
                    context["evidence_checksum"] = native_broker_setup.checksum(evidence)
                    job = broker_setup.enqueue(
                        db,
                        owner,
                        BrokerSetupRequest.model_validate(evidence["request"]),
                        commit=False,
                        captured_context=context,
                    )
                    payload = {
                        "signal_bar_close": signal["bar_close_time"],
                        "sizing": evidence["automatic_sizing"],
                        "expires_at": evidence["expires_at"],
                    }
                    flow = BrokerRouteWorkflow(
                        id=workflow_id,
                        owner_id=owner.id,
                        route_id=route.id,
                        version_id=version.id,
                        signal_key=key,
                        assessment_job_id=job.id,
                        payload=payload,
                        checksum=native_broker_setup.checksum(payload),
                        status="native_pending",
                        next_due_at=now(),
                    )
                    db.add(flow)
                    db.flush()
                    receipt(
                        db,
                        route,
                        version,
                        "native_pending",
                        "One exact native setup assessment queued; no broker order yet.",
                        flow,
                        quantity=evidence["request"]["quantity"],
                        sizing=evidence["automatic_sizing"],
                    )
    except HTTPException as error:
        receipt(db, route, version, "blocked", str(error.detail))
    route.next_due_at = now() + timedelta(seconds=body.cadence_seconds)
    db.commit()
    return True


def transition(db, route, version, flow, status, detail):
    changed = flow.status != status
    flow.status = status
    flow.next_due_at = (
        None if status in TERMINAL else now() + timedelta(seconds=5 if status != "tracking" else 15)
    )
    if changed:
        receipt(db, route, version, status, detail, flow)


def advance_one(db):
    candidate = db.scalar(
        select(BrokerRouteWorkflow)
        .where(BrokerRouteWorkflow.next_due_at <= now())
        .order_by(BrokerRouteWorkflow.next_due_at, BrokerRouteWorkflow.id)
        .limit(1)
    )
    if not candidate or not (owner := owner_lock(db, candidate.owner_id)):
        db.rollback()
        return False
    flow = db.scalar(
        select(BrokerRouteWorkflow)
        .where(BrokerRouteWorkflow.id == candidate.id, BrokerRouteWorkflow.next_due_at <= now())
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not flow:
        db.rollback()
        return False
    checked(flow)
    route, version = (
        owned(db, owner, flow.route_id),
        checked(db.get(BrokerRouteVersion, flow.version_id)),
    )
    body = BrokerRouteChange.model_validate(version.payload["request"])
    try:
        with db.begin_nested():
            # Recover delivery committed by the independent order worker before route pause.
            review = db.get(BrokerReviewJob, flow.review_job_id) if flow.review_job_id else None
            if review and review.decision_id and not flow.intent_id:
                flow.intent_id = db.scalar(
                    select(BrokerOrderIntent.id).where(
                        BrokerOrderIntent.review_decision_id == review.decision_id
                    )
                )
            if flow.intent_id:
                entry = broker_execution.owned(db, owner, flow.intent_id)
                position = broker_positions.latest(db, entry)
                if position and position.status == "closed":
                    transition(
                        db,
                        route,
                        version,
                        flow,
                        "closed",
                        "Actual broker executions confirm this attributed position is closed.",
                    )
                elif entry.status in {"rejected", "cancelled", "expired"} and (
                    not position or Decimal(position.remaining_quantity) == 0
                ):
                    transition(
                        db,
                        route,
                        version,
                        flow,
                        "blocked",
                        "The broker entry ended without an open attributed position.",
                    )
                else:
                    transition(
                        db,
                        route,
                        version,
                        flow,
                        "tracking"
                        if position and Decimal(position.remaining_quantity) > 0
                        else "entry_pending",
                        "Track actual fills and broker protection; unknown outcomes are reconciled without another entry submission.",
                    )
            else:
                current(db, owner, version)
                if datetime.fromisoformat(flow.payload["expires_at"]) <= now():
                    fail(
                        "This closed-bar signal expired; no automatic research or entry retry is made."
                    )
                native = db.get(AnalysisJob, flow.assessment_job_id)
                if native.status in {"failed", "interrupted"}:
                    fail(
                        "Native research failed or was interrupted; no automatic model retry is made."
                    )
                if native.status != "succeeded":
                    flow.next_due_at = now() + timedelta(seconds=5)
                elif not flow.review_job_id:
                    report = broker_setup.report(db, owner, native.id)
                    if (
                        not report.evidence_current
                        or not report.assessment
                        or report.assessment.decision != "approve"
                    ):
                        fail("Native research did not approve the current exact setup.")
                    review = broker_review.enqueue(
                        db,
                        owner,
                        BrokerReviewRequest(
                            request_id=uuid5(NAMESPACE_URL, "tauric-route-review:" + flow.id),
                            assessment_job_id=native.id,
                            profile=body.review_profile,
                            submit_on_allow=True,
                            reason="Automatically review the exact natively approved demo signal and calculated quantity",
                        ),
                        commit=False,
                    )
                    flow.review_job_id = review.id
                    transition(
                        db,
                        route,
                        version,
                        flow,
                        "review_pending",
                        "Separate OpenClaw broker review queued. An allow may submit this exact entry once.",
                    )
                elif review.status in {"failed", "interrupted"}:
                    fail(
                        "OpenClaw review failed or was interrupted; no automatic model retry is made."
                    )
                elif review.status == "succeeded":
                    decision, _ = broker_review.decision(db, owner, review.decision_id)
                    if decision.payload["outcome"] != "allow":
                        fail(
                            "OpenClaw "
                            + decision.payload["outcome"]
                            + ": "
                            + decision.payload["reason"]
                        )
                    flow.next_due_at = now() + timedelta(seconds=5)
                else:
                    flow.next_due_at = now() + timedelta(seconds=5)
    except HTTPException as error:
        transition(db, route, version, flow, "blocked", str(error.detail))
    db.commit()
    if flow.status == "tracking":
        channel_exit(db, owner, flow)
    return True


def channel_exit(db, owner, flow):
    """Close only attributed filled quantity on a complete post-fill channel signal.

    Candle stop/target touches never claim a broker fill or cancel its protective
    orders. The broker lifecycle service owns actual executions and close guards.
    """
    route = owned(db, owner, flow.route_id)
    version = checked(db.get(BrokerRouteVersion, flow.version_id))
    body = BrokerRouteChange.model_validate(version.payload["request"])
    entry = broker_execution.owned(db, owner, flow.intent_id)
    position = broker_positions.latest(db, entry)
    cursor = db.get(BrokerPositionCursor, entry.id)
    if (
        entry.status != "filled"
        or not position
        or position.status not in {"open", "partially_closed"}
        or position.holding_check != "covered"
        or position.side != "long"
        or not position.entry_fills
        or not cursor
        or cursor.status not in {"open", "partially_closed"}
        or not 0 <= (now() - cursor.updated_at).total_seconds() <= 60
    ):
        return
    try:
        strategy = strategies.version(db, owner, body.strategy_version_id)
        rules = BreakoutRules.model_validate(strategy.payload["parameters"]["rules"])
        feed = market.refresh(db, body.instrument_id, rules.interval, commit=False)
        if body.provider == "alpaca":
            from .stock_execution import evaluate

            signal = evaluate(feed, rules)
        else:
            signal = strategy_rules.evaluate(feed, rules)
        bars = signal.get("bars", [])
        if (
            signal["action"] == "blocked"
            or len(bars) < rules.exit_lookback + 1
            or datetime.fromisoformat(bars[-1]["open_time"])
            < max(fill.occurred_at for fill in position.entry_fills)
        ):
            return

        def price(bar, field):
            return Decimal(bar["bid"][field] if body.provider == "oanda" else bar[field])

        channel = min(price(bar, "low") for bar in bars[-rules.exit_lookback - 1 : -1])
        if price(bars[-1], "close") >= channel:
            return
        with broker_execution.matching_broker(db, entry) as broker:
            quote = broker.quote(position.symbol)
            if not quote.tradable or not 0 <= (now() - quote.observed_at).total_seconds() <= 60:
                return
        request = BrokerCloseAction(
            request_id=uuid5(
                NAMESPACE_URL, "tauric-route-channel:" + entry.id + ":" + signal["bar_close_time"]
            ),
            quantity=position.remaining_quantity,
            reason="Automatic attributed demo close: complete post-fill candle below the saved strategy exit channel",
        )
        result = broker_positions.request_close(db, owner, entry.id, request)
        receipt(
            db,
            route,
            version,
            "channel_close",
            "An attributed close was requested; actual broker executions determine completion.",
            flow,
            close_request_id=result.id,
            signal_bar_close=signal["bar_close_time"],
            exit_channel=format(channel, "f"),
        )
        db.commit()
    except (HTTPException, BrokerError) as error:
        db.rollback()
        route = owned(db, owner, flow.route_id)
        receipt(
            db,
            route,
            version,
            "channel_close_unavailable",
            str(error.detail) if isinstance(error, HTTPException) else str(error),
            flow,
        )
        db.commit()
