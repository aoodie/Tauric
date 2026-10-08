"""Capture exact demo entry evidence for native assessment; never submit orders."""

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import select

from . import (
    analysis,
    broker_execution,
    broker_risk,
    market,
    native_broker_setup,
    strategies,
    strategy_rules,
)
from .broker_adapters import BrokerError
from .broker_contracts import (
    BrokerOrderRequest,
    BrokerSetupRequest,
    BrokerSetupView,
    NativeBrokerSetupAssessment,
)
from .market_contracts import AnalysisRequest
from .model_profiles import load_profiles
from .models import AnalysisArtifact, AnalysisJob, Owner, now
from .strategy_contracts import BreakoutRules
from .strategy_execution import snapshot_checked


def capture(db, owner, body, *, route_context=None, risk_percent=None, intent_id=None):
    spec = market.instrument(db, body.instrument_id)
    expected = (
        "oanda"
        if spec.asset_type == "fx"
        else "alpaca"
        if spec.asset_type in {"equity", "etf"}
        else None
    )
    if body.provider != expected:
        raise HTTPException(422, "Choose a matching OANDA Forex or Alpaca stock/ETF instrument.")
    version = strategies.version(db, owner, body.strategy_version_id)
    raw_rules = version.payload["parameters"].get("rules")
    if not raw_rules:
        raise HTTPException(422, "Choose a saved strategy version with executable rules.")
    rules = BreakoutRules.model_validate(raw_rules)
    feed = market.refresh(db, spec.id, rules.interval, commit=False)
    snapshot = snapshot_checked(db, feed.snapshot_id)
    if spec.asset_type in {"equity", "etf"}:
        from .stock_execution import evaluate

        signal = evaluate(feed, rules)
    else:
        signal = strategy_rules.evaluate(feed, rules)
    config, revision = broker_execution.connection(db, body.provider)
    policy = broker_execution.latest_policy(db, owner, body.provider)
    if (
        not policy
        or not policy.payload["enabled"]
        or policy.payload["connection_revision"] != revision
    ):
        raise HTTPException(409, "Verify and enable this demo broker before assessing its entries.")
    from . import live_trading

    live_report = live_trading.require_live_strategy(
        db, owner, policy, version.id, spec.id, risk_percent
    )
    portfolio_observation = live_trading.operating_guard(db, owner, policy, intent_id)
    try:
        with broker_execution.adapter(body.provider, config) as broker:
            account = broker.account()
            if (
                account.account_fingerprint != policy.payload["account_fingerprint"]
                or not account.trading_enabled
            ):
                raise BrokerError("The verified demo account changed or is unavailable.")
            instrument = broker.instrument(spec.provider_symbol)
            positions = broker.positions()
            quote, quote_error, planned, sizing = None, None, None, None
            ready = signal["action"] == "enter"
            try:
                quote = broker.quote(spec.provider_symbol)
                ready = (
                    ready
                    and quote.tradable
                    and 0 <= (now() - quote.observed_at).total_seconds() <= 60
                )
                if ready:
                    with localcontext() as precision:
                        precision.prec = 120
                        step = Decimal(instrument.price_step)
                        stop = (Decimal(signal["stop_price"]) / step).to_integral_value(
                            rounding=ROUND_FLOOR
                        ) * step
                        price = Decimal(quote.ask)
                        if stop <= 0 or stop >= price:
                            raise BrokerError(
                                "The executable ask does not support this long stop reference."
                            )
                        target = (
                            (price + (price - stop) * Decimal(rules.take_profit_r)) / step
                        ).to_integral_value(rounding=ROUND_CEILING) * step
                        if risk_percent is not None:
                            quantity, sizing = broker_risk.calculate(
                                account, instrument, quote, stop, risk_percent
                            )
                            body = body.model_copy(update={"quantity": quantity})
                        elif live_report:
                            maximum, sizing = broker_risk.calculate(
                                account,
                                instrument,
                                quote,
                                stop,
                                live_report.payload["maximum_risk_percent"],
                            )
                            if Decimal(body.quantity) > Decimal(maximum):
                                raise BrokerError(
                                    "Requested live quantity exceeds the forward-validated risk ceiling; obtain new Tauric evidence before increasing risk."
                                )
                            sizing["quantity"] = body.quantity
                        planned = BrokerOrderRequest(
                            client_order_id="tauric_setup_" + str(body.request_id).replace("-", ""),
                            symbol=spec.provider_symbol,
                            side="buy",
                            quantity=body.quantity,
                            order_type="market",
                            time_in_force="fok" if body.provider == "oanda" else "day",
                            position_effect="open",
                            stop_loss=format(stop, "f"),
                            take_profit=format(target, "f"),
                        )
                        broker.validate(planned)
            except BrokerError as failure:
                quote_error, ready, planned = str(failure), False, None
    except BrokerError as failure:
        raise HTTPException(409, str(failure)) from None
    stamp = now()
    expiry = stamp + timedelta(seconds=rules.signal_ttl_seconds)
    if signal.get("bar_close_time"):
        expiry = min(
            expiry,
            datetime.fromisoformat(signal["bar_close_time"])
            + timedelta(seconds=market.INTERVAL_SECONDS[rules.interval], milliseconds=1),
        )
    ready = ready and expiry > stamp
    evidence = {
        "request": body.model_dump(mode="json"),
        "instrument": spec.model_dump(mode="json"),
        "strategy": strategies.view(version).model_dump(mode="json"),
        "rules": rules.model_dump(mode="json"),
        "snapshot_id": snapshot.id,
        "snapshot_checksum": snapshot.checksum,
        "signal": signal,
        "data_context": feed.data_context.model_dump(mode="json") if feed.data_context else None,
        "broker_instrument": instrument.model_dump(mode="json"),
        "account": account.model_dump(mode="json"),
        "existing_positions": [position.model_dump(mode="json") for position in positions],
        "risk_authority": "Native Tauric approves the maximum quantity. OpenClaw may reduce it; increases require a new native assessment.",
        "portfolio_valuation": portfolio_observation,
        "policy_id": policy.id,
        "policy_checksum": policy.checksum,
        "policy": policy.payload,
        "connection_revision": revision,
        "quote": quote.model_dump(mode="json") if quote else None,
        "quote_error": quote_error,
        "planned_order": planned.model_dump(mode="json") if planned else None,
        "entry_evidence_ready": ready,
        "expires_at": expiry.isoformat(),
        "captured_at": stamp.isoformat(),
        "sizing": "Explicit configured currency units or whole shares. Paper strategy capital/risk parameters are not broker caps. Broker restrictions remain authoritative.",
        "execution_authority": False,
    }
    if route_context is not None:
        evidence["route_context"] = route_context
    if sizing is not None:
        evidence["automatic_sizing"] = sizing
    return {
        "adapter_version": native_broker_setup.ADAPTER_VERSION,
        "evidence_checksum": native_broker_setup.checksum(evidence),
        "assessment_input": evidence,
    }


def enqueue(db, owner, body, *, commit=True, captured_context=None):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update(key_share=True))
    existing = db.scalar(
        select(AnalysisJob).where(
            AnalysisJob.owner_id == owner.id, AnalysisJob.request_id == str(body.request_id)
        )
    )
    if existing:
        context = existing.request.get("broker_context")
        if not context or context["assessment_input"]["request"] != body.model_dump(mode="json"):
            raise HTTPException(409, "This request ID already belongs to another research action.")
        native_broker_setup.verify(context)
        return analysis.view(db, existing)
    context = captured_context if captured_context is not None else capture(db, owner, body)
    evidence, _ = native_broker_setup.verify(context)
    if evidence["request"] != body.model_dump(mode="json"):
        raise HTTPException(409, "Captured setup inputs differ from the native request.")
    if evidence.get("route_context"):
        from .broker_routes import guard

        guard(db, owner, evidence)
    if not context["assessment_input"]["entry_evidence_ready"]:
        raise HTTPException(
            422,
            "Fresh executable rule, quote and instrument evidence did not pass; no native model job was queued.",
        )
    result = analysis.enqueue(
        db,
        owner,
        AnalysisRequest(
            request_id=body.request_id,
            instrument_id=body.instrument_id,
            profile=body.profile,
            snapshot_id=context["assessment_input"]["snapshot_id"],
        ),
        commit=False,
        broker_context=context,
    )
    if commit:
        db.commit()
    return result


def validate_admission(db, job, *, check_broker=True):
    context = job.request.get("broker_context")
    if not context:
        return
    evidence, _ = native_broker_setup.verify(context)
    body = BrokerSetupRequest.model_validate(evidence["request"])
    owner = db.get(Owner, job.owner_id)
    if evidence.get("route_context"):
        from .broker_routes import guard

        guard(db, owner, evidence)
    version = strategies.version(db, owner, body.strategy_version_id)
    policy = broker_execution.latest_policy(db, owner, body.provider)
    config, revision = broker_execution.connection(db, body.provider)
    snapshot = snapshot_checked(db, evidence["snapshot_id"])
    profiles, _ = load_profiles(db)
    profile = profiles.get(body.profile)
    if (
        job.request.get("mode") != "broker_entry_assessment"
        or job.request.get("upstream_revision") != analysis.UPSTREAM_REVISION
        or job.request.get("trade_date") != now().date().isoformat()
        or job.instrument_id != body.instrument_id
        or job.profile != body.profile
        or job.snapshot_id != snapshot.id
        or snapshot.checksum != evidence["snapshot_checksum"]
        or snapshot.instrument_id != body.instrument_id
        or version.checksum != evidence["strategy"]["checksum"]
        or not evidence["entry_evidence_ready"]
        or datetime.fromisoformat(evidence["expires_at"]) <= now()
        or revision != evidence["connection_revision"]
        or not policy
        or policy.id != evidence["policy_id"]
        or policy.checksum != evidence["policy_checksum"]
        or not policy.payload["enabled"]
        or not profile
        or not profile.configured()
        or profile.fingerprint() != job.request.get("profile_fingerprint")
    ):
        raise HTTPException(
            409, "The exact broker setup changed, expired or was disabled before native assessment."
        )
    if not check_broker:
        return
    try:
        with broker_execution.adapter(body.provider, config) as broker:
            account = broker.account()
            if (
                not account.trading_enabled
                or account.account_fingerprint != evidence["account"]["account_fingerprint"]
            ):
                raise BrokerError("The assessed broker account changed or is unavailable.")
    except BrokerError as failure:
        raise HTTPException(409, str(failure)) from None


def report(db, owner, job_id):
    job = analysis.get_job(db, owner, job_id)
    context = job.request.get("broker_context")
    if not context:
        raise HTTPException(404, "This is not a broker setup assessment.")
    evidence, expected = native_broker_setup.verify(context)
    artifact = db.scalar(select(AnalysisArtifact).where(AnalysisArtifact.job_id == job.id))
    assessment = None
    if artifact:
        payload = artifact.payload
        is_fx = evidence["instrument"]["asset_type"] == "fx"
        expected_runtime = (
            "Tauric Forex daily extension / TradingAgents LLM clients"
            if is_fx
            else "TauricResearch/TradingAgents"
        )
        if (
            job.status != "succeeded"
            or artifact.owner_id != owner.id
            or native_broker_setup.checksum(payload) != artifact.checksum
            or payload.get("request") != job.request
            or payload.get("execution_authority") is not False
            or payload.get("governance") is not None
            or payload.get("upstream_revision") != analysis.UPSTREAM_REVISION
            or payload.get("runtime") != expected_runtime
            or is_fx
            and (
                payload.get("adapter_version")
                != job.request.get("forex_context", {}).get("adapter_version")
                or not isinstance(payload.get("forex_evidence"), dict)
                or payload["forex_evidence"].get("binding") != job.request.get("forex_context")
                or payload["forex_evidence"].get("pair") != evidence["instrument"]["symbol"]
            )
            or payload.get("version") != "0.5.2"
            or not isinstance(payload.get("native_rating"), str)
            or not isinstance(payload.get("reports"), dict)
            or not isinstance(payload["reports"].get("final_trade_decision"), str)
            or not payload["reports"]["final_trade_decision"].strip()
        ):
            raise HTTPException(409, "The original broker setup report cannot be verified.")
        try:
            assessment = NativeBrokerSetupAssessment.model_validate(
                payload.get("broker_setup_assessment")
            )
            actual = assessment.model_dump(mode="json")
            if any(actual[key] != value for key, value in expected.items()) or (
                assessment.decision == "approve"
                and (
                    not evidence["entry_evidence_ready"]
                    or assessment.model_invocations != 1
                    or evidence["instrument"]["asset_type"] != "fx"
                    and str(payload.get("native_rating")).strip().upper() != "BUY"
                )
            ):
                raise ValueError("Native broker setup binding mismatch")
        except (ValueError, TypeError):
            raise HTTPException(
                409, "The native assessment does not match the exact broker entry evidence."
            ) from None
    current, detail = False, "Native assessment is pending. It grants no trading authority."
    if artifact:
        try:
            validate_admission(db, job, check_broker=False)
            current = True
            detail = "Exact native setup assessment preserved. A separate bound OpenClaw review and fresh execution checks are still required."
        except HTTPException as failure:
            detail = str(failure.detail)
    return BrokerSetupView(
        job=analysis.view(db, job),
        native_artifact_id=artifact.id if artifact else None,
        assessment=assessment,
        evidence_current=current,
        detail=detail,
    )


def reports(db, owner):
    jobs = db.scalars(
        select(AnalysisJob)
        .where(
            AnalysisJob.owner_id == owner.id,
            AnalysisJob.request["mode"].as_string() == "broker_entry_assessment",
        )
        .order_by(AnalysisJob.created_at.desc(), AnalysisJob.id.desc())
        .limit(20)
    ).all()
    return [report(db, owner, job.id) for job in jobs]
