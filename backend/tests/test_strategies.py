"""Version pinning, explicit account routing and pause safety on disposable data."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_capital import allocation
from test_paper import action, buy, planned, proposal
from test_paper import setup as setup

from tauric import strategies
from tauric.models import Owner
from tauric.strategy_contracts import StrategyRevise

PARAMS = {
    "label": "v1",
    "description": "Owner notes for explicit daily native research and manual long spot proposals.",
    "quantity_step": "0.0001",
    "capital_limit": "5000",
    "risk_percent": "1",
}


def registered(client):
    request = {**action(), "name": "Registered strategy", "parameters": PARAMS}
    response = client.post("/api/v1/strategies", json=request)
    assert response.status_code == 201, response.text
    return response.json(), request


def deploy(client, req, saved, scope, **changes):
    p = saved["parameters"]
    response = client.post(
        "/api/v1/paper/deployments",
        json={
            **req,
            **action(),
            "account_id": scope["account_id"],
            "portfolio": scope["name"],
            "capital_pot": scope["name"],
            "capital_scope_id": scope["id"],
            "strategy_revision_id": saved["id"],
            "strategy": saved["name"],
            "strategy_version": p["label"],
            "quantity_step": p["quantity_step"],
            "capital_limit": p["capital_limit"],
            "risk_percent": p["risk_percent"],
            **changes,
        },
    )
    return response


def pause(client, dep, enabled=False, revision=0):
    return client.put(
        f"/api/v1/paper/deployments/{dep['id']}/control",
        json={
            "expected_revision": revision,
            "enabled": enabled,
            "reason": "Owner test deployment change",
        },
    )


def test_registry_is_private(client):
    assert client.get("/api/v1/strategies").status_code == 401
    assert client.post("/api/v1/strategies", json={}).status_code == 401


def test_versions_are_idempotent_conflicted_and_immutable(signed_in, setup, database):
    first, req = registered(signed_in)
    assert signed_in.post("/api/v1/strategies", json=req).json()["id"] == first["id"]
    assert (
        signed_in.post("/api/v1/strategies", json={**req, "name": "Changed name"}).status_code
        == 409
    )
    body = {
        **action(),
        "expected_revision": 1,
        "parameters": {**PARAMS, "label": "v2", "risk_percent": "0.5"},
        "reason": "Prepare a lower risk version",
    }
    endpoint = f"/api/v1/strategies/{first['strategy_id']}/versions"
    second = signed_in.post(endpoint, json=body)
    assert second.status_code == 201, second.text
    assert signed_in.post(endpoint, json=body).json()["id"] == second.json()["id"]
    assert signed_in.post(endpoint, json={**body, **action()}).status_code == 409
    history = signed_in.get(endpoint).json()
    assert [v["revision"] for v in history] == [2, 1]
    assert history[1]["parameters"] == PARAMS
    assert signed_in.get(f"/api/v1/resources/strategy_version/{first['id']}").status_code == 200
    for table in ["strategy_versions", "deployment_control_versions"]:
        with database[0].begin() as db, pytest.raises(DBAPIError):
            db.execute(text(f"DELETE FROM {table}"))


def test_new_version_does_not_change_existing_deployment_or_fill(signed_in, setup):
    first, _ = registered(signed_in)
    root, _ = allocation(signed_in, setup[0])
    dep = deploy(signed_in, setup[3], first, root).json()
    fill = buy(signed_in, dep)
    assert fill["payload"]["lineage"]["strategy_revision_id"] == first["id"]
    body = {
        **action(),
        "expected_revision": 1,
        "parameters": {**PARAMS, "label": "v2", "risk_percent": "0.5"},
    }
    second = signed_in.post(f"/api/v1/strategies/{first['strategy_id']}/versions", json=body).json()
    state = signed_in.get("/api/v1/paper").json()
    old = next(v for v in state["deployments"] if v["id"] == dep["id"])
    assert old["risk_percent"] == "1" and old["strategy_revision_id"] == first["id"]
    new = deploy(signed_in, setup[3], second, root)
    assert new.status_code == 201
    assert new.json()["risk_percent"] == "0.5"
    exit = signed_in.post(f"/api/v1/paper/positions/{fill['id']}/close", json=action()).json()
    assert exit["payload"]["lineage"] == fill["payload"]["lineage"]


def test_tampered_version_parameters_and_live_modes_cannot_deploy(signed_in, setup):
    saved, _ = registered(signed_in)
    root, _ = allocation(signed_in, setup[0])
    assert deploy(signed_in, setup[3], saved, root, risk_percent="90").status_code == 422
    assert deploy(signed_in, setup[3], saved, root, capital_scope_id=None).status_code == 422
    assert deploy(signed_in, setup[3], saved, root, mode="live").status_code == 422
    for value in [1.0, "NaN", "0"]:
        req = {
            **action(),
            "name": "Invalid strategy",
            "parameters": {**PARAMS, "risk_percent": value},
        }
        assert signed_in.post("/api/v1/strategies", json=req).status_code == 422


def test_same_version_routes_to_two_accounts_with_independent_ledgers(signed_in, setup):
    saved, _ = registered(signed_in)
    root, _ = allocation(signed_in, setup[0])
    other = signed_in.post(
        "/api/v1/paper/accounts", json={**setup[2], **action(), "name": "Second paper account"}
    ).json()
    other_root, _ = allocation(signed_in, other, "Other portfolio")
    first = deploy(signed_in, setup[3], saved, root).json()
    second = deploy(signed_in, setup[3], saved, other_root).json()
    a, b = buy(signed_in, first), buy(signed_in, second)
    assert a["account_id"] != b["account_id"]
    assert (
        a["payload"]["lineage"]["strategy_revision_id"]
        == b["payload"]["lineage"]["strategy_revision_id"]
    )
    state = signed_in.get("/api/v1/paper").json()
    assert len(state["positions"]) == 2 and all(v["reconciled"] for v in state["accounts"])


def test_pause_blocks_stale_work_and_resume_needs_fresh_proposal(signed_in, setup):
    dep = setup[1]
    prop = proposal(signed_in, dep)
    _, _, plan = planned(signed_in, dep)
    assert pause(signed_in, dep).status_code == 200
    assert pause(signed_in, dep).status_code == 409
    assert (
        signed_in.post(
            "/api/v1/paper/proposals",
            json={**action(), "deployment_id": dep["id"], "stop_price": "90"},
        ).status_code
        == 409
    )
    assert (
        signed_in.post(
            f"/api/v1/paper/plans/{plan.json()['id']}/execute", json=action()
        ).status_code
        == 409
    )
    assert pause(signed_in, dep, True, 1).status_code == 200
    assert (
        signed_in.post(
            f"/api/v1/paper/proposals/{prop['id']}/review", json={**action(), "outcome": "allow"}
        ).status_code
        == 409
    )
    assert planned(signed_in, dep)[2].status_code == 201
    state = signed_in.get("/api/v1/paper").json()["deployments"][0]
    assert state["control_revision"] == 2 and state["enabled"]
    history = signed_in.get(f"/api/v1/resources/paper_deployment/{dep['id']}").json()["data"][
        "control_history"
    ]
    assert [v["payload"]["enabled"] for v in history] == [True, False]


def test_pause_preserves_existing_positions_and_owner_close(signed_in, setup):
    dep = setup[1]
    fill = buy(signed_in, dep)
    assert pause(signed_in, dep).status_code == 200
    assert signed_in.get("/api/v1/paper").json()["positions"][0]["id"] == fill["id"]
    assert (
        signed_in.post(f"/api/v1/paper/positions/{fill['id']}/close", json=action()).status_code
        == 201
    )


def test_concurrent_version_revisions_cannot_silently_overwrite(signed_in, setup, database):
    saved, _ = registered(signed_in)

    def revise(index):
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            body = StrategyRevise.model_validate(
                {
                    **action(),
                    "expected_revision": 1,
                    "parameters": {**PARAMS, "label": f"v2 candidate {index}"},
                }
            )
            try:
                return strategies.save(db, owner, body, saved["strategy_id"]).id
            except HTTPException as e:
                assert e.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(v is not None for v in pool.map(revise, range(2))) == 1
