"""Shared-budget behavior against disposable PostgreSQL and paper market fixtures."""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal as D
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_paper import action, buy, planned, proposal
from test_paper import setup as setup

from tauric import capital, paper
from tauric.capital_contracts import CapitalCreate
from tauric.models import CapitalScopeVersion, Owner, PaperAccount


def allocation(
    client, acct, name="Test portfolio", budget="10000", parent=None, risk="100", positions=10
):
    request = {
        **action(),
        "account_id": acct["id"],
        "kind": "pot" if parent else "portfolio",
        "parent_id": parent["id"] if parent else None,
        "name": name,
        "limits": {"budget": budget, "max_total_risk_percent": risk, "max_positions": positions},
    }
    response = client.post("/api/v1/paper/capital", json=request)
    assert response.status_code == 201, response.text
    return response.json(), request


def strategy(client, dep_req, scope, root=None, **changes):
    request = {
        **dep_req,
        **action(),
        "portfolio": (root or scope)["name"],
        "capital_pot": scope["name"],
        "capital_scope_id": scope["id"],
        "capital_limit": scope["limits"]["budget"],
        **changes,
    }
    response = client.post("/api/v1/paper/deployments", json=request)
    assert response.status_code == 201, response.text
    return response.json()


def change(client, scope, **limits):
    return client.put(
        f"/api/v1/paper/capital/{scope['id']}",
        json={
            "expected_revision": scope["revision"],
            "limits": {**scope["limits"], **limits},
            "reason": "Adjust allocation test",
        },
    )


def test_private_and_strict_hierarchy(signed_in, setup):
    acct = setup[0]
    for kind, parent in [("pot", None), ("portfolio", str(uuid4()))]:
        result = signed_in.post(
            "/api/v1/paper/capital",
            json={
                **action(),
                "account_id": acct["id"],
                "kind": kind,
                "parent_id": parent,
                "name": "Invalid scope",
                "limits": {"budget": "500", "max_total_risk_percent": "1", "max_positions": 1},
            },
        )
        assert result.status_code == 422
    root, _ = allocation(signed_in, acct)
    assert change(signed_in, root, budget=1.2).status_code == 422
    assert change(signed_in, root, max_total_risk_percent="101").status_code == 422
    assert change(signed_in, root, max_positions=True).status_code == 422
    assert signed_in.get(f"/api/v1/paper/capital/{uuid4()}/history").status_code == 404


def test_idempotency_history_conflict_and_immutable_sql(signed_in, setup, database):
    scope, request = allocation(signed_in, setup[0])
    assert signed_in.post("/api/v1/paper/capital", json=request).json()["id"] == scope["id"]
    assert (
        signed_in.post("/api/v1/paper/capital", json={**request, "name": "Other name"}).status_code
        == 409
    )
    changed = change(signed_in, scope, budget="9000")
    assert changed.status_code == 200
    assert change(signed_in, scope, budget="8000").status_code == 409
    history = signed_in.get(f"/api/v1/paper/capital/{scope['id']}/history").json()
    assert [v["revision"] for v in history] == [2, 1]
    assert history[1]["payload"]["limits"]["budget"] == "10000"
    assert signed_in.get(f"/api/v1/resources/capital_scope/{scope['id']}").status_code == 200
    for sql in [
        "UPDATE capital_scope_versions SET checksum = 'changed'",
        "DELETE FROM capital_scope_versions",
        "TRUNCATE capital_scope_versions",
    ]:
        with database[0].begin() as db, pytest.raises(DBAPIError):
            db.execute(text(sql))


def test_children_and_roots_cannot_overallocate(signed_in, setup):
    root, _ = allocation(signed_in, setup[0], budget="5000")
    child, _ = allocation(signed_in, setup[0], "First child", "3000", root)
    other, req = allocation(signed_in, setup[0], "Other portfolio", "5000")
    assert change(signed_in, root, budget="5001").status_code == 422
    assert change(signed_in, root, budget="2999").status_code == 422
    req.update(action())
    req.update(
        kind="pot",
        parent_id=root["id"],
        name="Too large child",
        limits={**child["limits"], "budget": "2001"},
    )
    assert signed_in.post("/api/v1/paper/capital", json=req).status_code == 422
    assert change(signed_in, child, budget="5000").status_code == 200
    assert change(signed_in, other, budget="5001").status_code == 422


def test_cross_account_parent_and_deployment_are_blocked(signed_in, setup):
    acct, _, acct_req, dep_req = setup
    root, _ = allocation(signed_in, acct)
    other = signed_in.post(
        "/api/v1/paper/accounts", json={**acct_req, **action(), "name": "Other account"}
    ).json()
    req = {
        **action(),
        "account_id": other["id"],
        "kind": "pot",
        "parent_id": root["id"],
        "name": "Cross account",
        "limits": root["limits"],
    }
    assert signed_in.post("/api/v1/paper/capital", json=req).status_code == 409
    req = {**dep_req, **action(), "account_id": other["id"], "capital_scope_id": root["id"]}
    assert signed_in.post("/api/v1/paper/deployments", json=req).status_code == 409


def test_legacy_labels_preserved_but_no_new_entries_after_allocation(signed_in, setup):
    acct, old, _, req = setup
    allocation(signed_in, acct)
    assert signed_in.get("/api/v1/paper").json()["deployments"][0]["portfolio"] == old["portfolio"]
    assert (
        signed_in.post(
            "/api/v1/paper/proposals",
            json={**action(), "deployment_id": old["id"], "stop_price": "90"},
        ).status_code
        == 409
    )
    assert signed_in.post("/api/v1/paper/deployments", json={**req, **action()}).status_code == 422


def test_open_legacy_position_must_close_before_first_allocation(signed_in, setup):
    acct, dep, _, _ = setup
    fill = buy(signed_in, dep)
    req = {
        **action(),
        "account_id": acct["id"],
        "kind": "portfolio",
        "name": "New capital",
        "limits": {"budget": "10000", "max_total_risk_percent": "1", "max_positions": 5},
    }
    assert signed_in.post("/api/v1/paper/capital", json=req).status_code == 409
    assert (
        signed_in.post(f"/api/v1/paper/positions/{fill['id']}/close", json=action()).status_code
        == 201
    )
    assert signed_in.post("/api/v1/paper/capital", json=req).status_code == 201


def test_sibling_and_nested_shared_capital_includes_fees_and_releases(signed_in, setup):
    acct, _, _, req = setup
    root, _ = allocation(signed_in, acct, budget="1000")
    pot, _ = allocation(signed_in, acct, "Shared pot", "500", root)
    deep, _ = allocation(signed_in, acct, "Nested pot", "500", pot)
    first = strategy(signed_in, req, deep, root, risk_percent="100")
    second = strategy(signed_in, req, deep, root, risk_percent="100", strategy="Second strategy")
    fill = buy(signed_in, first)
    committed = D(fill["payload"]["quantity"]) * D(fill["payload"]["price"]) + D(
        fill["payload"]["fee"]
    )
    assert D("499") < committed <= 500
    assert len(fill["payload"]["capital_allocations"]) == 3
    _, _, result = planned(signed_in, second)
    assert result.status_code == 409
    assert change(signed_in, deep, budget="100").status_code == 422
    scopes = signed_in.get("/api/v1/paper").json()["capital_scopes"]
    assert all(v["open_positions"] == 1 for v in scopes)
    exit = signed_in.post(f"/api/v1/paper/positions/{fill['id']}/close", json=action()).json()
    assert exit["payload"]["lineage"] == fill["payload"]["lineage"]
    assert all(
        v["open_cost"] == "0" for v in signed_in.get("/api/v1/paper").json()["capital_scopes"]
    )
    assert planned(signed_in, second)[2].status_code == 201


def test_parent_direct_trades_leave_child_budgets_reserved(signed_in, setup):
    acct, _, _, req = setup
    root, _ = allocation(signed_in, acct, budget="1000")
    allocation(signed_in, acct, "Reserved child", "800", root)
    dep = strategy(signed_in, req, root, risk_percent="100")
    fill = buy(signed_in, dep)
    p = fill["payload"]
    assert D("199") < D(p["quantity"]) * D(p["price"]) + D(p["fee"]) <= 200
    child_req = {
        **action(),
        "account_id": acct["id"],
        "kind": "pot",
        "parent_id": root["id"],
        "name": "No free budget",
        "limits": {"budget": "1", "max_total_risk_percent": "1", "max_positions": 1},
    }
    assert signed_in.post("/api/v1/paper/capital", json=child_req).status_code == 422


def test_ancestor_shared_risk_across_sibling_pots(signed_in, setup):
    acct, _, _, req = setup
    root, _ = allocation(signed_in, acct, budget="2000", risk="0.5")
    left, _ = allocation(signed_in, acct, "Left child", "1000", root)
    right, _ = allocation(signed_in, acct, "Right child", "1000", root)
    first = strategy(signed_in, req, left, root, risk_percent="100")
    second = strategy(signed_in, req, right, root, risk_percent="100")
    fill = buy(signed_in, first)
    assert D(fill["payload"]["estimated_stop_risk"]) <= 10
    _, _, result = planned(signed_in, second)
    assert result.status_code == 201, result.text
    p = result.json()["payload"]
    ancestor = p["capital_allocations"][-1]
    assert D(p["estimated_stop_risk"]) + D(ancestor["open_risk"]) <= 10
    assert D(p["estimated_stop_risk"]) < 1


def test_ancestor_position_limit_survives_revision_and_close(signed_in, setup):
    acct, _, _, req = setup
    root, _ = allocation(signed_in, acct, positions=1)
    child, _ = allocation(signed_in, acct, "Child allocation", "5000", root)
    dep = strategy(signed_in, req, child, root)
    fill = buy(signed_in, dep)
    assert planned(signed_in, dep)[2].status_code == 409
    assert change(signed_in, root, max_positions=2).status_code == 200
    assert planned(signed_in, dep)[2].status_code == 201
    assert (
        signed_in.post(f"/api/v1/paper/positions/{fill['id']}/close", json=action()).status_code
        == 201
    )


def test_changes_invalidate_proposals_decisions_and_plans(signed_in, setup):
    acct, _, _, req = setup
    root, _ = allocation(signed_in, acct)
    dep = strategy(signed_in, req, root)
    prop = proposal(signed_in, dep)
    _, decision, plan = planned(signed_in, dep)
    assert plan.status_code == 201
    assert change(signed_in, root, budget="9000").status_code == 200
    assert (
        signed_in.post(
            f"/api/v1/paper/proposals/{prop['id']}/review", json={**action(), "outcome": "allow"}
        ).status_code
        == 409
    )
    assert (
        signed_in.post(f"/api/v1/paper/decisions/{decision['id']}/plan", json=action()).status_code
        == 409
    )
    assert (
        signed_in.post(
            f"/api/v1/paper/plans/{plan.json()['id']}/execute", json=action()
        ).status_code
        == 409
    )
    assert planned(signed_in, dep)[2].status_code == 201


def test_concurrent_sibling_creation_cannot_oversubscribe(signed_in, setup, database):
    acct, _, _, _ = setup
    root, _ = allocation(signed_in, acct, budget="1000")

    def create_child(index):
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            body = CapitalCreate.model_validate(
                {
                    **action(),
                    "account_id": acct["id"],
                    "parent_id": root["id"],
                    "kind": "pot",
                    "name": f"Concurrent child {index}",
                    "limits": {"budget": "600", "max_total_risk_percent": "1", "max_positions": 1},
                }
            )
            try:
                return capital.save(db, owner, body).id
            except Exception as err:
                assert getattr(err, "status_code", None) == 422
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create_child, range(2)))
    assert sum(v is not None for v in results) == 1
    with Session(database[0]) as db:
        acct_row = db.get(PaperAccount, acct["id"])
        assert len(capital.current(db, acct_row)) == 2


def test_sql_cannot_reparent_or_skip_revision(signed_in, setup, database):
    scope, _ = allocation(signed_in, setup[0])
    with Session(database[0]) as db:
        initial = db.get(CapitalScopeVersion, scope["id"])
        values = {
            key: getattr(initial, key)
            for key in (
                "scope_id",
                "owner_id",
                "account_id",
                "parent_id",
                "kind",
                "name",
                "payload",
                "checksum",
            )
        }
    for changes in [
        {"revision": 3},
        {"revision": 2, "name": "Changed identity"},
        {"revision": 2, "parent_id": scope["id"], "kind": "pot"},
    ]:
        with Session(database[0]) as db, pytest.raises(DBAPIError):
            db.add(
                CapitalScopeVersion(
                    id=str(uuid4()), request_id=str(uuid4()), **{**values, **changes}
                )
            )
            db.commit()


def test_concurrent_shared_plans_cannot_double_reserve(database, signed_in, setup):
    from fastapi import HTTPException

    from tauric.trading_contracts import PaperAction

    root, _ = allocation(signed_in, setup[0], budget="500")
    dep = strategy(signed_in, setup[3], root, risk_percent="100")
    ids = [planned(signed_in, dep)[2].json()["id"] for _ in range(2)]

    def execute(plan_id):
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            try:
                paper.execute(db, owner, plan_id, PaperAction.model_validate(action()))
                return 201
            except HTTPException as err:
                return err.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(execute, ids)) == [201, 409]
    state = signed_in.get("/api/v1/paper").json()
    assert len(state["positions"]) == 1
    assert D(state["capital_scopes"][0]["open_cost"]) <= 500


def test_held_instrument_marks_checked_again_before_fill(signed_in, setup, database, monkeypatch):
    from tauric.models import MarketSnapshot

    acct, _, _, req = setup
    root, _ = allocation(signed_in, acct, budget="1000")
    with Session(database[0]) as db:
        original = db.scalar(select(MarketSnapshot))
        db.add(
            MarketSnapshot(
                instrument_id="binance:spot:ETHUSDT",
                interval="1m",
                received_at=original.received_at,
                provider_time=original.provider_time,
                data=original.data,
                checksum=original.checksum,
            )
        )
        db.commit()
    held_dep = strategy(
        signed_in, req, root, instrument_id="binance:spot:ETHUSDT", capital_limit="500"
    )
    dep = strategy(signed_in, req, root, strategy="New BTC strategy", capital_limit="500")
    buy(signed_in, held_dep)
    _, _, response = planned(signed_in, dep)
    assert response.status_code == 201
    original_quote = paper.quote

    def moved_mark(db, instrument_id, limits, **kwargs):
        price, snapshot = original_quote(db, instrument_id, limits, **kwargs)
        return (D("1000") if instrument_id.endswith("ETHUSDT") else price), snapshot

    monkeypatch.setattr(paper, "quote", moved_mark)
    result = signed_in.post(f"/api/v1/paper/plans/{response.json()['id']}/execute", json=action())
    assert result.status_code == 409
    assert len(signed_in.get("/api/v1/paper").json()["positions"]) == 1
