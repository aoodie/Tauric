"""Deterministic unlevered spot planner and paper adapter; no broker order endpoints.

Account locks cover plan validation and ledger writes. Projections are rebuilt from
immutable fills, never from a mutable position counter. All arithmetic is Decimal.
"""

import hashlib
import json
from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import select

from . import capital, market, services, strategies
from .models import (
    AnalysisArtifact,
    AnalysisJob,
    Owner,
    PaperAccount,
    PaperDeployment,
    TradingRecord,
    identifier,
    now,
)
from .trading_contracts import (
    DeploymentView,
    PaperAccountView,
    PaperPolicy,
    PaperPositionView,
    PaperState,
    TradingRecordView,
)

D = Decimal
ZERO = D("0")


def decimal_text(value):
    return format(value, "f")


def checksum(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def fail(message, status=409):
    raise HTTPException(status, message)


def account(db, owner, account_id):
    row = db.scalar(
        select(PaperAccount)
        .where(PaperAccount.id == str(account_id), PaperAccount.owner_id == owner.id)
        .with_for_update()
    )
    if not row:
        fail("This paper account does not exist.", 404)
    return row


def deployment(db, owner, deployment_id):
    row = db.get(PaperDeployment, str(deployment_id))
    if not row or row.owner_id != owner.id:
        fail("This strategy deployment does not exist.", 404)
    return row


def record(db, owner, record_id, kind):
    row = db.get(TradingRecord, str(record_id))
    if not row or row.owner_id != owner.id or row.kind != kind:
        fail("This trading record does not exist.", 404)
    if checksum(row.payload) != row.checksum:
        fail("Trading record integrity failed. Execution is blocked.")
    return row


def replay(db, owner, kind, request_id, request):
    row = db.scalar(
        select(TradingRecord).where(
            TradingRecord.owner_id == owner.id,
            TradingRecord.kind == kind,
            TradingRecord.request_id == str(request_id),
        )
    )
    if row:
        if row.payload.get("request") != request:
            fail("This request identifier was already used for different inputs.")
        return row
    return None


def append(db, owner, acct, kind, request_id, payload, deployment_id=None, consumes=None):
    if consumes and db.scalar(
        select(TradingRecord.id).where(
            TradingRecord.kind == kind, TradingRecord.consumes_id == consumes
        )
    ):
        fail("This record has already been used. Start a new proposal for a new decision.")
    row = TradingRecord(
        id=identifier(),
        owner_id=owner.id,
        account_id=acct.id,
        deployment_id=deployment_id,
        kind=kind,
        request_id=str(request_id),
        consumes_id=consumes,
        payload=payload,
        checksum=checksum(payload),
    )
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        f"paper.{kind}",
        "trading_record",
        row.id,
        payload.get("reason", "Paper record created"),
        {"account_id": acct.id, "deployment_id": deployment_id, "mode": "paper"},
    )
    return row


def policy_record(db, acct):
    return db.scalar(
        select(TradingRecord)
        .where(TradingRecord.account_id == acct.id, TradingRecord.kind == "policy")
        .order_by(TradingRecord.created_at.desc(), TradingRecord.id.desc())
        .limit(1)
    )


def policy(db, acct):
    row = policy_record(db, acct)
    if not row or checksum(row.payload) != row.checksum:
        fail("Paper policy integrity failed. Execution is blocked.")
    return PaperPolicy.model_validate(row.payload["policy"]), row


def ledger(db, acct):
    fills = list(
        db.scalars(
            select(TradingRecord)
            .where(TradingRecord.account_id == acct.id, TradingRecord.kind == "fill")
            .order_by(TradingRecord.created_at, TradingRecord.id)
        )
    )
    cash, realized, positions = D(acct.starting_balance), ZERO, {}
    with localcontext() as context:
        context.prec = 120
        for row in fills:
            p = row.payload
            if checksum(p) != row.checksum:
                fail("Paper ledger integrity failed. Execution is blocked.")
            quantity, price, fee = (D(p[key]) for key in ("quantity", "price", "fee"))
            if (
                not all(x.is_finite() for x in (quantity, price, fee))
                or min(quantity, price) <= 0
                or fee < 0
            ):
                fail("Paper ledger contains invalid financial values.")
            if p["side"] == "buy":
                cash -= quantity * price + fee
                positions[row.id] = row
            elif p["side"] == "sell":
                entry = positions.pop(p["position_id"], None)
                if (
                    not entry
                    or entry.deployment_id != row.deployment_id
                    or D(entry.payload["quantity"]) != quantity
                ):
                    fail("Position attribution does not reconcile. Execution is blocked.")
                cash += quantity * price - fee
                financing = D(p.get("financing", "0"))
                if not financing.is_finite():
                    fail("Invalid paper financing.")
                cash -= financing
                realized += (
                    quantity * (price - D(entry.payload["price"]))
                    - fee
                    - D(entry.payload["fee"])
                    - financing
                )
            else:
                fail("Unknown paper fill direction.")
            if cash < 0:
                fail("Paper cash does not reconcile. Execution is blocked.")
        cost = sum(
            (
                D(p.payload["quantity"]) * D(p.payload["price"]) + D(p.payload["fee"])
                for p in positions.values()
            ),
            ZERO,
        )
        if cash + cost != D(acct.starting_balance) + realized:
            fail("Paper balances do not reconcile. Execution is blocked.")
    digest = checksum(
        {"starting_balance": acct.starting_balance, "fills": [(r.id, r.checksum) for r in fills]}
    )
    return cash, realized, positions, digest


def quote(db, instrument_id, limits, *, side="sell"):
    view = market.view(db, instrument_id, "1m")
    if view.state != "fresh" or not view.snapshot_id or not view.candles or view.gaps:
        fail(
            "Refresh a continuous 1-minute market series before planning or closing a paper trade."
        )
    if not view.received_at or not view.provider_time:
        fail("The market price has no verified time.")
    stamp = now()
    if (
        not 0 <= (stamp - view.received_at).total_seconds() <= limits.quote_max_age_seconds
        or not 0 <= (stamp - view.provider_time).total_seconds() <= limits.quote_max_age_seconds
    ):
        fail("The paper price is too old for this account’s policy. Refresh market data.")
    if view.instrument.asset_type == "fx":
        from .forex_paper import validate_quote
        from .market_calendars import session_open

        try:
            validate_quote(db, view, stamp)
        except (ValueError, ArithmeticError):
            fail("Forex quote source, checksum or calendar verification failed.")
        if not session_open(view.data_context.model_dump(mode="json"), stamp):
            fail("Forex market is closed. Refresh during an open trading session.")
        bar = view.candles[-1]
        if not bar.bid or not bar.ask or D(bar.bid.close) > D(bar.ask.close):
            fail("Verified Forex bid/ask prices are required.")
        price = D(bar.ask.close if side == "buy" else bar.bid.close)
    elif view.instrument.asset_type in {"equity", "etf"}:
        from . import stock_execution, strategy_execution

        stored = strategy_execution.snapshot_checked(db, view.snapshot_id)
        if (
            stored.instrument_id != instrument_id
            or stored.interval != "1m"
            or stored.data.get("source") != "alpaca_stocks"
        ):
            fail("Stock quote source or instrument does not match its immutable snapshot.")
        price = stock_execution.quote(view, limits.quote_max_age_seconds, stamp=stamp)
    else:
        price = D(view.candles[-1].close)
    if price <= 0:
        fail("The paper price is invalid.")
    return price, view


def position_view(db, entry):
    dep = db.get(PaperDeployment, entry.deployment_id)
    return PaperPositionView(
        id=entry.id,
        account_id=entry.account_id,
        deployment_id=entry.deployment_id,
        instrument_id=dep.instrument_id,
        portfolio=dep.payload["portfolio"],
        capital_pot=dep.payload["capital_pot"],
        strategy=dep.payload["strategy"],
        strategy_version=dep.payload["strategy_version"],
        quantity=entry.payload["quantity"],
        entry_price=entry.payload["price"],
        entry_fee=entry.payload["fee"],
        stop_reference=entry.payload["stop_price"],
        opened_at=entry.created_at,
    )


def account_view(db, acct):
    cash, pnl, _, _ = ledger(db, acct)
    limits, version = policy(db, acct)
    return PaperAccountView(
        id=acct.id,
        name=acct.name,
        currency=acct.currency,
        starting_balance=acct.starting_balance,
        cash=decimal_text(cash),
        realized_pnl=decimal_text(pnl),
        revision=acct.revision,
        halted=acct.halted,
        policy_revision=version.payload["revision"],
        policy=limits,
        reconciled=True,
        created_at=acct.created_at,
    )


def deployment_view(dep, db=None):
    return DeploymentView(
        id=dep.id,
        enabled=strategies.control(db, dep).enabled if db else True,
        control_revision=strategies.control(db, dep).revision if db else 0,
        strategy_revision_id=dep.payload.get("strategy_revision_id"),
        stock_costs=dep.payload.get("stock_costs"),
        fx_financing_daily_bps=dep.payload.get("fx_financing_daily_bps"),
        account_id=dep.account_id,
        instrument_id=dep.instrument_id,
        portfolio=dep.payload["portfolio"],
        capital_pot=dep.payload["capital_pot"],
        strategy=dep.payload["strategy"],
        strategy_version=dep.payload["strategy_version"],
        quantity_step=dep.payload["quantity_step"],
        capital_scope_id=dep.payload.get("capital_scope_id"),
        capital_limit=dep.payload["capital_limit"],
        risk_percent=dep.payload["risk_percent"],
        created_at=dep.created_at,
    )


def state(db, owner):
    from . import autonomous, governance, strategy_execution, strategy_scheduler
    from .models import StrategyRun

    accounts = list(
        db.scalars(
            select(PaperAccount)
            .where(PaperAccount.owner_id == owner.id)
            .order_by(PaperAccount.created_at)
        )
    )
    positions = []
    for acct in accounts:
        _, _, entries, _ = ledger(db, acct)
        positions.extend(position_view(db, entry) for entry in entries.values())
    return PaperState(
        openclaw_ready=any(r.ready for r in governance.status(db, owner).routes),
        autonomy=autonomous.state(db, owner),
        strategy_versions=strategies.versions(db, owner),
        strategy_schedules=strategy_scheduler.views(db, owner),
        strategy_scheduler=strategy_scheduler.status(db),
        strategy_runs=[
            strategy_execution.view(r)
            for r in db.scalars(
                select(StrategyRun)
                .where(StrategyRun.owner_id == owner.id)
                .order_by(StrategyRun.created_at.desc(), StrategyRun.id.desc())
                .limit(200)
            )
        ],
        capital_scopes=[v for a in accounts for v in capital.views(db, a)],
        accounts=[account_view(db, a) for a in accounts],
        deployments=[
            deployment_view(d, db)
            for d in db.scalars(
                select(PaperDeployment)
                .where(PaperDeployment.owner_id == owner.id)
                .order_by(PaperDeployment.created_at)
            )
        ],
        positions=positions,
        records=[
            TradingRecordView.model_validate(r)
            for r in db.scalars(
                select(TradingRecord)
                .where(TradingRecord.owner_id == owner.id)
                .order_by(TradingRecord.created_at.desc(), TradingRecord.id.desc())
                .limit(200)
            )
        ],
        detail="Owner or validated OpenClaw review, unlevered long spot simulation. Stop prices size risk; they are not monitored stop orders.",
    )


def create_account(db, owner, body):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    request = body.model_dump(mode="json")
    existing = db.scalar(
        select(PaperAccount).where(
            PaperAccount.owner_id == owner.id, PaperAccount.request_id == str(body.request_id)
        )
    )
    if existing:
        if existing.request != request:
            fail("This request identifier was already used for different account inputs.")
        return account_view(db, existing)
    acct = PaperAccount(
        id=identifier(),
        owner_id=owner.id,
        request_id=str(body.request_id),
        name=body.name,
        currency=body.currency,
        starting_balance=body.starting_balance,
        request=request,
        revision=1,
        halted=False,
    )
    db.add(acct)
    db.flush()
    append(
        db,
        owner,
        acct,
        "policy",
        body.request_id,
        {
            "revision": 1,
            "policy": body.policy.model_dump(),
            "reason": body.reason,
            "request": request,
        },
    )
    db.commit()
    return account_view(db, acct)


def change_policy(db, owner, account_id, body):
    acct = account(db, owner, account_id)
    if acct.revision != body.expected_revision:
        fail("This account changed in another window. Reload before saving.")
    old = policy_record(db, acct)
    acct.revision += 1
    append(
        db,
        owner,
        acct,
        "policy",
        identifier(),
        {
            "revision": old.payload["revision"] + 1,
            "policy": body.policy.model_dump(),
            "reason": body.reason,
            "request": body.model_dump(mode="json"),
        },
    )
    db.commit()
    return account_view(db, acct)


def halt(db, owner, account_id, body):
    acct = account(db, owner, account_id)
    if acct.revision != body.expected_revision:
        fail("This account changed in another window. Reload before changing its halt state.")
    acct.halted = body.halted
    acct.revision += 1
    append(
        db,
        owner,
        acct,
        "halt",
        identifier(),
        {"halted": body.halted, "reason": body.reason, "revision": acct.revision},
    )
    db.commit()
    return account_view(db, acct)


def create_deployment(db, owner, body):
    acct = account(db, owner, body.account_id)
    request = body.model_dump(mode="json")
    existing = db.scalar(
        select(PaperDeployment).where(
            PaperDeployment.owner_id == owner.id, PaperDeployment.request_id == str(body.request_id)
        )
    )
    if existing:
        if existing.payload != request:
            fail("This request identifier was already used for different deployment inputs.")
        return deployment_view(existing, db)
    if body.strategy_revision_id:
        saved = strategies.version(db, owner, body.strategy_revision_id)
        params = saved.payload["parameters"]
        if not body.capital_scope_id or any(
            [
                body.strategy != saved.name,
                body.strategy_version != params["label"],
                body.quantity_step != params["quantity_step"],
                body.capital_limit != params["capital_limit"],
                body.risk_percent != params["risk_percent"],
            ]
        ):
            fail(
                "Deployment parameters must match the immutable strategy version and a selected allocation.",
                422,
            )
    nodes = capital.current(db, acct)
    if body.capital_scope_id:
        ancestry = capital.chain(nodes, str(body.capital_scope_id))
        if body.portfolio != ancestry[-1].name or body.capital_pot != ancestry[0].name:
            fail("Strategy attribution must match the selected portfolio and allocation.", 422)
        if D(body.capital_limit) > D(ancestry[0].payload["limits"]["budget"]):
            fail("Strategy capital limit exceeds its shared allocation.", 422)
    elif nodes:
        fail("Select a shared capital allocation for this strategy.", 422)
    spec = market.instrument(db, body.instrument_id)
    if (
        spec.asset_type not in {"crypto_spot", "fx", "equity", "etf"}
        or spec.quote_currency != acct.currency
    ):
        fail("Paper deployment must use an unlevered spot instrument in the account currency.", 422)
    if spec.asset_type == "fx" and body.fx_financing_daily_bps is None:
        fail(
            "Forex paper deployment needs an explicit daily financing assumption, including zero.",
            422,
        )
    if spec.asset_type in {"equity", "etf"}:
        from .stock_execution import deployment as validate_stock_deployment

        validate_stock_deployment(spec, acct.currency, body.quantity_step, body.stock_costs)
    elif body.stock_costs is not None:
        fail("Stock cost assumptions only apply to a stock or ETF deployment.", 422)
    if spec.asset_type != "fx" and body.fx_financing_daily_bps is not None:
        fail("Forex financing cannot be assigned to crypto spot.", 422)
    if D(body.risk_percent) > 100 or D(body.quantity_step) >= D("1e12"):
        fail("Deployment risk must be at most 100%, with a usable paper quantity step.", 422)
    dep = PaperDeployment(
        id=identifier(),
        owner_id=owner.id,
        request_id=str(body.request_id),
        account_id=acct.id,
        instrument_id=spec.id,
        payload=request,
    )
    db.add(dep)
    db.flush()
    services.audit(
        db,
        owner,
        "paper.deployment",
        "paper_deployment",
        dep.id,
        body.reason,
        {"account_id": acct.id, "mode": "paper"},
    )
    db.commit()
    return deployment_view(dep, db)


def propose(db, owner, body, *, commit=True, autonomous_context=None):
    dep = deployment(db, owner, body.deployment_id)
    acct = account(db, owner, dep.account_id)
    request = body.model_dump(mode="json")
    if autonomous_context is not None:
        request["autonomous_context"] = autonomous_context
    existing = replay(db, owner, "proposal", body.request_id, request)
    if existing:
        return existing
    if autonomous_context is not None:
        from .autonomous import guard_financial

        guard_financial(db, owner, dep, autonomous_context)
    if acct.halted:
        fail("This paper account is halted.")
    strategies.require_enabled(db, dep)
    nodes, _ = capital.assigned(db, acct, dep)
    limits, pol = policy(db, acct)
    from . import strategy_execution

    signal = strategy_execution.entry_signal(db, owner, dep, body)
    observed, snapshot = quote(db, dep.instrument_id, limits, side="buy")
    if D(body.stop_price) >= observed:
        fail("A long paper trade needs a stop reference below the observed price.", 422)
    report_checksum = None
    is_stock = snapshot.instrument.asset_type in {"equity", "etf"}
    if is_stock and not body.native_artifact_id:
        fail(
            "Attach current daily native stock/ETF research before requesting a paper review.", 422
        )
    if body.native_artifact_id:
        artifact = db.get(AnalysisArtifact, str(body.native_artifact_id))
        if (
            not artifact
            or artifact.owner_id != owner.id
            or hashlib.sha256(
                json.dumps(artifact.payload, sort_keys=True, allow_nan=False).encode()
            ).hexdigest()
            != artifact.checksum
        ):
            fail("This original native report cannot be verified.", 422)
        job = db.get(AnalysisJob, artifact.job_id)
        if job and (
            job.request.get("mode") == "broker_entry_assessment"
            or "broker_setup_assessment" in artifact.payload
        ):
            fail(
                "Broker setup assessment cannot approve a simulated paper entry; use its separate broker governance path.",
                422,
            )
        if (
            job.request.get("mode") == "strategy_qualification"
            or "strategy_assessment" in artifact.payload
        ):
            fail(
                "Strategy qualification evidence cannot approve a paper entry. Request current daily trading research separately.",
                422,
            )
        if job.instrument_id != dep.instrument_id:
            fail("The native report belongs to a different instrument.", 422)
        if is_stock:
            required = {
                "market_report",
                "sentiment_report",
                "news_report",
                "final_trade_decision",
                "investment_plan",
                "trader_investment_plan",
                "research_debate",
                "risk_debate",
            }
            if snapshot.instrument.asset_type == "equity":
                required.add("fundamentals_report")
            if (
                job.status != "succeeded"
                or job.request.get("mode") != "current_daily_research"
                or job.request.get("trade_date") != now().date().isoformat()
                or artifact.payload.get("request") != job.request
                or any(
                    not isinstance(artifact.payload.get("reports", {}).get(k), str)
                    or not artifact.payload["reports"][k].strip()
                    for k in required
                )
            ):
                fail(
                    "Complete current daily native stock research is required; qualification and stale reports cannot substitute.",
                    422,
                )
        report_checksum = artifact.checksum
    row = append(
        db,
        owner,
        acct,
        "proposal",
        body.request_id,
        {
            "request": request,
            **({"autonomous_context": autonomous_context} if autonomous_context else {}),
            "reason": body.reason,
            "side": "buy",
            "capital_digest": capital.digest(nodes),
            "strategy_control_revision": strategies.control(db, dep).revision,
            **(
                {
                    "strategy_run_id": signal.id,
                    "strategy_run_checksum": signal.checksum,
                    "strategy_signal_key": signal.payload["signal_key"],
                }
                if signal
                else {}
            ),
            "stop_price": body.stop_price,
            "instrument_id": dep.instrument_id,
            "native_artifact_id": str(body.native_artifact_id) if body.native_artifact_id else None,
            "native_checksum": report_checksum,
            "native_currency_note": "Native stock research and paper values use USD; chart observations and native sources remain separate."
            if snapshot.instrument.asset_type in {"equity", "etf"}
            else "Forex and paper values use the pair quote currency."
            if snapshot.instrument.asset_type == "fx"
            else "Native research uses USD; paper values use the account currency.",
            "snapshot_id": snapshot.snapshot_id,
            "observed_price": decimal_text(observed),
            "policy_id": pol.id,
            "mode": "paper",
        },
        dep.id,
    )
    if commit:
        db.commit()
    return row


def review(db, owner, proposal_id, body):
    prop = record(db, owner, proposal_id, "proposal")
    acct = account(db, owner, prop.account_id)
    request = {**body.model_dump(mode="json"), "proposal_id": prop.id}
    existing = replay(db, owner, "governance", body.request_id, request)
    if existing:
        return existing
    from .governance import pending

    if pending(db, prop.id):
        fail("An OpenClaw review is active for this proposal. Wait for it to finish.")
    if acct.halted:
        fail("This paper account is halted.")
    strategies.validate_proposal(db, deployment(db, owner, prop.deployment_id), prop)
    capital.validate_proposal(db, acct, deployment(db, owner, prop.deployment_id), prop)
    limits, pol = policy(db, acct)
    if prop.payload["policy_id"] != pol.id:
        fail("The policy changed. Create a new proposal.")
    quote(db, prop.payload["instrument_id"], limits)
    _, _, _, digest = ledger(db, acct)
    row = append(
        db,
        owner,
        acct,
        "governance",
        body.request_id,
        {
            "request": request,
            "reason": body.reason,
            "proposal_id": prop.id,
            "proposal_checksum": prop.checksum,
            "outcome": body.outcome,
            "source": "owner",
            "authority": "paper_only",
            "policy_id": pol.id,
            "account_revision": acct.revision,
            "ledger_digest": digest,
            "expires_at": (now() + timedelta(seconds=limits.decision_ttl_seconds)).isoformat(),
            "native_artifact_id": prop.payload["native_artifact_id"],
        },
        prop.deployment_id,
        prop.id,
    )
    db.commit()
    return row


def sizing(db, acct, dep, prop, limits, cash, entries, observed):
    if dep.payload.get("stock_costs") or any(
        db.get(PaperDeployment, held.deployment_id).payload.get("stock_costs")
        for held in entries.values()
    ):
        from .stock_paper import sizing as size_with_stock_costs

        return size_with_stock_costs(db, acct, dep, prop, limits, cash, entries, observed)
    with localcontext() as context:
        context.prec = 120
        fee_rate, slip = D(limits.fee_bps) / 10000, D(limits.slippage_bps) / 10000
        entry, stop = observed * (1 + slip), D(prop.payload["stop_price"])
        if stop >= observed:
            fail("The observed market price crossed the stop reference. Create a new proposal.")
        stop_fill = stop * (1 - slip)
        per_unit_risk = entry - stop_fill + fee_rate * (entry + stop_fill)
        exposure, open_risk, deployment_exposure = ZERO, ZERO, ZERO
        marks = {}
        for held in entries.values():
            held_dep = db.get(PaperDeployment, held.deployment_id)
            if held_dep.instrument_id not in marks:
                marks[held_dep.instrument_id] = quote(db, held_dep.instrument_id, limits)[0]
            mark = marks[held_dep.instrument_id]
            if mark <= D(held.payload["stop_price"]):
                fail(
                    "An open paper position crossed its stop reference. Close it before adding entries."
                )
            q = D(held.payload["quantity"])
            exposure += q * mark
            open_risk += (
                q * max(ZERO, mark - D(held.payload["stop_price"]) * (1 - slip))
                + q * mark * fee_rate
            )
            if held.deployment_id == dep.id:
                deployment_exposure += q * mark
        equity = cash + exposure
        if len(entries) >= limits.max_positions:
            fail("The account’s open-position limit is reached.")
        strategy_capital = D(dep.payload["capital_limit"])
        risk_budget = min(
            equity * D(limits.risk_percent) / 100,
            strategy_capital * D(dep.payload["risk_percent"]) / 100,
            equity * D(limits.max_total_risk_percent) / 100 - open_risk,
        )
        capacity = min(
            cash / (entry * (1 + fee_rate)),
            (equity * D(limits.max_exposure_percent) / 100 - exposure) / entry,
            (strategy_capital - deployment_exposure) / entry,
        )
        nodes, ancestry = capital.assigned(db, acct, dep)
        risk_budget, capacity, allocation_evidence = capital.constrain(
            db, nodes, ancestry, entries, marks, fee_rate, slip, entry, risk_budget, capacity
        )
        step = D(dep.payload["quantity_step"])
        quantity = (min(risk_budget / per_unit_risk, capacity) / step).to_integral_value(
            rounding=ROUND_DOWN
        ) * step
        if quantity <= 0:
            fail("Risk, cash or capital limits leave no tradable paper quantity.")
        return entry, stop, per_unit_risk, quantity, allocation_evidence


def plan(db, owner, decision_id, body, *, commit=True):
    decision = record(db, owner, decision_id, "governance")
    acct = account(db, owner, decision.account_id)
    request = {**body.model_dump(mode="json"), "decision_id": decision.id}
    existing = replay(db, owner, "plan", body.request_id, request)
    if existing:
        return existing
    if acct.halted:
        fail("This paper account is halted.")
    limits, pol = policy(db, acct)
    cash, _, entries, digest = ledger(db, acct)
    dp = decision.payload
    from datetime import datetime

    if dp["outcome"] != "allow" or datetime.fromisoformat(dp["expires_at"]) <= now():
        fail("The paper decision is denied, deferred or expired.")
    if (
        dp["policy_id"] != pol.id
        or dp["account_revision"] != acct.revision
        or dp["ledger_digest"] != digest
    ):
        fail("Account balances or policy changed after review. Create a new proposal.")
    prop = record(db, owner, dp["proposal_id"], "proposal")
    if prop.checksum != dp["proposal_checksum"]:
        fail("Proposal lineage cannot be verified.")
    dep = deployment(db, owner, prop.deployment_id)
    if dep.payload.get("stock_costs"):
        from .stock_paper import bound_review

        bound_review(db, decision, prop)
    from .autonomous import guard_proposal

    guard_proposal(db, owner, dep, prop)
    if prop.payload.get("autonomous_context"):
        from .models import AutonomyWorkflow

        workflow = db.get(AutonomyWorkflow, prop.payload["autonomous_context"]["workflow_id"])
        decision_payload = decision.payload
        if (
            decision_payload.get("source") != "openclaw"
            or decision_payload.get("job_id") != workflow.review_job_id
        ):
            fail("An autonomous paper entry requires its bound OpenClaw permission.")
    strategies.validate_proposal(db, dep, prop)
    capital.validate_proposal(db, acct, dep, prop)
    observed, snapshot = quote(db, dep.instrument_id, limits, side="buy")
    with localcontext() as context:
        context.prec = 120
        entry, stop, per_unit_risk, quantity, allocation_evidence = sizing(
            db, acct, dep, prop, limits, cash, entries, observed
        )
        from .stock_paper import fee as execution_fee

        fee = execution_fee(dep, quantity, entry, "buy", limits)
        expires = min(
            snapshot.received_at + timedelta(seconds=limits.quote_max_age_seconds),
            snapshot.provider_time + timedelta(seconds=limits.quote_max_age_seconds),
            datetime.fromisoformat(dp["expires_at"]),
        )
        payload = {
            "request": request,
            **(
                {"autonomous_context": prop.payload["autonomous_context"]}
                if prop.payload.get("autonomous_context")
                else {}
            ),
            "reason": body.reason,
            "decision_id": decision.id,
            "proposal_id": prop.id,
            "native_artifact_id": prop.payload["native_artifact_id"],
            "policy_id": pol.id,
            "account_revision": acct.revision,
            "ledger_digest": digest,
            "instrument_id": dep.instrument_id,
            "mode": "paper",
            "side": "buy",
            "quantity": decimal_text(quantity),
            "quantity_step": dep.payload["quantity_step"],
            "price": decimal_text(entry),
            "fee": decimal_text(fee),
            "estimated_stop_risk": decimal_text(quantity * per_unit_risk),
            "capital_allocations": allocation_evidence,
            "stop_price": decimal_text(stop),
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_checksum": snapshot.checksum,
            "observed_price": decimal_text(observed),
            "expires_at": expires.isoformat(),
            **(
                {
                    "stock_costs": dep.payload["stock_costs"],
                    "market_data_context": snapshot.data_context.model_dump(mode="json"),
                }
                if dep.payload.get("stock_costs")
                else {}
            ),
            "price_model": "Selected Alpaca regular-session 1m trade close with explicit spread, adverse slippage, share/minimum commissions and sell tax. Whole-share, unlevered USD cash simulation; no NBBO, broker fills, partial liquidity, dividends, settlement or corporate-action ledger model."
            if snapshot.instrument.asset_type in {"equity", "etf"}
            else "Latest OANDA 1m ask close for entry and bid close for exit/marks, with adverse slippage and fees. Long-only unlevered; no broker fills or liquidity model. Explicit fixed financing uses opening notional, NY17 weekday rollover and is settled at close."
            if snapshot.instrument.asset_type == "fx"
            else "Latest observed 1m candle close, receipt-stamped; configured adverse slippage and fee. No bid/ask or liquidity model.",
            "lineage": {
                **(
                    {
                        "strategy_run_id": prop.payload["strategy_run_id"],
                        "strategy_run_checksum": prop.payload["strategy_run_checksum"],
                    }
                    if prop.payload.get("strategy_run_id")
                    else {}
                ),
                "account_id": acct.id,
                "portfolio": dep.payload["portfolio"],
                "capital_pot": dep.payload["capital_pot"],
                "capital_scope_id": dep.payload.get("capital_scope_id"),
                "capital_versions": allocation_evidence,
                "strategy": dep.payload["strategy"],
                "strategy_version": dep.payload["strategy_version"],
                "strategy_revision_id": dep.payload.get("strategy_revision_id"),
                "deployment_id": dep.id,
                "mode": "paper",
            },
        }
    row = append(db, owner, acct, "plan", body.request_id, payload, dep.id, decision.id)
    if commit:
        db.commit()
    return row


class PaperAdapter:
    """Apply an already validated immutable plan exactly once under an account lock."""

    def fill(self, db, owner, acct, planned, request_id, request, reason):
        p = planned.payload
        row = append(
            db,
            owner,
            acct,
            "fill",
            request_id,
            {
                **p,
                "request": request,
                "reason": reason,
                "plan_id": planned.id,
                "simulated": True,
                "adapter": "paper_spot_v1",
                "cash_delta": decimal_text(
                    -(D(p["quantity"]) * D(p["price"]) + D(p["fee"]))
                    if p["side"] == "buy"
                    else D(p["quantity"]) * D(p["price"]) - D(p["fee"]) - D(p.get("financing", "0"))
                ),
            },
            planned.deployment_id,
            planned.id,
        )
        ledger(db, acct)
        return row


def execute(db, owner, plan_id, body, *, commit=True):
    from datetime import datetime

    planned = record(db, owner, plan_id, "plan")
    acct = account(db, owner, planned.account_id)
    request = {**body.model_dump(mode="json"), "plan_id": planned.id}
    existing = replay(db, owner, "fill", body.request_id, request)
    if existing:
        return existing
    p = planned.payload
    if p.get("side") != "buy":
        fail("Exit plans are applied only through their attributed close action.")
    if acct.halted:
        fail("This paper account is halted. Closing an existing position is still available.")
    limits, pol = policy(db, acct)
    cash, _, entries, digest = ledger(db, acct)
    if (
        p["policy_id"] != pol.id
        or p["account_revision"] != acct.revision
        or p["ledger_digest"] != digest
    ):
        fail("Account balances or policy changed. Create a new proposal.")
    observed, snapshot = quote(db, p["instrument_id"], limits, side="buy")
    if (
        datetime.fromisoformat(p["expires_at"]) <= now()
        or snapshot.snapshot_id != p["snapshot_id"]
        or snapshot.checksum != p["snapshot_checksum"]
        or decimal_text(observed) != p["observed_price"]
    ):
        fail("The paper plan’s price expired or changed. Create a new proposal.")
    dep = deployment(db, owner, planned.deployment_id)
    prop = record(db, owner, p["proposal_id"], "proposal")
    if dep.payload.get("stock_costs"):
        from .stock_paper import bound_review

        bound_review(db, record(db, owner, p["decision_id"], "governance"), prop)
    from .autonomous import guard_proposal

    guard_proposal(db, owner, dep, prop)
    if prop.payload.get("autonomous_context"):
        from .models import AutonomyWorkflow

        workflow = db.get(AutonomyWorkflow, prop.payload["autonomous_context"]["workflow_id"])
        decision_payload = record(db, owner, p["decision_id"], "governance").payload
        if (
            decision_payload.get("source") != "openclaw"
            or decision_payload.get("job_id") != workflow.review_job_id
        ):
            fail("An autonomous paper entry requires its bound OpenClaw permission.")
    strategies.validate_proposal(db, dep, prop)
    capital.validate_proposal(db, acct, dep, prop)
    _, _, _, available_quantity, _ = sizing(db, acct, dep, prop, limits, cash, entries, observed)
    if D(p["quantity"]) > available_quantity:
        fail("Current shared risk or capital no longer supports this plan. Create a new proposal.")
    with localcontext() as context:
        context.prec = 120
        row = PaperAdapter().fill(db, owner, acct, planned, body.request_id, request, body.reason)
    if commit:
        db.commit()
    return row


def close(db, owner, position_id, body, *, strategy_run=None, commit=True, autonomous_context=None):
    entry = record(db, owner, position_id, "fill")
    acct = account(db, owner, entry.account_id)
    request = {**body.model_dump(mode="json"), "position_id": entry.id}
    if autonomous_context is not None:
        request["autonomous_context"] = autonomous_context
    if strategy_run:
        request["strategy_run_id"] = strategy_run.id
    existing = replay(db, owner, "fill", body.request_id, request)
    if existing:
        return existing
    if autonomous_context is not None:
        from .autonomous import guard_financial

        guard_financial(
            db,
            owner,
            deployment(db, owner, entry.deployment_id),
            autonomous_context,
            exit_action=True,
        )
    _, _, positions, digest = ledger(db, acct)
    if entry.id not in positions:
        fail("This attributed position is already closed.")
    limits, pol = policy(db, acct)
    observed, snapshot = quote(db, entry.payload["instrument_id"], limits)
    with localcontext() as context:
        context.prec = 120
        from .stock_paper import fee as execution_fee
        from .stock_paper import price as execution_price

        route = deployment(db, owner, entry.deployment_id)
        price = execution_price(route, observed, "sell", limits)
        quantity = D(entry.payload["quantity"])
        fee = execution_fee(route, quantity, price, "sell", limits)
        from .forex_paper import closing_financing

        financing, financing_events = closing_financing(db, entry, now())
        pnl = (
            quantity * (price - D(entry.payload["price"]))
            - fee
            - D(entry.payload["fee"])
            - financing
        )
        planned = append(
            db,
            owner,
            acct,
            "plan",
            body.request_id,
            {
                "request": request,
                "reason": body.reason,
                "side": "sell",
                "mode": "paper",
                "position_id": entry.id,
                "quantity": decimal_text(quantity),
                "price": decimal_text(price),
                "fee": decimal_text(fee),
                "realized_pnl": decimal_text(pnl),
                "financing": decimal_text(financing),
                "financing_events": financing_events,
                "instrument_id": entry.payload["instrument_id"],
                "snapshot_id": snapshot.snapshot_id,
                "snapshot_checksum": snapshot.checksum,
                "observed_price": decimal_text(observed),
                "policy_id": pol.id,
                "ledger_digest": digest,
                "exit_source": "autonomous_rule_close"
                if autonomous_context
                else "owner_rule_close"
                if strategy_run
                else "owner_close",
                **({"autonomous_context": autonomous_context} if autonomous_context else {}),
                **(
                    {
                        "strategy_exit_run_id": strategy_run.id,
                        "strategy_exit_run_checksum": strategy_run.checksum,
                        "exit_trigger": strategy_run.payload["exit_trigger"],
                    }
                    if strategy_run
                    else {}
                ),
                "lineage": entry.payload["lineage"],
                "opening_decision_id": entry.payload["decision_id"],
                "native_artifact_id": entry.payload["native_artifact_id"],
                "price_model": entry.payload["price_model"],
                **(
                    {
                        "stock_costs": route.payload["stock_costs"],
                        "market_data_context": snapshot.data_context.model_dump(mode="json"),
                    }
                    if route.payload.get("stock_costs")
                    else {}
                ),
            },
            entry.deployment_id,
            entry.id,
        )
        row = PaperAdapter().fill(db, owner, acct, planned, body.request_id, request, body.reason)
    if commit:
        db.commit()
    return row
