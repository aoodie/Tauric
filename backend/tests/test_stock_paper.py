"""Whole-share stock routes on disposable PostgreSQL; explicit AI/feed fixtures."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal, localcontext

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_autonomous import policy
from test_autonomous import process as autonomy_process
from test_capital import allocation
from test_governance import ready as ready
from test_governance import result, run_context
from test_market_adapters import bar, dt, stocks_context
from test_paper import P, action
from test_paper import setup as setup
from test_scanner import body, scan
from test_strategies import pause
from test_strategy_rules import RULES, run
from test_strategy_scheduler import process as monitor
from test_strategy_scheduler import schedule

from tauric import (
    analysis,
    autonomous,
    governance,
    market,
    openclaw_api,
    paper,
    scanner,
    stock_execution,
    strategy_execution,
    strategy_scheduler,
)
from tauric.market_calendars import periods
from tauric.models import AnalysisArtifact, AnalysisJob, GatewayStatus, Owner, TradingRecord

STOCK = "alpaca:equity:AAPL"
COSTS = dict(
    asset_type="equity",
    spread_bps="2",
    commission_per_unit="0.02",
    minimum_commission="1",
    sell_tax_bps="3",
    financing_daily_bps=None,
    triple_rollover_weekday=2,
)


@pytest.fixture
def stock_clock(monkeypatch):
    state = {"now": dt("2026-10-05T10:17") + timedelta(seconds=5), "mode": "enter"}
    from tauric.models import ScannerAnalysis, ScannerCandidate

    for model in (ScannerCandidate, ScannerAnalysis):
        monkeypatch.setattr(
            model.__table__.c.created_at.default, "arg", lambda context: state["now"]
        )
    for module in (
        market,
        paper,
        governance,
        scanner,
        analysis,
        stock_execution,
        strategy_execution,
        strategy_scheduler,
        autonomous,
        openclaw_api,
    ):
        monkeypatch.setattr(module, "now", lambda: state["now"])
    context = stocks_context(
        [("2026-10-02T09:30", "2026-10-02T16:00"), ("2026-10-05T09:30", "2026-10-05T16:00")]
    )

    def collect(spec, interval, **kwargs):
        rows = [
            bar(op, close, "100")
            for op, close in periods(context, interval, dt("2026-10-02T14:00"), state["now"])
        ]
        for row in rows:
            row.update(
                high="103",
                low="90",
                volume="10",
                closed=row["close_time"] <= state["now"].isoformat(),
            )
        closed = [r for r in rows if r["closed"]]
        if interval != "1m":
            closed[-1].update(high="111", close="110", volume="50")
            if state["mode"] == "stop":
                closed[-1].update(high="135", low="85", close="100")
        else:
            rows[-1].update(high="111", close="110")
        return rows[-300:], state["now"], state["now"], context.model_dump(mode="json")

    monkeypatch.setattr(market.PROVIDERS["alpaca_stocks"], "collect", collect)

    # Persisted fills must use the same simulated clock as exit-bar selection.
    record_sequence = 0

    def stamp_record(mapper, connection, record):
        nonlocal record_sequence
        if record.created_at is None:
            # Preserve insertion order when several ledger facts share a fixed clock.
            record_sequence += 1
            record.created_at = state["now"] + timedelta(microseconds=record_sequence)

    event.listen(TradingRecord, "before_insert", stamp_record)
    try:
        yield state
    finally:
        event.remove(TradingRecord, "before_insert", stamp_record)


@pytest.fixture
def stocks(signed_in, database, ready, stock_clock):
    account = signed_in.post(
        "/api/v1/paper/accounts",
        json={
            **action(),
            "name": "Stock cash",
            "currency": "USD",
            "starting_balance": "10000",
            "policy": P,
        },
    )
    assert account.status_code == 201, account.text
    saved = signed_in.post(
        "/api/v1/strategies",
        json={
            **action(),
            "name": "Stock breakout",
            "parameters": {
                "label": "v1",
                "description": "Whole-share stock fixture",
                "quantity_step": "1",
                "capital_limit": "5000",
                "risk_percent": "1",
                "rules": RULES,
            },
        },
    )
    assert saved.status_code == 201, saved.text
    version = saved.json()
    scope, _ = allocation(signed_in, account.json())
    request = {
        **action(),
        "account_id": account.json()["id"],
        "instrument_id": STOCK,
        "portfolio": scope["name"],
        "capital_pot": scope["name"],
        "strategy": "Stock breakout",
        "strategy_version": "v1",
        "strategy_revision_id": version["id"],
        "capital_scope_id": scope["id"],
        "quantity_step": "1",
        "capital_limit": "5000",
        "risk_percent": "1",
        "stock_costs": COSTS,
    }
    response = signed_in.post("/api/v1/paper/deployments", json=request)
    assert response.status_code == 201, response.text
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        req = {
            "mode": "current_daily_research",
            "trade_date": stock_clock["now"].date().isoformat(),
        }
        job = AnalysisJob(
            owner_id=owner.id,
            request_id=action()["request_id"],
            instrument_id=STOCK,
            profile="cloud",
            status="succeeded",
            request=req,
            progress=[],
        )
        db.add(job)
        db.flush()
        original = {
            "request": req,
            "native_rating": "Overweight",
            "execution_authority": False,
            "reports": {
                k: "Explicit synthetic native research fixture"
                for k in (
                    "market_report",
                    "sentiment_report",
                    "news_report",
                    "fundamentals_report",
                    "investment_plan",
                    "trader_investment_plan",
                    "final_trade_decision",
                    "research_debate",
                    "risk_debate",
                )
            },
        }
        artifact = AnalysisArtifact(
            job_id=job.id,
            owner_id=owner.id,
            payload=original,
            checksum=hashlib.sha256(
                json.dumps(original, sort_keys=True, allow_nan=False).encode()
            ).hexdigest(),
        )
        db.add(artifact)
        db.flush()
        artifact_id = artifact.id
        db.get(GatewayStatus, "openclaw").heartbeat_at = stock_clock["now"]
        governance.heartbeat(db)
        market.refresh(db, STOCK, "1m")
    return account.json(), response.json(), version, artifact_id, request


def proposal(client, stocks):
    signal, _ = run(client, stocks[1])
    assert signal["payload"]["action"] == "enter", signal
    response = client.post(
        "/api/v1/paper/proposals",
        json={
            **action(),
            "deployment_id": stocks[1]["id"],
            "strategy_run_id": signal["id"],
            "stop_price": signal["payload"]["stop_price"],
            "native_artifact_id": stocks[3],
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def planned(client, database, ready, stocks):
    prop = proposal(client, stocks)
    response = client.post(
        "/api/v1/governance/jobs", json={**action(), "proposal_id": prop["id"], "profile": "cloud"}
    )
    assert response.status_code == 201, response.text
    claimed, context = run_context(client, database, ready, response.json())
    with Session(database[0]) as db:
        assert governance.finish(db, *claimed, raw=json.dumps(result(context)))
    job = client.get("/api/v1/governance").json()["jobs"][0]
    response = client.post(f"/api/v1/paper/decisions/{job['decision_id']}/plan", json=action())
    assert response.status_code == 201, response.text
    return response.json()


def test_stock_costs_whole_shares_exact_journal_and_duplicate_fill(
    signed_in, database, ready, stocks
):
    plan = planned(signed_in, database, ready, stocks)
    p = plan["payload"]
    with localcontext() as ctx:
        ctx.prec = 120
        q = Decimal(p["quantity"])
        price = Decimal("110") * Decimal("1.0001") * Decimal("1.0005")
        fee = max(Decimal("1"), q * price * Decimal("0.001") + q * Decimal("0.02"))
        assert q == q.to_integral_value() and q > 0
        assert Decimal(p["price"]) == price and Decimal(p["fee"]) == fee
        assert Decimal(p["estimated_stop_risk"]) <= 50
        req = action()
        endpoint = f"/api/v1/paper/plans/{plan['id']}/execute"
        fill = signed_in.post(endpoint, json=req)
        assert fill.status_code == 201, fill.text
        assert signed_in.post(endpoint, json=req).json()["id"] == fill.json()["id"]
        assert signed_in.post(endpoint, json=action()).status_code == 409
        closed = signed_in.post(f"/api/v1/paper/positions/{fill.json()['id']}/close", json=action())
        assert closed.status_code == 201, closed.text
        sell = Decimal("110") * Decimal("0.9999") * Decimal("0.9995")
        exitfee = max(
            Decimal("1"), q * sell * Decimal("0.001") + q * Decimal("0.02")
        ) + q * sell * Decimal("0.0003")
        assert Decimal(closed.json()["payload"]["fee"]) == exitfee
        expected = q * (sell - price) - fee - exitfee
        view = next(
            a
            for a in signed_in.get("/api/v1/paper").json()["accounts"]
            if a["id"] == stocks[0]["id"]
        )
        assert Decimal(view["cash"]) == 10000 + expected
        assert Decimal(view["realized_pnl"]) == expected and view["reconciled"]
        assert closed.json()["payload"]["stock_costs"] == p["stock_costs"]
    original = signed_in.get(f"/api/v1/analysis-artifacts/{stocks[3]}").json()
    assert original["payload"]["native_rating"] == "Overweight"


def test_stock_owner_review_cannot_substitute_for_bound_gateway(signed_in, stocks):
    prop = proposal(signed_in, stocks)
    decision = signed_in.post(
        f"/api/v1/paper/proposals/{prop['id']}/review", json={**action(), "outcome": "allow"}
    )
    assert decision.status_code == 201
    response = signed_in.post(
        f"/api/v1/paper/decisions/{decision.json()['id']}/plan", json=action()
    )
    assert response.status_code == 409 and "bound OpenClaw" in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity_step": "0.1"},
        {"stock_costs": None},
        {"stock_costs": {**COSTS, "asset_type": "etf"}},
        {"fx_financing_daily_bps": "1"},
    ],
)
def test_stock_invalid_execution_assumptions_rejected(signed_in, stocks, changes):
    response = signed_in.post(
        "/api/v1/paper/deployments", json={**stocks[4], **action(), **changes}
    )
    assert response.status_code == 422, response.text


def test_stock_scanner_deduplicates_and_never_queues_ai(signed_in, database, stocks, stock_clock):
    request = body(stocks[2], universe=[STOCK])
    response = signed_in.put("/api/v1/scanners/profiles", json=request)
    assert response.status_code == 200, response.text
    profile = response.json()
    first, _ = scan(signed_in, profile)
    candidate_id = first["results"][0]["candidate_id"]
    assert candidate_id, first
    stock_clock["now"] += timedelta(seconds=31)
    second, _ = scan(signed_in, profile)
    assert second["results"][0]["candidate_id"] == candidate_id
    candidate = signed_in.get("/api/v1/scanners/candidates/" + candidate_id).json()
    assert candidate["asset_class"] == "equity"
    assert candidate["execution_authority"] is False
    assert signed_in.get("/api/v1/governance").json()["jobs"] == []


def test_stock_exit_monitor_survives_pause_and_keeps_autoentries_gated(
    signed_in, database, ready, stocks, stock_clock
):
    plan = planned(signed_in, database, ready, stocks)
    fill = signed_in.post(f"/api/v1/paper/plans/{plan['id']}/execute", json=action())
    assert fill.status_code == 201, fill.text
    dep = stocks[1]
    schedule(signed_in, dep)
    _, request = policy(signed_in, dep, auto_entries=False, auto_exits=True)
    denied = signed_in.put(
        f"/api/v1/strategies/deployments/{dep['id']}/autonomy",
        json={**request, **action(), "expected_revision": 1, "auto_entries": True},
    )
    assert denied.status_code == 422 and "assessment" in denied.text
    assert pause(signed_in, dep).status_code == 200
    stock_clock["now"] += timedelta(minutes=45)
    stock_clock["mode"] = "stop"
    assert monitor(database)
    with Session(database[0]) as db:
        market.refresh(db, STOCK, "1m")
    for _ in range(5):
        autonomy_process(database)
    state = signed_in.get("/api/v1/paper").json()
    assert not [p for p in state["positions"] if p["deployment_id"] == dep["id"]], state["autonomy"]
    closes = [
        r
        for r in state["records"]
        if r["kind"] == "fill" and r["payload"].get("position_id") == fill.json()["id"]
    ]
    assert len(closes) == 1 and closes[0]["payload"]["exit_source"] == "autonomous_rule_close"
    assert closes[0]["payload"]["exit_trigger"] == "stop"
    assert pause(signed_in, dep, enabled=True, revision=1).status_code == 200
    assert monitor(database) is False


def test_forex_autoentries_gated_and_attributed_exits_can_be_enabled(signed_in, stocks):
    response = signed_in.post(
        "/api/v1/paper/deployments",
        json={
            **stocks[4],
            **action(),
            "instrument_id": "oanda:fx:EUR_USD",
            "stock_costs": None,
            "fx_financing_daily_bps": "0",
        },
    )
    assert response.status_code == 201, response.text
    dep = response.json()
    assert dep["fx_financing_daily_bps"] == "0" and dep["stock_costs"] is None
    schedule(signed_in, dep)
    _, req = policy(signed_in, dep, auto_entries=False, auto_exits=True)
    denied = signed_in.put(
        f"/api/v1/strategies/deployments/{dep['id']}/autonomy",
        json={**req, **action(), "expected_revision": 1, "auto_entries": True},
    )
    assert denied.status_code == 422 and "REVIEW status" in denied.text


def test_stock_scanner_escalation_is_daily_research_only_and_reuses_queue(
    signed_in, database, stocks, stock_clock, monkeypatch
):
    from tauric import research_cache
    from tauric.models import ScannerAnalysis, ScannerCandidate

    monkeypatch.setattr(research_cache, "now", lambda: stock_clock["now"])
    for model in (ScannerCandidate, ScannerAnalysis):
        monkeypatch.setattr(
            model.__table__.c.created_at.default, "arg", lambda context: stock_clock["now"]
        )
    response = signed_in.put("/api/v1/scanners/profiles", json=body(stocks[2], universe=[STOCK]))
    assert response.status_code == 200, response.text
    profile = response.json()
    scanned, _ = scan(signed_in, profile)
    candidate_id = scanned["results"][0]["candidate_id"]
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    request = {**action(), "profile_version_id": profile["id"]}
    endpoint = f"/api/v1/scanners/candidates/{candidate_id}/analysis"
    first = signed_in.post(endpoint, json=request)
    assert first.status_code == 200, first.text
    second = signed_in.post(endpoint, json={**request, **action()})
    assert second.status_code == 200 and second.json()["id"] == first.json()["id"], second.text
    with Session(database[0]) as db:
        job = db.get(AnalysisJob, first.json()["id"])
        assert job.request["mode"] == "current_daily_research"
        assert job.request["scanner_context"]["features_consumed_by_native_graph"] is False
    state = signed_in.get("/api/v1/scanners").json()
    assert state["profiles"][0]["charged_analyses_today"] == 1
    assert not signed_in.get("/api/v1/paper").json()["positions"]
