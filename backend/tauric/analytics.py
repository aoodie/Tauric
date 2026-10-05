"""Rebuild closed-trade outcomes from verified paper ledgers and attribution."""

from decimal import Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import select

from . import paper
from .models import PaperAccount, PaperDeployment, TradingRecord, now
from .outcomes import drawdown, metrics
from .research_contracts import AccountAnalytics, AnalyticsView, StrategyAnalytics

D = Decimal
ZERO = D("0")


def account_view(db, owner, account_id):
    # The same account lock used by fills keeps each projection internally consistent.
    acct = paper.account(db, owner, account_id)
    cash, realized, open_entries, digest = paper.ledger(db, acct)
    rows = list(
        db.scalars(
            select(TradingRecord)
            .where(
                TradingRecord.account_id == acct.id,
                TradingRecord.kind == "fill",
            )
            .order_by(TradingRecord.created_at, TradingRecord.id)
        )
    )
    deployments = {
        r.id: r
        for r in db.scalars(
            select(PaperDeployment).where(
                PaperDeployment.account_id == acct.id, PaperDeployment.owner_id == owner.id
            )
        )
    }
    trades, opened = [], {}
    by_deployment = {key: {"trades": [], "fees": ZERO, "open": 0} for key in deployments}
    points = [(acct.created_at, D(acct.starting_balance))]
    balance, all_fees = D(acct.starting_balance), ZERO
    for row in rows:
        p = row.payload
        if row.deployment_id not in deployments or row.owner_id != owner.id:
            raise HTTPException(409, "Paper fill attribution cannot be verified.")
        if p["instrument_id"] != deployments[row.deployment_id].instrument_id:
            raise HTTPException(409, "Paper fill instrument attribution cannot be verified.")
        group = by_deployment[row.deployment_id]
        fee = D(p["fee"])
        all_fees += fee
        group["fees"] += fee
        if p["side"] == "buy":
            opened[row.id] = row
            continue
        entry = opened.pop(p["position_id"])
        ep = entry.payload
        pnl = D(p["quantity"]) * (D(p["price"]) - D(ep["price"])) - fee - D(ep["fee"])
        if pnl != D(p["realized_pnl"]):
            raise HTTPException(
                409, "Recorded paper profit/loss does not reconcile with its fills."
            )
        dep = deployments[row.deployment_id]
        trade = {
            "entry_fill_id": entry.id,
            "exit_fill_id": row.id,
            "deployment_id": dep.id,
            "strategy": dep.payload["strategy"],
            "strategy_version": dep.payload["strategy_version"],
            "instrument_id": dep.instrument_id,
            "quantity": p["quantity"],
            "entry_price": ep["price"],
            "exit_price": p["price"],
            "fees": paper.decimal_text(D(ep["fee"]) + fee),
            "net_pnl": paper.decimal_text(pnl),
            "entry_time": entry.created_at.isoformat(),
            "exit_time": row.created_at.isoformat(),
            "holding_seconds": (row.created_at - entry.created_at).total_seconds(),
            "native_artifact_id": ep.get("native_artifact_id"),
            "lineage": ep.get("lineage", {}),
        }
        trades.append(trade)
        group["trades"].append(trade)
        balance += pnl
        points.append((row.created_at, balance))
    for entry in open_entries.values():
        by_deployment[entry.deployment_id]["open"] += 1
    if balance != D(acct.starting_balance) + realized:
        raise HTTPException(409, "Paper analytics do not reconcile with the account ledger.")
    curve, amount, percent = drawdown(points)
    strategies = [
        StrategyAnalytics(
            deployment_id=dep.id,
            strategy=dep.payload["strategy"],
            strategy_version=dep.payload["strategy_version"],
            instrument_id=dep.instrument_id,
            open_positions=by_deployment[dep.id]["open"],
            metrics=metrics(by_deployment[dep.id]["trades"], by_deployment[dep.id]["fees"]),
        )
        for dep in deployments.values()
    ]
    return AccountAnalytics(
        account_id=acct.id,
        name=acct.name,
        currency=acct.currency,
        starting_balance=acct.starting_balance,
        cash=paper.decimal_text(cash),
        realized_balance=paper.decimal_text(balance),
        realized_return_percent=paper.decimal_text(realized / D(acct.starting_balance) * 100),
        realized_max_drawdown_percent=percent,
        realized_max_drawdown_amount=amount,
        open_positions=len(open_entries),
        ledger_digest=digest,
        metrics=metrics(trades, all_fees),
        strategies=strategies,
        equity=curve,
        trades=trades,
    )


def view(db, owner, account_id=None):
    accounts = (
        [str(account_id)]
        if account_id
        else list(
            db.scalars(
                select(PaperAccount.id)
                .where(PaperAccount.owner_id == owner.id)
                .order_by(PaperAccount.id)
            )
        )
    )
    with localcontext() as ctx:
        ctx.prec = 120
        result = [account_view(db, owner, key) for key in accounts]
    return AnalyticsView(
        accounts=result,
        generated_at=now(),
        detail="Closed paper trades only. Realized balance/drawdown exclude open positions and their entry fees; cash includes all fills. Total fees include open entry fees. Unrealized returns and intratrade equity drawdown require historical marks and are not inferred. Profit factor is unavailable without a losing trade.",
    )
