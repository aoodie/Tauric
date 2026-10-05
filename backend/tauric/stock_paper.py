"""Exact stock costs and monotone cash/risk sizing for unlevered paper fills."""

from decimal import ROUND_DOWN, Decimal, localcontext

from . import asset_costs, capital
from .models import GovernanceJob, PaperDeployment
from .research_contracts import AssetCosts

D = Decimal
ZERO = D(0)


def costs(dep):
    value = dep.payload.get("stock_costs")
    return AssetCosts.model_validate(value) if value else None


def price(dep, observed, side, limits):
    return asset_costs.execution_price(
        observed, side, None, "close", costs(dep), D(limits.slippage_bps) / 10000
    )


def fee(dep, quantity, execution_price, side, limits):
    return asset_costs.commission(
        quantity, execution_price, side, D(limits.fee_bps) / 10000, costs(dep)
    )


def stop_risk(dep, quantity, entry, stop, limits):
    stop_fill = price(dep, stop, "sell", limits)
    return (
        quantity * (entry - stop_fill)
        + fee(dep, quantity, entry, "buy", limits)
        + fee(dep, quantity, stop_fill, "sell", limits)
    )


def sizing(db, acct, dep, prop, limits, cash, entries, observed):
    from . import paper

    with localcontext() as ctx:
        ctx.prec = 120
        entry, stop = price(dep, observed, "buy", limits), D(prop.payload["stop_price"])
        if stop >= observed:
            paper.fail(
                "The observed market price crossed the stop reference. Create a new proposal."
            )
        if len(entries) >= limits.max_positions:
            paper.fail("The account’s open-position limit is reached.")
        nodes, ancestry = capital.assigned(db, acct, dep)
        totals = {
            key: {"reserved": ZERO, "risk": ZERO, "positions": 0, "direct": ZERO} for key in nodes
        }
        exposure = open_risk = deployment_exposure = ZERO
        marks = {}
        for held in entries.values():
            route = db.get(PaperDeployment, held.deployment_id)
            if route.instrument_id not in marks:
                marks[route.instrument_id] = paper.quote(db, route.instrument_id, limits)[0]
            mark = marks[route.instrument_id]
            if mark <= D(held.payload["stop_price"]):
                paper.fail(
                    "An open paper position crossed its stop reference. Close it before adding entries."
                )
            q = D(held.payload["quantity"])
            liquidation, stop_fill = (
                price(route, mark, "sell", limits),
                price(route, D(held.payload["stop_price"]), "sell", limits),
            )
            risk = q * max(ZERO, liquidation - stop_fill) + fee(
                route, q, liquidation, "sell", limits
            )
            reserved = q * max(D(held.payload["price"]), mark) + D(held.payload["fee"])
            exposure += q * mark
            open_risk += risk
            if held.deployment_id == dep.id:
                deployment_exposure += reserved
            route_scope = route.payload.get("capital_scope_id")
            if nodes and not route_scope:
                paper.fail("Close unallocated legacy positions before using shared capital.")
            for index, scope in enumerate(capital.chain(nodes, route_scope) if route_scope else []):
                total = totals[scope.scope_id]
                total["reserved"] += reserved
                total["risk"] += risk
                total["positions"] += 1
                if index == 0:
                    total["direct"] += reserved
        equity = cash + exposure
        strategy_capital = D(dep.payload["capital_limit"])
        risk_budget = min(
            equity * D(limits.risk_percent) / 100,
            strategy_capital * D(dep.payload["risk_percent"]) / 100,
            equity * D(limits.max_total_risk_percent) / 100 - open_risk,
        )
        capacity = min(
            cash / entry,
            (equity * D(limits.max_exposure_percent) / 100 - exposure) / entry,
            (strategy_capital - deployment_exposure) / entry,
        )
        allocations = []
        shared_available = []
        for index, scope in enumerate(ancestry):
            total = totals[scope.scope_id]
            policy = scope.payload["limits"]
            if total["positions"] >= policy["max_positions"]:
                paper.fail(f"The shared open-position limit for {scope.name} is reached.")
            available = D(policy["budget"]) - total["reserved"]
            if index == 0:
                available = min(
                    available,
                    D(policy["budget"])
                    - capital.child_budget(nodes, scope.scope_id)
                    - total["direct"],
                )
            available_risk = (
                D(policy["budget"]) * D(policy["max_total_risk_percent"]) / 100 - total["risk"]
            )
            capacity = min(capacity, available / entry)
            risk_budget = min(risk_budget, available_risk)
            shared_available.append(available)
            allocations.append(
                {
                    "scope_id": scope.scope_id,
                    "version_id": scope.id,
                    "revision": scope.revision,
                    "name": scope.name,
                    "limits": policy,
                    "reserved": paper.decimal_text(total["reserved"]),
                    "available_capital": paper.decimal_text(available),
                    "open_risk": paper.decimal_text(total["risk"]),
                    "available_risk": paper.decimal_text(available_risk),
                    "open_positions": total["positions"],
                }
            )
        step = D(dep.payload["quantity_step"])
        upper = max(0, int((capacity / step).to_integral_value(rounding=ROUND_DOWN)))

        def fits(units):
            quantity = step * units
            spend = quantity * entry + fee(dep, quantity, entry, "buy", limits)
            return (
                spend <= cash
                and spend <= strategy_capital - deployment_exposure
                and all(spend <= v for v in shared_available)
                and stop_risk(dep, quantity, entry, stop, limits) <= risk_budget
            )

        # Minimum commissions are not linear per-share fees. Find the largest
        # affordable step count using actual entry and stressed stop-close costs.
        lower = 0
        while lower < upper:
            middle = (lower + upper + 1) // 2
            if fits(middle):
                lower = middle
            else:
                upper = middle - 1
        quantity = step * lower
        if quantity <= 0:
            paper.fail(
                "Risk, commission, cash or shared capital limits leave no tradable paper quantity."
            )
        return (
            entry,
            stop,
            stop_risk(dep, quantity, entry, stop, limits) / quantity,
            quantity,
            allocations,
        )


def bound_review(db, decision, proposal):
    from . import paper

    job = (
        db.get(GovernanceJob, decision.payload.get("job_id"))
        if decision.payload.get("job_id")
        else None
    )
    if (
        decision.payload.get("source") != "openclaw"
        or not job
        or job.owner_id != decision.owner_id
        or job.proposal_id != proposal.id
        or job.status != "succeeded"
        or job.decision_id != decision.id
    ):
        paper.fail("A stock paper entry requires its actual bound OpenClaw review.")
