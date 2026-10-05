"""Closed-bar signals and real PostgreSQL paper lineage on disposable accounts."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_capital import allocation
from test_paper import action
from test_paper import setup as setup
from test_strategies import PARAMS, deploy, pause

from tauric import market, paper, strategy_execution, strategy_rules
from tauric.models import MarketSnapshot, Owner, TradingRecord, now
from tauric.strategy_contracts import BreakoutRules
from tauric.trading_contracts import ProposalCreate

RULES = {
    "engine": "closed_bar_breakout_v1",
    "interval": "15m",
    "entry_lookback": 3,
    "minimum_volume_ratio": "2",
    "exit_lookback": 2,
    "take_profit_r": "2",
    "signal_ttl_seconds": 300,
}


@pytest.fixture
def rule_feed(monkeypatch):
    state = {"now": now(), "mode": "enter", "volume": "50", "calls": 0}
    for module in (market, paper, strategy_execution, strategy_rules):
        monkeypatch.setattr(module, "now", lambda: state["now"])

    def collect(spec, interval):
        state["calls"] += 1
        duration = market.INTERVAL_SECONDS[interval]
        boundary = state["now"].replace(second=0, microsecond=0)
        boundary -= timedelta(seconds=int(boundary.timestamp()) % duration)
        bars = []
        for offset in range(-8, 1):
            start = boundary + timedelta(seconds=offset * duration)
            values = {"open": "100", "high": "103", "low": "90", "close": "101", "volume": "10"}
            if offset == -1:
                values.update(high="111", low="95", close="110", volume=state["volume"])
                if state["mode"] == "stop":
                    values.update(open="100", high="135", low="85", close="100")
                if state["mode"] == "target":
                    values.update(open="100", high="135", low="95", close="125")
                if state["mode"] == "hold":
                    values.update(high="103", low="99", close="101")
            if offset == 0:
                values.update(high="1000", close="999", volume="1000")
            bars.append(
                {
                    "open_time": start.isoformat(),
                    "close_time": (
                        start + timedelta(seconds=duration, milliseconds=-1)
                    ).isoformat(),
                    **values,
                    "closed": offset < 0,
                }
            )
        return bars, state["now"], state["now"]

    monkeypatch.setattr(market.PROVIDERS["binance_spot_public"], "collect", collect)
    return state


def configured(client, setup, **changes):
    req = {
        **action(),
        "name": "Executable breakout",
        "parameters": {**PARAMS, "rules": {**RULES, **changes}},
    }
    saved = client.post("/api/v1/strategies", json=req)
    assert saved.status_code == 201, saved.text
    scope, _ = allocation(client, setup[0])
    dep = deploy(client, setup[3], saved.json(), scope)
    assert dep.status_code == 201, dep.text
    return dep.json(), saved.json()


def run(client, dep):
    req = {**action(), "deployment_id": dep["id"]}
    response = client.post("/api/v1/strategies/runs", json=req)
    assert response.status_code == 201, response.text
    return response.json(), req


def propose(client, dep, signal, **changes):
    req = {
        **action(),
        "deployment_id": dep["id"],
        "stop_price": signal["payload"].get("stop_price", "90"),
        "strategy_run_id": signal["id"],
        **changes,
    }
    return client.post("/api/v1/paper/proposals", json=req), req


def filled(client, dep, signal):
    prop, _ = propose(client, dep, signal)
    assert prop.status_code == 201, prop.text
    decision = client.post(
        f"/api/v1/paper/proposals/{prop.json()['id']}/review", json={**action(), "outcome": "allow"}
    )
    assert decision.status_code == 201, decision.text
    plan = client.post(f"/api/v1/paper/decisions/{decision.json()['id']}/plan", json=action())
    assert plan.status_code == 201, plan.text
    fill = client.post(f"/api/v1/paper/plans/{plan.json()['id']}/execute", json=action())
    assert fill.status_code == 201, fill.text
    return fill.json()


def test_rules_routes_auth_and_csrf(client):
    assert client.post("/api/v1/strategies/runs", json={}).status_code == 401
    assert client.post("/api/v1/strategies/runs/missing/close", json={}).status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert client.post("/api/v1/strategies/runs", json={}).status_code == 403


@pytest.mark.parametrize(
    "changes",
    [
        {"engine": "arbitrary_python"},
        {"entry_lookback": 1},
        {"entry_lookback": 2.5},
        {"interval": "30s"},
        {"take_profit_r": "0.5"},
        {"minimum_volume_ratio": 2},
        {"signal_ttl_seconds": 0},
        {"unexpected_rule": True},
    ],
)
def test_rule_inputs_are_bounded(signed_in, changes):
    response = signed_in.post(
        "/api/v1/strategies",
        json={
            **action(),
            "name": "Invalid rules",
            "parameters": {**PARAMS, "rules": {**RULES, **changes}},
        },
    )
    assert response.status_code == 422


def test_closed_bar_signal_is_saved_idempotent_and_immutable(signed_in, setup, rule_feed, database):
    dep, version = configured(signed_in, setup)
    signal, req = run(signed_in, dep)
    assert signal["action"] == "enter"
    assert signal["payload"]["stop_price"] == "90"
    assert signal["payload"]["volume_ratio"] == "5.00000000"
    assert signal["payload"]["bars"][-1]["close"] == "110"
    assert signal["strategy_version_id"] == version["id"]
    assert signed_in.post("/api/v1/strategies/runs", json=req).json()["id"] == signal["id"]
    assert (
        signed_in.post(
            "/api/v1/strategies/runs", json={**req, "reason": "Changed request"}
        ).status_code
        == 409
    )
    assert signed_in.get(f"/api/v1/resources/strategy_run/{signal['id']}").status_code == 200
    assert signed_in.get("/api/v1/paper").json()["strategy_runs"][0]["id"] == signal["id"]
    for statement in (
        "DELETE FROM strategy_runs",
        "UPDATE strategy_runs SET action='exit'",
        "TRUNCATE strategy_runs",
    ):
        with database[0].begin() as db, pytest.raises(DBAPIError):
            db.execute(text(statement))


def test_entry_uses_governance_sizing_and_provenance(signed_in, setup, rule_feed):
    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)
    missing, _ = propose(signed_in, dep, signal, strategy_run_id=None)
    assert missing.status_code == 422
    altered, _ = propose(signed_in, dep, signal, stop_price="89")
    assert altered.status_code == 422
    fill = filled(signed_in, dep, signal)
    assert fill["payload"]["lineage"]["strategy_run_id"] == signal["id"]
    assert fill["payload"]["lineage"]["strategy_run_checksum"] == signal["checksum"]
    assert Decimal(fill["payload"]["estimated_stop_risk"]) <= 100
    assert len(signed_in.get("/api/v1/paper").json()["positions"]) == 1
    assert propose(signed_in, dep, signal)[0].status_code == 409
    next_run, _ = run(signed_in, dep)
    assert next_run["action"] == "hold"


def test_same_bar_cannot_create_duplicate_proposals(signed_in, setup, rule_feed):
    dep, _ = configured(signed_in, setup)
    a, _ = run(signed_in, dep)
    b, _ = run(signed_in, dep)
    response, req = propose(signed_in, dep, a)
    assert response.status_code == 201
    assert signed_in.post("/api/v1/paper/proposals", json=req).json()["id"] == response.json()["id"]
    assert propose(signed_in, dep, b)[0].status_code == 409


def test_signal_expiry_and_closed_evidence_change_block_entries(
    signed_in, setup, rule_feed, database
):
    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)
    rule_feed["now"] += timedelta(seconds=301)
    assert propose(signed_in, dep, signal)[0].status_code == 409
    signal, _ = run(signed_in, dep)
    with Session(database[0]) as db:
        old = db.get(MarketSnapshot, signal["payload"]["snapshot_id"])
        changed = json.loads(json.dumps(old.data))
        changed["candles"][-2]["volume"] = "1"
        db.add(
            MarketSnapshot(
                instrument_id=old.instrument_id,
                interval=old.interval,
                received_at=old.received_at + timedelta(milliseconds=1),
                provider_time=old.provider_time,
                data=changed,
                checksum=hashlib.sha256(json.dumps(changed, sort_keys=True).encode()).hexdigest(),
            )
        )
        db.commit()
    assert propose(signed_in, dep, signal)[0].status_code == 409


def test_pause_invalidates_entry_even_after_resume(signed_in, setup, rule_feed):
    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)
    assert pause(signed_in, dep).status_code == 200
    assert run(signed_in, dep)[0]["action"] == "blocked"
    assert pause(signed_in, dep, True, 1).status_code == 200
    assert propose(signed_in, dep, signal)[0].status_code == 409


@pytest.mark.parametrize("trigger", ["stop", "target"])
def test_rule_exit_is_attributed_and_available_while_halted_or_paused(
    signed_in, setup, rule_feed, trigger
):
    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)
    fill = filled(signed_in, dep, signal)
    assert pause(signed_in, dep).status_code == 200
    acct = signed_in.get("/api/v1/paper").json()["accounts"][0]
    assert (
        signed_in.put(
            f"/api/v1/paper/accounts/{acct['id']}/halt",
            json={
                "expected_revision": acct["revision"],
                "halted": True,
                "reason": "Test halt with existing position",
            },
        ).status_code
        == 200
    )
    rule_feed["now"] += timedelta(minutes=45)
    rule_feed["mode"] = trigger
    signal, _ = run(signed_in, dep)
    assert signal["action"] == "exit"
    assert signal["payload"]["exit_trigger"] == ("take_profit" if trigger == "target" else "stop")
    req = action()
    endpoint = f"/api/v1/strategies/runs/{signal['id']}/close"
    with_exit = signed_in.post(endpoint, json=req)
    assert with_exit.status_code == 409  # An exit still needs a current execution observation.
    assert (
        signed_in.post(
            f"/api/v1/market/{dep['instrument_id']}/refresh", json={"interval": "1m"}
        ).status_code
        == 200
    )
    with_exit = signed_in.post(endpoint, json=req)
    assert with_exit.status_code == 201, with_exit.text
    assert with_exit.json()["payload"]["position_id"] == fill["id"]
    assert with_exit.json()["payload"]["exit_source"] == "owner_rule_close"
    assert with_exit.json()["payload"]["strategy_exit_run_id"] == signal["id"]
    assert signed_in.post(endpoint, json=req).json()["id"] == with_exit.json()["id"]
    assert signed_in.post(endpoint, json=action()).status_code == 409
    state = signed_in.get("/api/v1/paper").json()
    assert state["positions"] == [] and state["accounts"][0]["reconciled"]
    # Current observed price, not the stop/target level, determines the paper fill.
    assert with_exit.json()["payload"]["observed_price"] == "999"


def test_forming_candle_and_zero_volume_do_not_generate_entry(signed_in, setup, rule_feed):
    dep, _ = configured(signed_in, setup)
    rule_feed["mode"] = "hold"
    assert run(signed_in, dep)[0]["action"] == "hold"
    rule_feed["now"] += timedelta(minutes=15)
    rule_feed["mode"] = "enter"
    rule_feed["volume"] = "0"
    assert run(signed_in, dep)[0]["action"] == "hold"


def test_channel_exit_uses_exact_decimal_comparison(signed_in, setup, rule_feed, database):
    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)
    fill = filled(signed_in, dep, signal)
    rule_feed["now"] += timedelta(minutes=45)
    with Session(database[0]) as db:
        feed = market.refresh(db, dep["instrument_id"], "15m")
        entry = db.get(TradingRecord, fill["id"])
        # The exit channel (99) is above the immutable initial stop (90).
        for bar in feed.candles[:-1]:
            bar.low = "99"
        feed.candles[-2].low = "95"
        feed.candles[-2].close = "98"
        feed.candles[-2].open = "100"
        feed.candles[-2].high = "103"
        result = strategy_rules.evaluate(feed, BreakoutRules(**RULES), entry)
        assert result["action"] == "exit" and result["exit_trigger"] == "channel"


def test_concurrent_entry_proposals_consume_signal_once(signed_in, setup, rule_feed, database):
    dep, _ = configured(signed_in, setup)
    signal, _ = run(signed_in, dep)

    def propose_once(_):
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            body = ProposalCreate(
                **action(), deployment_id=dep["id"], stop_price="90", strategy_run_id=signal["id"]
            )
            try:
                return paper.propose(db, owner, body).id
            except HTTPException as error:
                assert error.status_code == 409
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(v is not None for v in pool.map(propose_once, range(2))) == 1


def test_future_closed_candle_and_missing_data_block_rules(signed_in, setup, rule_feed, database):
    dep, _ = configured(signed_in, setup)
    with Session(database[0]) as db:
        feed = market.refresh(db, dep["instrument_id"], "15m")
        feed.candles[-1].closed = True
        assert strategy_rules.evaluate(feed, BreakoutRules(**RULES))["action"] == "blocked"
        feed.candles = []
        assert strategy_rules.evaluate(feed, BreakoutRules(**RULES))["action"] == "blocked"
        feed.state = "stale"
        assert strategy_rules.evaluate(feed, BreakoutRules(**RULES))["action"] == "blocked"


def test_new_version_keeps_deployed_rules_pinned(signed_in, setup, rule_feed):
    dep, version = configured(signed_in, setup)
    revised = signed_in.post(
        f"/api/v1/strategies/{version['strategy_id']}/versions",
        json={
            **action(),
            "expected_revision": 1,
            "parameters": {
                **PARAMS,
                "label": "v2",
                "rules": {**RULES, "minimum_volume_ratio": "10"},
            },
        },
    )
    assert revised.status_code == 201
    signal, _ = run(signed_in, dep)
    assert signal["action"] == "enter" and signal["payload"]["rules"]["minimum_volume_ratio"] == "2"
