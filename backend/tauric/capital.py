"""Fixed capital allocations, account-serialized history and shared paper limits.

Children reserve their budget from their parent. Direct trades can use only the
parent's unallocated budget. Every descendant also consumes all ancestor limits.
Legacy labels remain readable; once an account is allocated they cannot open trades.
"""

from decimal import Decimal as D
from decimal import localcontext

from sqlalchemy import select

from . import paper, services
from .capital_contracts import CapitalLimits, CapitalVersionView, CapitalView
from .models import CapitalScopeVersion, Owner, PaperDeployment, identifier

ZERO = D("0")


def current(db, acct):
    rows = db.scalars(
        select(CapitalScopeVersion)
        .where(CapitalScopeVersion.account_id == acct.id)
        .order_by(CapitalScopeVersion.revision)
    )
    result = {}
    for row in rows:
        if row.owner_id != acct.owner_id or paper.checksum(row.payload) != row.checksum:
            paper.fail("Capital history integrity failed. New entries are blocked.")
        CapitalLimits.model_validate(row.payload["limits"])
        result[row.scope_id] = row
    return result


def chain(nodes, scope_id):
    result, seen = [], set()
    while scope_id:
        if scope_id in seen or scope_id not in nodes or len(result) >= 16:
            paper.fail("Capital ancestry cannot be verified.")
        seen.add(scope_id)
        row = nodes[scope_id]
        result.append(row)
        scope_id = row.parent_id
    return result


def digest(nodes):
    return paper.checksum({key: row.checksum for key, row in sorted(nodes.items())})


def assigned(db, acct, dep):
    nodes = current(db, acct)
    scope_id = dep.payload.get("capital_scope_id")
    if not scope_id and nodes:
        paper.fail(
            "This legacy strategy has no shared allocation. Create an assigned strategy deployment before opening new trades."
        )
    return nodes, chain(nodes, scope_id) if scope_id else []


def validate_proposal(db, acct, dep, prop):
    nodes, ancestry = assigned(db, acct, dep)
    captured = prop.payload.get("capital_digest", digest({}))
    if captured != digest(nodes):
        paper.fail("Capital allocations changed. Create a new proposal.")
    return nodes, ancestry


def child_budget(nodes, scope_id):
    return sum(
        (D(r.payload["limits"]["budget"]) for r in nodes.values() if r.parent_id == scope_id), ZERO
    )


def usage(db, nodes, entries, marks=None, fee_rate=ZERO, slip=ZERO):
    totals = {
        key: {"cost": ZERO, "reserved": ZERO, "risk": ZERO, "positions": 0, "direct": ZERO}
        for key in nodes
    }
    for held in entries.values():
        dep = db.get(PaperDeployment, held.deployment_id)
        scope_id = dep.payload.get("capital_scope_id")
        if not scope_id:
            if nodes:
                paper.fail("Close unallocated legacy positions before using shared capital.")
            continue
        ancestry = chain(nodes, scope_id)
        q, entry, fee = (D(held.payload[k]) for k in ("quantity", "price", "fee"))
        mark = marks[dep.instrument_id] if marks is not None else entry
        cost = q * entry + fee
        reserved = q * max(entry, mark) + fee
        risk = (
            q * max(ZERO, mark - D(held.payload["stop_price"]) * (1 - slip)) + q * mark * fee_rate
        )
        for index, row in enumerate(ancestry):
            total = totals[row.scope_id]
            total["cost"] += cost
            total["reserved"] += reserved
            total["risk"] += risk
            total["positions"] += 1
            if index == 0:
                total["direct"] += reserved
    return totals


def view(row, nodes, totals):
    return CapitalView(
        id=row.scope_id,
        version_id=row.id,
        account_id=row.account_id,
        parent_id=row.parent_id,
        kind=row.kind,
        name=row.name,
        revision=row.revision,
        limits=row.payload["limits"],
        allocated_to_children=paper.decimal_text(child_budget(nodes, row.scope_id)),
        open_cost=paper.decimal_text(totals[row.scope_id]["cost"]),
        open_positions=totals[row.scope_id]["positions"],
        reason=row.payload["reason"],
        checksum=row.checksum,
        created_at=row.created_at,
    )


def views(db, acct):
    with localcontext() as ctx:
        ctx.prec = 120
        nodes = current(db, acct)
        totals = usage(db, nodes, paper.ledger(db, acct)[2])
        return [view(row, nodes, totals) for row in nodes.values()]


def scope(db, owner, scope_id):
    row = db.get(CapitalScopeVersion, str(scope_id))
    if not row or row.owner_id != owner.id or row.revision != 1:
        paper.fail("This capital allocation does not exist.", 404)
    return row


def history(db, owner, scope_id):
    scope(db, owner, scope_id)
    rows = db.scalars(
        select(CapitalScopeVersion)
        .where(CapitalScopeVersion.scope_id == str(scope_id))
        .order_by(CapitalScopeVersion.revision.desc())
    )
    result = []
    for row in rows:
        if paper.checksum(row.payload) != row.checksum:
            paper.fail("Capital history integrity failed.")
        result.append(CapitalVersionView.model_validate(row))
    return result


def save(db, owner, body, scope_id=None):
    if not scope_id:
        db.scalar(select(Owner).where(Owner.id == owner.id).with_for_update())
    initial = scope(db, owner, scope_id) if scope_id else None
    acct = paper.account(db, owner, initial.account_id if initial else body.account_id)
    request = body.model_dump(mode="json")
    nodes = current(db, acct)
    old = nodes.get(initial.scope_id) if initial else None
    if old and old.revision != body.expected_revision:
        paper.fail("This allocation changed in another window. Reload before saving.")
    if not initial:
        existing = db.scalar(
            select(CapitalScopeVersion).where(
                CapitalScopeVersion.owner_id == owner.id,
                CapitalScopeVersion.request_id == str(body.request_id),
            )
        )
        if existing:
            if existing.payload["request"] != request:
                paper.fail(
                    "This request identifier was already used for different allocation inputs."
                )
            return next(v for v in views(db, acct) if v.id == existing.scope_id)
        if body.parent_id:
            ancestry = chain(nodes, str(body.parent_id))
            if len(ancestry) >= 16:
                paper.fail("This allocation exceeds the supported nesting depth.", 422)
    new_id = identifier()
    row = CapitalScopeVersion(
        id=new_id,
        scope_id=old.scope_id if old else new_id,
        owner_id=owner.id,
        account_id=acct.id,
        parent_id=old.parent_id if old else str(body.parent_id) if body.parent_id else None,
        kind=old.kind if old else body.kind,
        name=old.name if old else body.name.strip(),
        revision=old.revision + 1 if old else 1,
        request_id=identifier() if old else str(body.request_id),
        payload={"limits": body.limits.model_dump(), "reason": body.reason, "request": request},
    )
    row.checksum = paper.checksum(row.payload)
    candidate = {**nodes, row.scope_id: row}
    with localcontext() as ctx:
        ctx.prec = 120
        totals = usage(db, candidate, paper.ledger(db, acct)[2])
        root_total = sum(
            (D(r.payload["limits"]["budget"]) for r in candidate.values() if r.parent_id is None),
            ZERO,
        )
        if root_total > D(acct.starting_balance):
            paper.fail("Portfolio budgets exceed the account’s fixed starting capital.", 422)
        for key, item in candidate.items():
            if child_budget(candidate, key) + totals[key]["direct"] > D(
                item.payload["limits"]["budget"]
            ):
                paper.fail(
                    "Child allocations and direct open-position costs exceed a parent budget. Close positions or adjust allocations first.",
                    422,
                )
    db.add(row)
    acct.revision += 1
    db.flush()
    services.audit(
        db,
        owner,
        "paper.capital",
        "capital_scope",
        row.scope_id,
        body.reason,
        {"version_id": row.id, "revision": row.revision, "account_id": acct.id},
    )
    db.commit()
    return next(v for v in views(db, acct) if v.id == row.scope_id)


def constrain(db, nodes, ancestry, entries, marks, fee_rate, slip, entry, risk_budget, capacity):
    totals = usage(db, nodes, entries, marks, fee_rate, slip)
    evidence = []
    for index, row in enumerate(ancestry):
        limits = row.payload["limits"]
        total = totals[row.scope_id]
        if total["positions"] >= limits["max_positions"]:
            paper.fail(f"The shared open-position limit for {row.name} is reached.")
        available = D(limits["budget"]) - total["reserved"]
        if index == 0:
            available = min(
                available, D(limits["budget"]) - child_budget(nodes, row.scope_id) - total["direct"]
            )
        available_risk = (
            D(limits["budget"]) * D(limits["max_total_risk_percent"]) / 100 - total["risk"]
        )
        capacity = min(capacity, available / (entry * (1 + fee_rate)))
        risk_budget = min(risk_budget, available_risk)
        evidence.append(
            {
                "scope_id": row.scope_id,
                "version_id": row.id,
                "revision": row.revision,
                "name": row.name,
                "limits": limits,
                "reserved": paper.decimal_text(total["reserved"]),
                "available_capital": paper.decimal_text(available),
                "open_risk": paper.decimal_text(total["risk"]),
                "available_risk": paper.decimal_text(available_risk),
                "open_positions": total["positions"],
            }
        )
    return risk_budget, capacity, evidence
