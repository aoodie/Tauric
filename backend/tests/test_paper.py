from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import paper
from tauric.models import MarketSnapshot, Owner, TradingRecord, now
from tauric.trading_contracts import PaperAction

INSTRUMENT = "binance:spot:BTCUSDT"
P = {
    "risk_percent": "1",
    "max_total_risk_percent": "3",
    "max_exposure_percent": "80",
    "max_positions": 5,
    "fee_bps": "10",
    "slippage_bps": "5",
    "quote_max_age_seconds": 60,
    "decision_ttl_seconds": 300,
}


def action(reason="Paper test action"):
    return {"request_id": str(uuid4()), "reason": reason}


@pytest.fixture
def setup(signed_in, database):
    with Session(database[0]) as db:
        stamp = now()
        start = stamp.replace(second=0, microsecond=0)
        candles = [
            {
                "open_time": (start + timedelta(minutes=k)).isoformat(),
                "close_time": (
                    start + timedelta(minutes=k + 1) - timedelta(milliseconds=1)
                ).isoformat(),
                "open": "100",
                "high": "101",
                "low": "99",
                "close": "100",
                "volume": "3",
                "closed": k < 0,
            }
            for k in [-2, -1, 0]
        ]
        payload = {
            "candles": candles,
            "source": "binance_spot_public",
            "availability": "observed_at_receipt",
            "volume_type": "exchange_base",
        }
        row = MarketSnapshot(
            instrument_id=INSTRUMENT,
            interval="1m",
            received_at=stamp,
            provider_time=stamp,
            checksum=paper.checksum(payload),
            data=payload,
        )
        db.add(row)
        db.commit()
    request = {
        **action(),
        "name": "Paper account",
        "currency": "USDT",
        "starting_balance": "10000",
        "policy": P,
    }
    response = signed_in.post("/api/v1/paper/accounts", json=request)
    assert response.status_code == 201, response.text
    acct = response.json()
    dep_req = {
        **action(),
        "account_id": acct["id"],
        "instrument_id": INSTRUMENT,
        "portfolio": "Test portfolio",
        "capital_pot": "Test capital",
        "strategy": "Test strategy",
        "strategy_version": "v1",
        "quantity_step": "0.0001",
        "capital_limit": "10000",
        "risk_percent": "1",
    }
    dep = signed_in.post("/api/v1/paper/deployments", json=dep_req)
    assert dep.status_code == 201, dep.text
    return acct, dep.json(), request, dep_req


def proposal(client, dep):
    response = client.post(
        "/api/v1/paper/proposals", json={**action(), "deployment_id": dep["id"], "stop_price": "90"}
    )
    assert response.status_code == 201, response.text
    return response.json()


def planned(client, dep, outcome="allow"):
    prop = proposal(client, dep)
    reviewed = client.post(
        f"/api/v1/paper/proposals/{prop['id']}/review", json={**action(), "outcome": outcome}
    )
    assert reviewed.status_code == 201, reviewed.text
    decision = reviewed.json()
    result = client.post(f"/api/v1/paper/decisions/{decision['id']}/plan", json=action())
    return prop, decision, result


def buy(client, dep):
    _, _, result = planned(client, dep)
    assert result.status_code == 201, result.text
    fill = client.post(f"/api/v1/paper/plans/{result.json()['id']}/execute", json=action())
    assert fill.status_code == 201, fill.text
    return fill.json()


def test_execution_is_private_and_csrf_guarded(client):
    assert client.get("/api/v1/paper").status_code == 401
    result = client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert result.status_code == 200
    assert client.post("/api/v1/paper/accounts", json={}).status_code == 403


@pytest.mark.parametrize(
    "invalid", [100, "NaN", "Infinity", "-1", "0", "1e3", " 10 ", "0.0000000000001", "01"]
)
def test_money_requires_bounded_positive_plain_decimal(signed_in, invalid):
    response = signed_in.post(
        "/api/v1/paper/accounts",
        json={
            **action(),
            "name": "Test account",
            "currency": "USDT",
            "starting_balance": invalid,
            "policy": P,
        },
    )
    assert response.status_code == 422


def test_fee_aware_quantity_attribution_close_reconcile(signed_in, setup):
    acct, dep, _, _ = setup
    prop, decision, result = planned(signed_in, dep)
    assert result.status_code == 201, result.text
    plan = result.json()
    p = plan["payload"]
    assert (
        p["estimated_stop_risk"] <= "100.0" if False else Decimal(p["estimated_stop_risk"]) <= 100
    )
    assert Decimal(p["quantity"]) % Decimal("0.0001") == 0
    assert decision["payload"]["source"] == "owner"
    assert p["lineage"]["strategy_version"] == "v1"
    req = action()
    endpoint = f"/api/v1/paper/plans/{plan['id']}/execute"
    response = signed_in.post(endpoint, json=req)
    assert response.status_code == 201, response.text
    fill = response.json()
    assert signed_in.post(endpoint, json=req).json()["id"] == fill["id"]
    assert signed_in.post(endpoint, json=action()).status_code == 409
    state = signed_in.get("/api/v1/paper").json()
    assert len(state["positions"]) == 1 and state["accounts"][0]["reconciled"]
    assert state["live_enabled"] is False
    close_req = action()
    close_endpoint = f"/api/v1/paper/positions/{fill['id']}/close"
    closed = signed_in.post(close_endpoint, json=close_req)
    assert closed.status_code == 201, closed.text
    assert signed_in.post(close_endpoint, json=close_req).json()["id"] == closed.json()["id"]
    assert signed_in.post(close_endpoint, json=action()).status_code == 409
    state = signed_in.get("/api/v1/paper").json()
    assert state["positions"] == []
    view = state["accounts"][0]
    assert Decimal(view["cash"]) == Decimal(view["starting_balance"]) + Decimal(
        view["realized_pnl"]
    )
    assert Decimal(view["realized_pnl"]) < 0
    assert signed_in.get(f"/api/v1/resources/trading_record/{fill['id']}").status_code == 200
    assert prop["payload"]["native_artifact_id"] is None


@pytest.mark.parametrize("outcome", ["reject", "defer"])
def test_decision_without_permission_cannot_plan(signed_in, setup, outcome):
    _, _, response = planned(signed_in, setup[1], outcome)
    assert response.status_code == 409


def test_policy_history_conflict_and_stale_plan(signed_in, setup, database):
    acct, dep, _, _ = setup
    _, _, result = planned(signed_in, dep)
    revised = {**P, "risk_percent": "0.5"}
    endpoint = f"/api/v1/paper/accounts/{acct['id']}/policy"
    change = {"expected_revision": 1, "policy": revised, "reason": "Reduce paper risk"}
    assert signed_in.put(endpoint, json=change).status_code == 200
    assert signed_in.put(endpoint, json=change).status_code == 409
    assert (
        signed_in.post(
            f"/api/v1/paper/plans/{result.json()['id']}/execute", json=action()
        ).status_code
        == 409
    )
    with Session(database[0]) as db:
        versions = list(db.scalars(select(TradingRecord).where(TradingRecord.kind == "policy")))
        assert len(versions) == 2
    with Session(database[0]) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE trading_records SET payload = '{}' WHERE kind = 'policy'"))
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("DELETE FROM paper_accounts"))


def test_halt_blocks_entries_but_owner_can_close(signed_in, setup):
    acct, dep, _, _ = setup
    entry = buy(signed_in, dep)
    assert (
        signed_in.put(
            f"/api/v1/paper/accounts/{acct['id']}/halt",
            json={"expected_revision": 1, "halted": True, "reason": "Pause paper entries"},
        ).status_code
        == 200
    )
    assert (
        signed_in.post(
            "/api/v1/paper/proposals",
            json={**action(), "deployment_id": dep["id"], "stop_price": "90"},
        ).status_code
        == 409
    )
    assert (
        signed_in.post(f"/api/v1/paper/positions/{entry['id']}/close", json=action()).status_code
        == 201
    )


def test_identical_symbol_separate_deployments_keep_attribution(signed_in, setup):
    _, dep, _, request = setup
    second = signed_in.post(
        "/api/v1/paper/deployments",
        json={**request, "request_id": str(uuid4()), "strategy": "Other strategy"},
    ).json()
    first_fill, second_fill = buy(signed_in, dep), buy(signed_in, second)
    state = signed_in.get("/api/v1/paper").json()
    assert {p["strategy"] for p in state["positions"]} == {"Test strategy", "Other strategy"}
    assert (
        signed_in.post(
            f"/api/v1/paper/positions/{first_fill['id']}/close", json=action()
        ).status_code
        == 201
    )
    assert signed_in.get("/api/v1/paper").json()["positions"][0]["id"] == second_fill["id"]


def test_two_concurrent_plans_do_not_overspend(database, signed_in, setup):
    _, dep, _, _ = setup
    _, _, first = planned(signed_in, dep)
    _, _, second = planned(signed_in, dep)
    assert first.status_code == second.status_code == 201
    ids = [first.json()["id"], second.json()["id"]]

    def execute(plan_id):
        from fastapi import HTTPException

        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            try:
                paper.execute(db, owner, plan_id, PaperAction.model_validate(action()))
                return 201
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(execute, ids))
    assert sorted(statuses) == [201, 409]
    assert len(signed_in.get("/api/v1/paper").json()["positions"]) == 1


def test_stale_price_and_expired_decision_fail_closed(signed_in, setup, monkeypatch):
    _, dep, _, _ = setup
    _, _, response = planned(signed_in, dep)
    future = now() + timedelta(seconds=61)
    monkeypatch.setattr(paper, "now", lambda: future)
    assert (
        signed_in.post(
            f"/api/v1/paper/plans/{response.json()['id']}/execute", json=action()
        ).status_code
        == 409
    )


def test_idempotency_inputs_are_bound(signed_in, setup):
    acct, dep, request, dep_req = setup
    assert signed_in.post("/api/v1/paper/accounts", json=request).json()["id"] == acct["id"]
    assert (
        signed_in.post(
            "/api/v1/paper/accounts", json={**request, "starting_balance": "20000"}
        ).status_code
        == 409
    )
    assert signed_in.post("/api/v1/paper/deployments", json=dep_req).json()["id"] == dep["id"]
    assert (
        signed_in.post(
            "/api/v1/paper/deployments", json={**dep_req, "strategy": "Different strategy"}
        ).status_code
        == 409
    )


def test_position_limit_and_strategy_cap_are_applied(signed_in, setup):
    acct, dep, _, _ = setup
    limits = {**P, "max_positions": 1}
    response = signed_in.put(
        f"/api/v1/paper/accounts/{acct['id']}/policy",
        json={"expected_revision": 1, "policy": limits, "reason": "One position for fixture"},
    )
    assert response.status_code == 200
    entry = buy(signed_in, dep)
    assert Decimal(entry["payload"]["quantity"]) * Decimal(entry["payload"]["price"]) <= Decimal(
        dep["capital_limit"]
    )
    _, _, result = planned(signed_in, dep)
    assert result.status_code == 409 and "position" in result.json()["detail"]


def test_missing_price_or_native_report_never_creates_proposal(signed_in, setup, monkeypatch):
    _, dep, _, _ = setup
    invalid = signed_in.post(
        "/api/v1/paper/proposals",
        json={
            **action(),
            "deployment_id": dep["id"],
            "stop_price": "90",
            "native_artifact_id": str(uuid4()),
        },
    )
    assert invalid.status_code == 422
    future = now() + timedelta(seconds=61)
    monkeypatch.setattr(paper, "now", lambda: future)
    result = signed_in.post(
        "/api/v1/paper/proposals", json={**action(), "deployment_id": dep["id"], "stop_price": "90"}
    )
    assert result.status_code == 409
    assert not [
        r for r in signed_in.get("/api/v1/paper").json()["records"] if r["kind"] == "proposal"
    ]


def test_native_original_is_linked_without_modification(signed_in, setup, database):
    import hashlib
    import json

    from tauric.models import AnalysisArtifact, AnalysisJob, identifier

    _, dep, _, _ = setup
    original = {
        "native_rating": "BUY",
        "reports": {"final_trade_decision": "Original report and risk narrative"},
        "governance": None,
        "execution_authority": False,
    }
    digest = hashlib.sha256(
        json.dumps(original, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        job = AnalysisJob(
            id=identifier(),
            owner_id=owner.id,
            request_id=identifier(),
            instrument_id=INSTRUMENT,
            profile="cloud",
            status="succeeded",
            request={},
            progress=[],
        )
        db.add(job)
        db.flush()
        artifact = AnalysisArtifact(
            id=identifier(), job_id=job.id, owner_id=owner.id, payload=original, checksum=digest
        )
        artifact_id = artifact.id
        db.add(artifact)
        db.commit()
    prop = signed_in.post(
        "/api/v1/paper/proposals",
        json={
            **action(),
            "deployment_id": dep["id"],
            "stop_price": "90",
            "native_artifact_id": artifact_id,
        },
    )
    assert prop.status_code == 201, prop.text
    reviewed = signed_in.post(
        f"/api/v1/paper/proposals/{prop.json()['id']}/review", json={**action(), "outcome": "allow"}
    )
    assert reviewed.status_code == 201
    response = signed_in.get(f"/api/v1/analysis-artifacts/{artifact_id}").json()
    assert response["payload"] == original and response["checksum"] == digest


def test_private_openclaw_context_has_read_authority_only(signed_in, setup, tmp_path, monkeypatch):
    import json
    import secrets

    from tauric.config import runtime_config

    token = secrets.token_urlsafe(48)
    secret = tmp_path / "service.json"
    secret.write_text(json.dumps({"platform_token": token}))
    monkeypatch.setenv("TAURIC_OPENCLAW_PRIVATE_FILE", str(secret))
    runtime_config.cache_clear()
    prop = proposal(signed_in, setup[1])
    endpoint = f"/internal/openclaw/proposals/{prop['id']}"
    assert signed_in.get(endpoint).status_code == 401
    assert signed_in.get(endpoint, headers={"Authorization": "Bearer invalid"}).status_code == 401
    response = signed_in.get(endpoint, headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    context = response.json()
    assert context["authority"] == "read_only_paper_context"
    assert context["proposal"]["id"] == prop["id"]
    assert token not in response.text and "encrypted_config" not in response.text
    assert (
        signed_in.post(
            endpoint, headers={"Authorization": "Bearer " + token}, json={"outcome": "allow"}
        ).status_code
        == 405
    )
    assert signed_in.get("/api/v1/paper").json()["openclaw_ready"] is False


def test_ledger_corruption_blocks_planning_and_state(signed_in, setup, database):
    entry = buy(signed_in, setup[1])
    with database[1].begin() as connection:
        # Deliberate administrator tampering verifies application-level integrity;
        # the restricted application role cannot perform either statement.
        connection.execute(text("ALTER TABLE trading_records DISABLE TRIGGER immutable_history"))
        connection.execute(
            text("UPDATE trading_records SET checksum = :digest WHERE id = :id"),
            {"digest": "0" * 64, "id": entry["id"]},
        )
        connection.execute(text("ALTER TABLE trading_records ENABLE TRIGGER immutable_history"))
    assert signed_in.get("/api/v1/paper").status_code == 409
    assert (
        signed_in.post(f"/api/v1/paper/positions/{entry['id']}/close", json=action()).status_code
        == 409
    )


def test_exit_plan_cannot_be_executed_as_an_extra_entry(signed_in, setup):
    entry = buy(signed_in, setup[1])
    closed = signed_in.post(f"/api/v1/paper/positions/{entry['id']}/close", json=action())
    assert closed.status_code == 201
    result = signed_in.post(
        f"/api/v1/paper/plans/{closed.json()['payload']['plan_id']}/execute", json=action()
    )
    assert result.status_code == 409
    assert signed_in.get("/api/v1/paper").json()["positions"] == []
