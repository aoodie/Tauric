"""Timestamped account valuation, with incomplete marks kept visibly incomplete."""

from datetime import timedelta
from decimal import Decimal, localcontext
from uuid import NAMESPACE_URL, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from . import broker_execution, broker_positions, forex_paper, market, paper
from .broker_adapters import BrokerError
from .live_contracts import ReadinessAction
from .live_models import TradingReadinessRecord
from .models import Owner, PaperAccount, PaperDeployment, TradingRecord, now
from .strategy_execution import snapshot_checked

D = Decimal
ZERO = D(0)
txt = paper.decimal_text


def exposure_groups(positions, currency):
    """Retain instrument and strategy units separately; never sum unlike units."""
    groups = {}
    for item in positions:
        key = item["group"]
        group = groups.setdefault(
            key,
            {
                "key": key,
                "currency": currency,
                "instruments": {},
                "gross_exposure": ZERO,
                "complete": True,
            },
        )
        instrument = item["instrument"]
        group["instruments"][instrument] = group["instruments"].get(instrument, ZERO) + D(
            item["quantity"]
        )
        if item["value"] is None:
            group["complete"] = False
        else:
            group["gross_exposure"] += D(item["value"])
    return [
        {
            **g,
            "instruments": {k: txt(q) for k, q in g["instruments"].items()},
            "gross_exposure": txt(g["gross_exposure"]) if g["complete"] else None,
        }
        for g in groups.values()
    ]


def checked(row):
    if not row or row.checksum != paper.checksum(row.payload):
        raise HTTPException(409, "Trading readiness evidence failed its integrity check.")
    return row


def append(db, owner, kind, request, payload, parent_id=None):
    db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    previous = db.scalar(
        select(TradingReadinessRecord).where(
            TradingReadinessRecord.owner_id == owner.id,
            TradingReadinessRecord.request_id == str(request.request_id),
        )
    )
    body = request.model_dump(mode="json")
    if previous:
        checked(previous)
        if (
            previous.kind != kind
            or previous.parent_id != parent_id
            or previous.payload.get("request") != body
        ):
            raise HTTPException(
                409, "This request identifier belongs to different readiness inputs."
            )
        return previous
    data = {"request": body, **payload}
    row = TradingReadinessRecord(
        owner_id=owner.id,
        kind=kind,
        parent_id=parent_id,
        request_id=str(request.request_id),
        payload=data,
        checksum=paper.checksum(data),
    )
    db.add(row)
    db.flush()
    return row


def paper_account(db, owner, account_id):
    with localcontext() as precision:
        precision.prec = 120
        return _paper_account(db, owner, account_id)


def _paper_account(db, owner, account_id):
    acct = paper.account(db, owner, account_id)
    cash, realized, entries, digest = paper.ledger(db, acct)
    limits, _ = paper.policy(db, acct)
    stamp = now()
    positions, errors, legs, allocations = [], [], {}, []
    value = unrealized = accrued = ZERO
    for entry in entries.values():
        dep = db.get(PaperDeployment, entry.deployment_id)
        spec = market.instrument(db, dep.instrument_id)
        q, paid = D(entry.payload["quantity"]), D(entry.payload["price"])
        item = {
            "entry_id": entry.id,
            "instrument_id": spec.id,
            "strategy_version_id": dep.payload.get("strategy_revision_id"),
            "strategy": dep.payload["strategy"],
            "quantity": txt(q),
            "entry_price": txt(paid),
            "mark": None,
            "market_value": None,
            "unrealized_pnl": None,
            "mark_time": None,
            "mark_status": "missing",
        }
        try:
            mark, source = paper.quote(db, spec.id, limits)
            snapshot_checked(db, source.snapshot_id)
            financing, _ = forex_paper.closing_financing(db, entry, stamp)
            holding = q * mark
            net = q * (mark - paid) - D(entry.payload["fee"]) - financing
            value += holding
            unrealized += net
            accrued += financing
            item.update(
                mark=txt(mark),
                market_value=txt(holding),
                unrealized_pnl=txt(net),
                mark_time=source.provider_time.isoformat(),
                mark_status="fresh",
                snapshot_id=source.snapshot_id,
                snapshot_checksum=source.checksum,
                accrued_financing=txt(financing),
            )
            if spec.asset_type == "fx":
                legs[spec.base_currency] = legs.get(spec.base_currency, ZERO) + q
                legs[spec.quote_currency] = legs.get(spec.quote_currency, ZERO) - holding
        except HTTPException as error:
            item["mark_status"] = "unavailable"
            errors.append({"instrument_id": spec.id, "detail": str(error.detail)})
        positions.append(item)
        allocations.append(
            {
                "group": dep.payload.get("strategy_revision_id") or dep.id,
                "instrument": spec.id,
                "quantity": txt(q),
                "value": item["market_value"],
            }
        )
    fills = db.scalars(
        select(TradingRecord).where(
            TradingRecord.account_id == acct.id, TradingRecord.kind == "fill"
        )
    ).all()
    fees = sum((D(fill.payload["fee"]) for fill in fills), ZERO)
    settled = sum(
        (D(fill.payload.get("financing", "0")) for fill in fills if fill.payload["side"] == "sell"),
        ZERO,
    )
    return {
        "key": "simulated:" + acct.id,
        "account_id": acct.id,
        "name": acct.name,
        "mode": "simulated",
        "currency": acct.currency,
        "cash": txt(cash),
        "equity": txt(cash + value - accrued) if not errors else None,
        "gross_exposure": txt(value) if not errors else None,
        "realized_pnl": txt(realized),
        "unrealized_pnl": txt(unrealized) if not errors else None,
        "fees": txt(fees),
        "settled_financing": txt(settled),
        "accrued_financing": txt(accrued) if not errors else None,
        "positions": positions,
        "instrument_exposure": exposure_groups(
            [{**a, "group": a["instrument"]} for a in allocations], acct.currency
        ),
        "strategy_exposure": exposure_groups(allocations, acct.currency),
        "currency_legs": {key: txt(val) for key, val in legs.items()},
        "complete": not errors,
        "errors": errors,
        "ledger_digest": digest,
        "observed_at": stamp.isoformat(),
        "starting_equity": acct.starting_balance,
        "performance_basis": "Verified fills plus current marks; entry fees and accrued financing included.",
    }


def broker_account(db, owner, provider):
    config, revision, _ = broker_execution.stored_connection(db, provider)
    base = {
        "key": provider + ":" + config.environment + ":" + str(revision),
        "provider": provider,
        "mode": config.environment,
        "connection_revision": revision,
        "positions": [],
        "currency_legs": {},
        "errors": [],
        "complete": False,
        "equity": None,
        "cash": None,
        "gross_exposure": None,
        "fees": None,
        "settled_financing": None,
        "accrued_financing": None,
    }
    try:
        with broker_execution.adapter(provider, config) as broker:
            account, holdings = broker.account(), broker.positions()
            base.update(
                currency=account.currency,
                equity=account.equity,
                cash=account.cash,
                account_fingerprint=account.account_fingerprint,
                observed_at=account.observed_at.isoformat(),
                name=provider.upper() + " " + account.environment,
                unrealized_pnl=txt(sum((D(p.unrealized_pnl) for p in holdings), ZERO)),
                fees=account.fees_home,
                settled_financing=account.financing_home,
                realized_pnl=account.realized_pnl_home,
                performance_basis="Broker NAV includes account costs and external holdings. Unavailable fee breakdowns are not inferred. Deposits and withdrawals require cash-flow reconciliation.",
            )
            attributed, allocations, allocation_values = {}, [], []
            for entry in db.scalars(
                select(broker_execution.BrokerOrderIntent).where(
                    broker_execution.BrokerOrderIntent.owner_id == owner.id,
                    broker_execution.BrokerOrderIntent.provider == provider,
                    broker_execution.BrokerOrderIntent.account_fingerprint
                    == account.account_fingerprint,
                )
            ):
                bound = db.get(broker_execution.BrokerPolicyVersion, entry.policy_version_id)
                if not bound or bound.payload.get("environment") != config.environment:
                    continue
                position = broker_positions.latest(db, entry)
                if (
                    not position
                    and entry.status == "filled"
                    and entry.payload["order"]["position_effect"] == "open"
                ):
                    base["errors"].append(
                        {
                            "instrument_id": entry.payload["order"]["symbol"],
                            "detail": "Filled entry attribution awaits position reconciliation.",
                        }
                    )
                if position and D(position.remaining_quantity):
                    attributed[(position.symbol, position.side)] = attributed.get(
                        (position.symbol, position.side), ZERO
                    ) + D(position.remaining_quantity)
                    allocations.append(
                        {
                            "group": entry.payload.get("strategy_version_id")
                            or "manual:" + entry.id,
                            "instrument": position.symbol,
                            "quantity": position.remaining_quantity,
                        }
                    )
            total, legs = ZERO, {}
            for holding in holdings:
                item = holding.model_dump(mode="json")
                own = attributed.get((holding.symbol, holding.side), ZERO)
                item.update(
                    attributed_quantity=txt(own),
                    external_quantity=txt(max(ZERO, D(holding.quantity) - own)),
                )
                try:
                    quote = broker.quote(holding.symbol)
                    if (
                        not quote.tradable
                        or not 0 <= (now() - quote.observed_at).total_seconds() <= 60
                    ):
                        raise BrokerError(
                            "Position mark is stale; refresh during the broker's trading session."
                        )
                    q = D(holding.quantity)
                    mark = D(quote.bid if holding.side == "long" else quote.ask)
                    value = q * mark
                    factor = (
                        D(1)
                        if provider == "alpaca" or holding.symbol[-3:] == account.currency
                        else D(quote.quote_to_home_position or "NaN")
                    )
                    if not factor.is_finite() or factor <= 0:
                        raise BrokerError("Position currency conversion is missing.")
                    total += value * factor
                    item.update(
                        mark=txt(mark),
                        mark_time=quote.observed_at.isoformat(),
                        mark_status="fresh",
                        market_value_home=txt(value * factor),
                    )
                    if provider == "oanda":
                        sign = D(1) if holding.side == "long" else D(-1)
                        base_ccy, quote_ccy = holding.symbol.split("_")
                        legs[base_ccy] = legs.get(base_ccy, ZERO) + sign * q
                        legs[quote_ccy] = legs.get(quote_ccy, ZERO) - sign * value
                    if own > q:
                        raise BrokerError("Attributed quantities exceed observed broker holdings.")
                except BrokerError as error:
                    item.update(mark_status="unavailable")
                    base["errors"].append({"instrument_id": holding.symbol, "detail": str(error)})
                base["positions"].append(item)
                for allocation in allocations:
                    if allocation["instrument"] == holding.symbol:
                        allocation_values.append(
                            {
                                **allocation,
                                "value": txt(D(allocation["quantity"]) * mark * factor)
                                if item["mark_status"] == "fresh"
                                else None,
                            }
                        )
                if D(item["external_quantity"]):
                    allocation_values.append(
                        {
                            "group": "external",
                            "instrument": holding.symbol,
                            "quantity": item["external_quantity"],
                            "value": txt(D(item["external_quantity"]) * mark * factor)
                            if item["mark_status"] == "fresh"
                            else None,
                        }
                    )
            for symbol, side in attributed:
                if not any(p.symbol == symbol and p.side == side for p in holdings):
                    base["errors"].append(
                        {
                            "instrument_id": symbol,
                            "detail": "Attributed position is absent from observed broker holdings.",
                        }
                    )
            base.update(
                gross_exposure=txt(total) if not base["errors"] else None,
                currency_legs={key: txt(value) for key, value in legs.items()},
                complete=not base["errors"],
                instrument_exposure=exposure_groups(
                    [{**a, "group": a["instrument"]} for a in allocation_values], account.currency
                ),
                strategy_exposure=exposure_groups(allocation_values, account.currency),
            )
    except BrokerError as error:
        base["errors"].append({"provider": provider, "detail": str(error)})
    return base


def history(db, owner, account_key, starting=None, ending=None):
    # A prospective year at one observation/minute fits this explicit bound.
    # Query the registered window, never the first N observations of account life.
    query = select(TradingReadinessRecord).where(
        TradingReadinessRecord.owner_id == owner.id, TradingReadinessRecord.kind == "valuation"
    )
    if starting is not None:
        query = query.where(TradingReadinessRecord.created_at >= starting)
    if ending is not None:
        query = query.where(TradingReadinessRecord.created_at <= ending)
    rows = db.scalars(
        query.order_by(TradingReadinessRecord.created_at)
        .limit(600001)
        .execution_options(yield_per=200)
    )
    result = []
    for count, row in enumerate(rows):
        if count == 600000:
            raise BrokerError("Valuation evidence exceeds the supported observation window.")
        checked(row)
        account = next((a for a in row.payload["accounts"] if a["key"] == account_key), None)
        if account and account["complete"] and account.get("equity") is not None:
            result.append((row.created_at, D(account["equity"])))
    return result


def capture(db, owner, request):
    previous = db.scalar(
        select(TradingReadinessRecord).where(
            TradingReadinessRecord.owner_id == owner.id,
            TradingReadinessRecord.request_id == str(request.request_id),
        )
    )
    if previous:
        return append(db, owner, "valuation", request, {})
    accounts = [
        paper_account(db, owner, key)
        for key in db.scalars(
            select(PaperAccount.id)
            .where(PaperAccount.owner_id == owner.id)
            .order_by(PaperAccount.id)
        )
    ]
    with localcontext() as precision:
        precision.prec = 120
        previous_snapshot = db.scalar(
            select(TradingReadinessRecord)
            .where(
                TradingReadinessRecord.owner_id == owner.id,
                TradingReadinessRecord.kind == "valuation",
            )
            .order_by(TradingReadinessRecord.created_at.desc())
            .limit(1)
        )
        previous_accounts = (
            {a["key"]: a for a in checked(previous_snapshot).payload["accounts"]}
            if previous_snapshot
            else {}
        )
        for provider in ("oanda", "alpaca"):
            config, revision, _ = broker_execution.stored_connection(db, provider)
            configured = (
                bool(config.token.get_secret_value())
                if provider == "oanda"
                else bool(config.key.get_secret_value() and config.secret.get_secret_value())
            )
            if revision and configured:
                accounts.append(broker_account(db, owner, provider))
        totals = {}
        for account in accounts:
            if account.get("currency"):
                key = account["mode"] + ":" + account["currency"]
                group = totals.setdefault(
                    key,
                    {
                        "mode": account["mode"],
                        "currency": account["currency"],
                        "equity": ZERO,
                        "gross_exposure": ZERO,
                        "complete": True,
                    },
                )
                group["complete"] = group["complete"] and account["complete"]
                group["equity"] += D(account.get("equity") or "0")
                group["gross_exposure"] += D(account.get("gross_exposure") or "0")
            prior = previous_accounts.get(account["key"], {})
            # Keep the running peak through missing marks without inventing an observation.
            for field in (
                "marked_peak_equity",
                "marked_max_drawdown_amount",
                "marked_max_drawdown_percent",
                "drawdown_observations",
            ):
                if field in prior:
                    account[field] = prior[field]
            if account["complete"] and account.get("equity") is not None:
                equity = D(account["equity"])
                peak = max(
                    equity,
                    D(
                        account.get("marked_peak_equity")
                        or account.get("starting_equity")
                        or equity
                    ),
                )
                amount = max(D(account.get("marked_max_drawdown_amount", "0")), peak - equity)
                percent = max(
                    D(account.get("marked_max_drawdown_percent", "0")),
                    (peak - equity) / peak * 100 if peak > 0 else ZERO,
                )
                account.update(
                    marked_peak_equity=txt(peak),
                    marked_max_drawdown_amount=txt(amount),
                    marked_max_drawdown_percent=txt(percent),
                    drawdown_observations=account.get("drawdown_observations", 0) + 1,
                    drawdown_basis="Observed valuations only; no marks between observations are invented.",
                )
        for group in totals.values():
            for field in ("equity", "gross_exposure"):
                group[field] = txt(group[field]) if group["complete"] else None
        row = append(
            db,
            owner,
            "valuation",
            request,
            {
                "accounts": accounts,
                "totals": list(totals.values()),
                "observed_at": now().isoformat(),
                "complete": all(a["complete"] for a in accounts),
            },
        )
        db.commit()
        return row


def sample_one(db):
    for owner in db.scalars(select(Owner).order_by(Owner.id)):
        last = db.scalar(
            select(TradingReadinessRecord)
            .where(
                TradingReadinessRecord.owner_id == owner.id,
                TradingReadinessRecord.kind == "valuation",
            )
            .order_by(TradingReadinessRecord.created_at.desc())
            .limit(1)
        )
        if not last or last.created_at < now() - timedelta(seconds=60):
            minute = now().replace(second=0, microsecond=0).isoformat()
            capture(
                db,
                owner,
                ReadinessAction(
                    request_id=uuid5(NAMESPACE_URL, "valuation:" + owner.id + ":" + minute),
                    reason="Automatic marked portfolio observation",
                ),
            )
            return True
    return False
