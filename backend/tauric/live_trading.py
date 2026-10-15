"""Prospective broker-demo evidence and explicit live promotion boundaries."""

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select

from . import (
    broker_execution,
    broker_positions,
    broker_review,
    market,
    qualification,
    strategies,
    valuation,
)
from .broker_adapters import BrokerError
from .broker_models import BrokerCloseRequest, BrokerOrderIntent, BrokerPositionCursor
from .live_contracts import ReadinessRecordView, TradingReadinessView
from .live_models import TradingReadinessRecord
from .models import Owner, WorkerStatus, now
from .outcomes import drawdown

D = Decimal


def signature():
    root = Path(__file__).parent
    files = (
        "broker_adapters.py",
        "broker_positions.py",
        "broker_lifecycle_adapters.py",
        "broker_setup.py",
        "execution_scope.py",
        "broker_review.py",
        "broker_risk.py",
        "strategy_rules.py",
        "data_quality.py",
        "native_broker_setup.py",
        "native_runner.py",
        "native_fx.py",
        "analysis.py",
        "broker_contracts.py",
        "broker_routes.py",
        "broker_execution.py",
        "broker_worker.py",
        "live_trading.py",
        "valuation.py",
        "valuation_worker.py",
        "forward_worker.py",
        "operations.py",
        "operations_models.py",
        "live_contracts.py",
    )
    return {
        "execution_model": "broker_protected_long_v2",
        "source_checksum": hashlib.sha256(
            b"".join((name.encode() + (root / name).read_bytes()) for name in files)
        ).hexdigest(),
    }


def record_view(row):
    valuation.checked(row)
    return ReadinessRecordView(
        id=row.id,
        kind=row.kind,
        checksum=row.checksum,
        payload=row.payload,
        created_at=row.created_at,
    )


def owned(db, owner, key, kind):
    row = db.get(TradingReadinessRecord, str(key))
    if not row or row.owner_id != owner.id or row.kind != kind:
        raise HTTPException(404, "This trading readiness record does not exist.")
    return valuation.checked(row)


def register(db, owner, body):
    previous = db.scalar(
        select(TradingReadinessRecord).where(
            TradingReadinessRecord.owner_id == owner.id,
            TradingReadinessRecord.request_id == str(body.request_id),
        )
    )
    if previous:
        return record_view(valuation.append(db, owner, "forward_protocol", body, {}))
    spec = market.instrument(db, body.instrument_id)
    if (body.provider == "oanda") != (spec.asset_type == "fx") or spec.asset_type not in {
        "fx",
        "equity",
        "etf",
    }:
        raise HTTPException(422, "Select a matching FX or stock broker instrument.")
    strategy = strategies.version(db, owner, body.strategy_version_id)
    if not strategy.payload["parameters"].get("rules"):
        raise HTTPException(422, "Forward validation requires executable saved strategy rules.")
    config, revision = broker_execution.connection(db, body.provider)
    if config.environment != ("practice" if body.provider == "oanda" else "paper"):
        raise HTTPException(
            409, "Register forward validation on the broker practice/paper account first."
        )
    baseline = valuation.broker_account(db, owner, body.provider)
    if not baseline["complete"] or baseline["positions"]:
        raise HTTPException(
            409,
            "Forward account must have a complete valuation and no existing holdings. Use a dedicated demo account.",
        )
    with broker_execution.adapter(body.provider, config) as broker:
        if body.provider == "oanda":
            summary = broker.request("GET", broker.path("/summary"))["account"]
            checkpoint = summary["lastTransactionID"]
        else:
            checkpoint = None
        pending = broker.request(
            "GET",
            broker.path("/pendingOrders") if body.provider == "oanda" else "/v2/orders",
            params=None if body.provider == "oanda" else {"status": "open", "limit": 500},
        )
        if pending.get("orders", []) if isinstance(pending, dict) else pending:
            raise HTTPException(
                409, "Resolve open demo orders before registering forward validation."
            )
    report_ids = []
    for report in db.scalars(
        select(qualification.QualificationReport)
        .where(qualification.QualificationReport.owner_id == owner.id)
        .order_by(qualification.QualificationReport.created_at.desc())
    ):
        qualification.checked(report)
        if (
            report.payload.get("strategy_version_id") != strategy.id
            or report.payload.get("status") != "passed"
        ):
            continue
        research_protocol = qualification.owned(
            db, owner, qualification.QualificationProtocol, report.protocol_id
        )
        if (
            research_protocol.payload["instrument_id"] == spec.id
            and research_protocol.payload["engine_checksum"]
            == valuation.paper.checksum(qualification.sources())
            and report.payload.get("strategy_checksum") == strategy.checksum
        ):
            latest_research = db.scalar(
                select(qualification.QualificationReport)
                .where(qualification.QualificationReport.protocol_id == report.protocol_id)
                .order_by(qualification.QualificationReport.revision.desc())
                .limit(1)
            )
            if latest_research.id == report.id:
                report_ids.append(report.id)
    payload = {
        "strategy_checksum": strategy.checksum,
        "execution_signature": signature(),
        "connection_revision": revision,
        "account_fingerprint": baseline["account_fingerprint"],
        "currency": baseline["currency"],
        "starting_equity": baseline["equity"],
        "account_key": baseline["key"],
        "transaction_checkpoint": checkpoint,
        "baseline": baseline,
        "research_report_ids": report_ids,
        "execution_authority": False,
        "limitations": [
            "Prospective demo observations do not establish live fill quality or statistical certainty.",
            "Account must remain dedicated to this exact strategy and instrument; cash transfers and unrelated activity invalidate the window.",
            "Drawdown is measured at observed valuation timestamps; gaps in observations prevent promotion.",
        ],
    }
    row = valuation.append(db, owner, "forward_protocol", body, payload)
    db.commit()
    return record_view(row)


def account_activity(broker, protocol, ending):
    """Bounded, unfiltered account activity proves cash flows were not omitted."""
    if broker.provider == "oanda":
        latest = broker.request("GET", broker.path("/summary"))["account"]["lastTransactionID"]
        first, last = int(protocol.payload["transaction_checkpoint"]) + 1, int(latest)
        if last < first - 1 or last - first + 1 > 10000:
            raise BrokerError(
                "Forward account transaction history is inconsistent or exceeds collection bounds."
            )
        rows = []
        for start in range(first, last + 1, 1000):
            data = broker.request(
                "GET",
                broker.path("/transactions/idrange"),
                params={"from": str(start), "to": str(min(last, start + 999))},
            )["transactions"]
            if not isinstance(data, list) or len(data) > 1000:
                raise BrokerError("Forward transaction response is invalid.")
            rows.extend(data)
        if [int(row["id"]) for row in rows] != list(range(first, last + 1)):
            raise BrokerError("Forward transaction sequence is incomplete.")
        if any(row.get("accountID") != broker.config.account_id for row in rows):
            raise BrokerError("Forward transaction account identity is inconsistent.")
        return rows
    rows, seen, token = [], set(), None
    for _ in range(100):
        params = {
            "after": protocol.created_at.isoformat(),
            "until": ending.isoformat(),
            "direction": "asc",
            "page_size": 100,
        }
        if token:
            params["page_token"] = token
        page = broker.request("GET", "/v2/account/activities", params=params)
        if not isinstance(page, list) or len(page) > 100:
            raise BrokerError("Forward account activity response is invalid.")
        for row in page:
            if row["id"] in seen:
                raise BrokerError("Forward account activity pagination duplicated an item.")
            seen.add(row["id"])
        rows.extend(page)
        if len(page) < 100:
            return rows
        token = page[-1]["id"]
    raise BrokerError("Forward activity exceeds collection bounds.")


def evaluate(db, owner, protocol_id, body):
    with localcontext() as precision:
        precision.prec = 120
        return _evaluate(db, owner, protocol_id, body)


def _evaluate(db, owner, protocol_id, body):
    protocol = owned(db, owner, protocol_id, "forward_protocol")
    previous = db.scalar(
        select(TradingReadinessRecord).where(
            TradingReadinessRecord.owner_id == owner.id,
            TradingReadinessRecord.request_id == str(body.request_id),
        )
    )
    if previous:
        return record_view(valuation.append(db, owner, "forward_report", body, {}, protocol.id))
    frozen = protocol.payload["request"]
    criteria, entries, closed, latencies, slippage, risks, ids = [], [], [], [], [], [], set()

    def check(name, passed, detail):
        criteria.append(
            {"name": name, "status": "passed" if passed else "incomplete", "detail": detail}
        )

    check(
        "execution_model",
        protocol.payload["execution_signature"] == signature(),
        "Execution source must match the version registered before observing outcomes.",
    )
    strategy = strategies.version(db, owner, frozen["strategy_version_id"])
    check(
        "strategy_version",
        strategy.checksum == protocol.payload["strategy_checksum"],
        "Exact immutable strategy version and parameters.",
    )
    check(
        "prospective_duration",
        now() >= protocol.created_at + timedelta(days=frozen["minimum_days"]),
        f"At least {frozen['minimum_days']} prospective days.",
    )
    config, revision = broker_execution.connection(db, frozen["provider"])
    check(
        "demo_connection",
        revision == protocol.payload["connection_revision"] and config.environment != "live",
        "Same demo account and connection throughout the validation window.",
    )
    observations = []
    try:
        with broker_execution.adapter(frozen["provider"], config) as broker:
            account = broker.account()
            if account.account_fingerprint != protocol.payload["account_fingerprint"]:
                raise BrokerError("Forward account identity changed.")
            journal_entries = db.scalars(
                select(BrokerOrderIntent)
                .where(
                    BrokerOrderIntent.owner_id == owner.id,
                    BrokerOrderIntent.provider == frozen["provider"],
                    BrokerOrderIntent.account_fingerprint == account.account_fingerprint,
                    BrokerOrderIntent.created_at >= protocol.created_at,
                    BrokerOrderIntent.created_at <= now(),
                )
                .order_by(BrokerOrderIntent.created_at)
            ).all()
            clean = True
            for entry in journal_entries:
                broker_execution.owned(db, owner, entry.id)
                if (
                    entry.payload.get("strategy_version_id") != strategy.id
                    or not entry.review_decision_id
                    or entry.payload["order"]["symbol"]
                    != market.instrument(db, frozen["instrument_id"]).provider_symbol
                ):
                    clean = False
                    continue
                permission, job = broker_review.decision(db, owner, entry.review_decision_id)
                if permission.payload["outcome"] != "allow":
                    clean = False
                setup = job.context["setup_evidence"]
                approved = permission.payload["approved_order"]
                loss_factor = (
                    D(1)
                    if frozen["provider"] == "alpaca"
                    or approved["symbol"][-3:] == setup["account"]["currency"]
                    else D(setup["quote"]["quote_to_home_negative"])
                )
                risk = (
                    D(approved["quantity"])
                    * (D(setup["quote"]["ask"]) - D(approved["stop_loss"]))
                    * loss_factor
                    / D(setup["account"]["equity"])
                    * 100
                )
                if not risk.is_finite() or not 0 < risk <= D(frozen["risk_percent"]):
                    clean = False
                risks.append(risk)
                position = broker_positions.collect(db, entry, broker)
                broker_positions.save_position(db, owner, entry.id, position)
                entries.append(
                    {
                        "id": entry.id,
                        "checksum": entry.checksum,
                        "position": position.model_dump(mode="json"),
                        "native_artifact_id": permission.native_artifact_id,
                        "review_checksum": permission.checksum,
                    }
                )
                ids.update(
                    fill.broker_order_id for fill in position.entry_fills + position.exit_fills
                )
                ids.update(p.broker_order_id for p in position.protections)
                ids.update(t for t in position.trade_ids)
                history = broker_execution.events(db, entry)
                order = next(
                    (e.payload["order"] for e in reversed(history) if e.payload.get("order")), None
                )
                if order:
                    ids.add(order["broker_order_id"])
                if position.status == "closed":
                    closed.append(position)
                elif position.status != "unfilled":
                    clean = False
                if entry.status not in broker_execution.TERMINAL:
                    clean = False
                if position.entry_fills:
                    filled_at = min(fill.occurred_at for fill in position.entry_fills)
                elif position.trade_ids:
                    raw = broker.request("GET", broker.path("/trades/" + position.trade_ids[0]))[
                        "trade"
                    ]
                    filled_at = datetime.fromisoformat(raw["openTime"].replace("Z", "+00:00"))
                else:
                    continue
                if not protocol.created_at <= filled_at <= now():
                    clean = False
                signal = datetime.fromisoformat(
                    job.context["setup_evidence"]["signal"]["bar_close_time"]
                )
                latencies.append((filled_at - signal).total_seconds())
                quote = D(job.context["setup_evidence"]["quote"]["ask"])
                if D(position.entry_quantity):
                    slippage.append(
                        (D(position.entry_notional) / D(position.entry_quantity) / quote - 1)
                        * 10000
                    )
            activity = account_activity(broker, protocol, now())
            relevant = []
            for item in activity:
                kind = (
                    item.get("type") if frozen["provider"] == "oanda" else item.get("activity_type")
                )
                if frozen["provider"] == "oanda":
                    if kind == "DAILY_FINANCING":
                        # Financing is allowed only when its trades belong to this registered run.
                        if any(
                            trade["tradeID"] not in ids
                            for position_financing in item.get("positionFinancings", [])
                            for trade in position_financing.get("openTradeFinancings", [])
                        ):
                            clean = False
                    elif kind == "ORDER_FILL":
                        if item.get("orderID") not in ids:
                            clean = False
                    elif kind not in {
                        "MARKET_ORDER",
                        "LIMIT_ORDER",
                        "STOP_ORDER",
                        "STOP_LOSS_ORDER",
                        "TAKE_PROFIT_ORDER",
                        "TRAILING_STOP_LOSS_ORDER",
                        "ORDER_CANCEL",
                        "ORDER_CLIENT_EXTENSIONS_MODIFY",
                        "TRADE_CLIENT_EXTENSIONS_MODIFY",
                    }:
                        # Unknown account adjustments cannot silently become strategy profit.
                        clean = False
                elif kind == "FILL":
                    if item.get("order_id") not in ids:
                        clean = False
                elif kind not in {"FEE", "CFEE", "INT"}:
                    clean = False
                relevant.append(
                    {
                        key: item[key]
                        for key in (
                            "id",
                            "type",
                            "activity_type",
                            "orderID",
                            "order_id",
                            "net_amount",
                            "financing",
                            "commission",
                        )
                        if key in item
                    }
                )
            unresolved = db.scalar(
                select(BrokerCloseRequest.id)
                .join(BrokerOrderIntent, BrokerOrderIntent.id == BrokerCloseRequest.entry_intent_id)
                .where(
                    BrokerOrderIntent.account_fingerprint == account.account_fingerprint,
                    BrokerOrderIntent.owner_id == owner.id,
                    BrokerCloseRequest.status.not_in(["filled", "rejected"]),
                )
                .limit(1)
            )
            pending = broker.request(
                "GET",
                broker.path("/pendingOrders") if frozen["provider"] == "oanda" else "/v2/orders",
                params=None if frozen["provider"] == "oanda" else {"status": "open", "limit": 500},
            )
            has_pending = bool(pending.get("orders", []) if isinstance(pending, dict) else pending)
            check(
                "dedicated_reconciled_account",
                clean and not broker.positions() and not unresolved and not has_pending,
                "All executions must belong to the registered native/OpenClaw strategy; no cash transfers, unrelated holdings or unresolved closes.",
            )
            net = D(account.equity) - D(protocol.payload["starting_equity"])
            check(
                "net_account_result",
                net > D(frozen["minimum_net_pnl_home"]),
                "Dedicated-account NAV change includes broker costs; cash flows must reconcile.",
            )
            observations = valuation.history(
                db, owner, protocol.payload["account_key"], protocol.created_at, now()
            )
            observations.insert(0, (protocol.created_at, D(protocol.payload["starting_equity"])))
            observations.append((now(), D(account.equity)))
            _, _, dd = drawdown(observations)
            check(
                "marked_drawdown",
                D(dd) <= D(frozen["maximum_drawdown_percent"]),
                "Observed marked drawdown stays within the frozen threshold.",
            )
            gaps = [
                (right[0] - left[0]).total_seconds()
                for left, right in zip(observations, observations[1:], strict=False)
            ]
            check(
                "valuation_coverage",
                len(observations) > 2 and max(gaps, default=0) <= 300,
                "Complete account valuations must be observed at least every five minutes throughout the run.",
            )
            check(
                "latency",
                bool(latencies)
                and min(latencies) >= 0
                and max(latencies) <= frozen["maximum_signal_to_fill_seconds"],
                "Actual signal-to-fill latency remains inside the registered signal budget.",
            )
            check(
                "slippage",
                bool(slippage) and max(slippage) <= D(frozen["maximum_adverse_slippage_bps"]),
                "Actual entry fills compared with the captured executable ask.",
            )
            check(
                "risk_observation",
                bool(risks) and all(0 < r <= D(frozen["risk_percent"]) for r in risks),
                "Observed approved stop risk must stay within the frozen ceiling; live promotion cannot exceed the largest observed approved risk.",
            )
            result = {
                "net_pnl_home": str(net),
                "maximum_marked_drawdown_percent": dd,
                "closed_trades": len(closed),
                "max_signal_to_fill_seconds": max(latencies, default=None),
                "max_adverse_slippage_bps": str(max(slippage)) if slippage else None,
                "account_activity": relevant,
                "valuation_observations": len(observations),
            }
    except (BrokerError, KeyError, ValueError, TypeError, ArithmeticError) as error:
        check(
            "broker_evidence",
            False,
            str(error)
            if isinstance(error, BrokerError)
            else "Broker forward evidence could not be fully verified.",
        )
        result = {"closed_trades": len(closed)}
    check(
        "prospective_sample",
        len(closed) >= frozen["minimum_closed_trades"],
        f"At least {frozen['minimum_closed_trades']} complete trades after protocol registration.",
    )
    check(
        "research_qualification",
        bool(protocol.payload["research_report_ids"]),
        "A qualified research report for this exact strategy must predate forward registration.",
    )
    payload = {
        "protocol_checksum": protocol.checksum,
        "strategy_version_id": strategy.id,
        "strategy_checksum": strategy.checksum,
        "provider": frozen["provider"],
        "instrument_id": frozen["instrument_id"],
        "execution_signature": signature(),
        "status": "passed" if all(c["status"] == "passed" for c in criteria) else "incomplete",
        "criteria": criteria,
        "result": result,
        "entries": entries,
        "maximum_risk_percent": str(min(D(frozen["risk_percent"]), max(risks, default=D(0)))),
        "execution_authority": False,
    }
    row = valuation.append(db, owner, "forward_report", body, payload, protocol.id)
    db.commit()
    return record_view(row)


def eligible_report(db, owner, report_id, provider):
    report = owned(db, owner, report_id, "forward_report")
    protocol = owned(db, owner, report.parent_id, "forward_protocol")
    if (
        report.payload["status"] != "passed"
        or report.payload["provider"] != provider
        or report.payload["execution_signature"] != signature()
        or report.payload["protocol_checksum"] != protocol.checksum
    ):
        raise HTTPException(
            409,
            "Live activation requires passing exact-version forward evidence for this broker and current execution model.",
        )
    # Later incomplete evidence supersedes an earlier pass for the same run.
    latest = db.scalar(
        select(TradingReadinessRecord)
        .where(TradingReadinessRecord.parent_id == protocol.id)
        .order_by(TradingReadinessRecord.created_at.desc(), TradingReadinessRecord.id.desc())
        .limit(1)
    )
    if latest.id != report.id or not timedelta(0) <= now() - report.created_at <= timedelta(
        hours=24
    ):
        raise HTTPException(
            409,
            "Re-evaluate forward evidence before live activation; the latest report must be less than 24 hours old.",
        )
    return report


def require_live_strategy(db, owner, policy, strategy_version_id, instrument_id, risk_percent=None):
    if policy.payload.get("environment") != "live":
        return
    report = owned(db, owner, policy.payload["forward_report_id"], "forward_report")
    latest = db.scalar(
        select(TradingReadinessRecord)
        .where(TradingReadinessRecord.parent_id == report.parent_id)
        .order_by(TradingReadinessRecord.created_at.desc(), TradingReadinessRecord.id.desc())
        .limit(1)
    )
    if (
        report.payload["execution_signature"] != signature()
        or report.payload["status"] != "passed"
        or report.payload["strategy_version_id"] != str(strategy_version_id)
        or report.payload["instrument_id"] != instrument_id
        or valuation.checked(latest).payload["status"] != "passed"
    ):
        raise HTTPException(
            409, "Live route does not match its validated strategy, instrument or execution model."
        )
    if risk_percent is not None and D(risk_percent) > D(report.payload["maximum_risk_percent"]):
        raise HTTPException(
            409, "An increased risk budget requires a new Tauric-approved forward protocol."
        )
    return report


def operating_guard(db, owner, policy, intent_id=None):
    if not policy or policy.payload.get("environment") != "live":
        return
    snapshot = db.scalar(
        select(TradingReadinessRecord)
        .where(
            TradingReadinessRecord.owner_id == owner.id, TradingReadinessRecord.kind == "valuation"
        )
        .order_by(TradingReadinessRecord.created_at.desc())
        .limit(1)
    )
    if not snapshot or not timedelta(0) <= now() - snapshot.created_at <= timedelta(seconds=120):
        raise HTTPException(409, "Live entries wait for a recent marked portfolio observation.")
    account = next(
        (
            a
            for a in valuation.checked(snapshot).payload["accounts"]
            if a.get("provider") == policy.provider
            and a.get("account_fingerprint") == policy.payload["account_fingerprint"]
            and a.get("mode") == "live"
            and a.get("connection_revision") == policy.payload["connection_revision"]
        ),
        None,
    )
    if not account or not account["complete"]:
        raise HTTPException(
            409, "Live entries wait for complete current account valuation and currency exposure."
        )
    query = select(BrokerOrderIntent.id).where(
        BrokerOrderIntent.owner_id == owner.id,
        BrokerOrderIntent.provider == policy.provider,
        BrokerOrderIntent.account_fingerprint == policy.payload["account_fingerprint"],
        BrokerOrderIntent.status.in_(
            ["prepared", "submitting", "pending", "partially_filled", "unknown", "cancelling"]
        ),
    )
    if intent_id:
        query = query.where(BrokerOrderIntent.id != intent_id)
    if db.scalar(query.limit(1)):
        raise HTTPException(
            409, "Resolve the outstanding live entry before accepting another signal."
        )
    unresolved_close = db.scalar(
        select(BrokerCloseRequest.id)
        .join(BrokerOrderIntent, BrokerOrderIntent.id == BrokerCloseRequest.entry_intent_id)
        .where(
            BrokerCloseRequest.owner_id == owner.id,
            BrokerOrderIntent.provider == policy.provider,
            BrokerOrderIntent.account_fingerprint == policy.payload["account_fingerprint"],
            BrokerCloseRequest.status.not_in(["filled", "rejected"]),
        )
        .limit(1)
    )
    if unresolved_close:
        raise HTTPException(
            409, "Resolve the close or protection incident before opening another live position."
        )
    for cursor in db.scalars(
        select(BrokerPositionCursor)
        .join(BrokerOrderIntent, BrokerOrderIntent.id == BrokerPositionCursor.entry_intent_id)
        .where(
            BrokerPositionCursor.owner_id == owner.id,
            BrokerOrderIntent.provider == policy.provider,
            BrokerOrderIntent.account_fingerprint == policy.payload["account_fingerprint"],
            BrokerPositionCursor.status.not_in(["closed", "unfilled"]),
        )
    ):
        position = broker_positions.latest(db, db.get(BrokerOrderIntent, cursor.entry_intent_id))
        if (
            not position
            or position.protection_status != "covered"
            or cursor.status == "unknown"
            or not timedelta(0) <= now() - cursor.updated_at <= timedelta(seconds=120)
        ):
            raise HTTPException(
                409,
                "Live entries are paused while a position or its protective stop needs reconciliation.",
            )
    return {
        "valuation_id": snapshot.id,
        "checksum": snapshot.checksum,
        "observed_at": snapshot.created_at.isoformat(),
        "account": account,
    }


def state(db, owner):
    rows = []
    for kind, limit in (("valuation", 1), ("forward_protocol", 100), ("forward_report", 100)):
        rows.extend(
            db.scalars(
                select(TradingReadinessRecord)
                .where(
                    TradingReadinessRecord.owner_id == owner.id, TradingReadinessRecord.kind == kind
                )
                .order_by(TradingReadinessRecord.created_at.desc())
                .limit(limit)
            ).all()
        )
    broker_states = []
    for provider in ("oanda", "alpaca"):
        config, revision, _ = broker_execution.stored_connection(db, provider)
        policy = broker_execution.latest_policy(db, owner, provider)
        enabled = bool(
            policy
            and policy.payload["enabled"]
            and policy.payload.get("environment") == config.environment
            and policy.payload.get("connection_revision") == revision
        )
        blockers = []
        if enabled and config.environment == "live":
            try:
                report = owned(db, owner, policy.payload["forward_report_id"], "forward_report")
                require_live_strategy(
                    db,
                    owner,
                    policy,
                    report.payload["strategy_version_id"],
                    report.payload["instrument_id"],
                )
                operating_guard(db, owner, policy)
            except HTTPException as error:
                blockers.append(str(error.detail))
        broker_states.append(
            {
                "provider": provider,
                "environment": config.environment,
                "connection_revision": revision,
                "enabled": enabled,
                "live_enabled": enabled and config.environment == "live",
                "native_required": True,
                "risk_authority": "Tauric approves the ceiling; OpenClaw may reduce it.",
                "forward_report_id": policy.payload.get("forward_report_id") if policy else None,
                "entry_blockers": blockers,
            }
        )
    return TradingReadinessView(
        valuations=[record_view(r) for r in rows if r.kind == "valuation"][:1],
        forward_protocols=[record_view(r) for r in rows if r.kind == "forward_protocol"],
        forward_reports=[record_view(r) for r in rows if r.kind == "forward_report"],
        brokers=broker_states,
        valuation_worker_ready=worker_ready(db, "valuation"),
        forward_worker_ready=worker_ready(db, "forward_validation"),
        generated_at=now(),
    )


def worker_ready(db, worker_id):
    status = db.get(WorkerStatus, worker_id)
    return bool(status and timedelta(0) <= now() - status.heartbeat_at <= timedelta(seconds=60))


def process_one(db):
    """Prospective evaluations are background observations, never orders."""
    protocols = db.scalars(
        select(TradingReadinessRecord)
        .where(TradingReadinessRecord.kind == "forward_protocol")
        .order_by(TradingReadinessRecord.created_at.desc())
        .limit(100)
    ).all()
    for protocol in protocols:
        valuation.checked(protocol)
        if protocol.payload["execution_signature"] != signature():
            continue
        provider = protocol.payload["request"]["provider"]
        config, revision, _ = broker_execution.stored_connection(db, provider)
        if config.environment == "live" or revision != protocol.payload["connection_revision"]:
            continue
        latest = db.scalar(
            select(TradingReadinessRecord)
            .where(TradingReadinessRecord.parent_id == protocol.id)
            .order_by(TradingReadinessRecord.created_at.desc())
            .limit(1)
        )
        if latest and latest.created_at > now() - timedelta(minutes=15):
            continue
        from uuid import NAMESPACE_URL, uuid5

        from .live_contracts import ReadinessAction

        stamp = now().replace(minute=now().minute // 15 * 15, second=0, microsecond=0).isoformat()
        evaluate(
            db,
            db.get(Owner, protocol.owner_id),
            protocol.id,
            ReadinessAction(
                request_id=uuid5(NAMESPACE_URL, "forward:" + protocol.id + ":" + stamp),
                reason="Automatic prospective broker evidence evaluation",
            ),
        )
        return True
    return False
