"""Qualification boundaries on disposable PostgreSQL, with no provider calls."""

import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_paper import setup as setup
from test_portfolio_chat import HEADERS, active_turn, preferences
from test_portfolio_chat import portfolios_ready as portfolios_ready
from test_research import bars, import_request, run_request
from test_strategy_replay import register

from tauric import analysis, chat, native_strategy_assessment, qualification
from tauric.models import AnalysisJob, ChatTurn, TradingRecord, now
from tauric.native_runner import artifact
from tauric.qualification_models import QualificationAdmission, QualificationProtocol

pytest_plugins = ["test_chat"]
BASE = "/api/v1/qualification/protocols"


def action():
    return {"request_id": str(uuid4()), "reason": "Verify exact strategy research evidence"}


def evidence():
    result = bars((10, 10, 13, 14, 16, 18, 18, 10, 10, 13, 14, 16, 18, 18))
    for index in (2, 9):
        result[index]["volume"] = "30"
    return result


def prepare(client, scope, **changes):
    saved = register(client, risk_percent="1", quantity_step="0.00001")
    response = client.post("/api/v1/research/datasets/import", json=import_request(evidence()))
    assert response.status_code == 201, response.text
    body = {
        **action(),
        "portfolio_id": scope["id"],
        "strategy_version_id": saved["id"],
        "dataset_id": response.json()["id"],
        "name": "Frozen breakout qualification",
        "hypothesis": "Synthetic verification of split and cost assumptions; no profitability claim",
        "training_bars": 7,
        "embargo_bars": 0,
        "parameters": {
            "engine": "closed_bar_breakout_v1",
            "strategy_version_id": saved["id"],
            "starting_balance": "1000",
            "allocation_percent": "50",
            "risk_percent": "1",
            "quantity_step": "0.00001",
            "fee_bps": "10",
            "slippage_bps": "5",
        },
        "thresholds": {
            "minimum_holdout_bars": 7,
            "minimum_closed_trades": 1,
            "minimum_net_return_percent": "0",
            "maximum_drawdown_percent": "10",
        },
        "stresses": [{"name": "Higher costs", "fee_bps": "30", "slippage_bps": "20"}],
        "intended_mode": "research_comparison",
        "assessment_validity_hours": 24,
        **changes,
    }
    return body


def frozen(client, scope, **changes):
    body = prepare(client, scope, **changes)
    response = client.post(BASE, json=body)
    assert response.status_code == 201, response.text
    return response.json(), body


def evaluated(client, scope, **changes):
    protocol, body = frozen(client, scope, **changes)
    response = client.post(f"{BASE}/{protocol['id']}/evaluate", json=action())
    assert response.status_code == 201, response.text
    return response.json(), body


def admitted(client, database, protocol):
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    body = {**action(), "profile": "local"}
    response = client.post(f"{BASE}/{protocol['id']}/assessment", json=body)
    assert response.status_code == 202, response.text
    return response.json(), body


def finish(database, protocol, *, decision="suitable", changes=None, omit=False):
    job_id = protocol["assessment_jobs"][0]["job"]["id"]
    with Session(database[0]) as db:
        assert analysis.claim(db) == job_id
        job = db.get(AnalysisJob, job_id)
        qualification.validate_admission(db, job)
        payload = artifact(
            {
                "market_report": "Original daily fixture market report",
                "final_trade_decision": "Original daily fixture decision",
            },
            "BUY",
            job.request,
            job.request["settings"],
            "0.5.2",
        )
        if not omit:
            payload["strategy_assessment"] = {
                "strategy_version_id": protocol["strategy_version_id"],
                "evidence_checksum": job.request["qualification_context"]["evidence_checksum"],
                "intended_mode": protocol["frozen"]["request"]["intended_mode"],
                "decision": decision,
                "rationale": "Offline exact-version fixture review",
                "limitations": ["Synthetic test, not connected model evidence"],
                "adapter_version": qualification.ADAPTER_VERSION,
                "source": "native_tauric_strategy_review",
                "execution_authority": False,
                **(changes or {}),
            }
        assert analysis.finish(db, job_id, payload=payload)
    return payload


def reported(client, protocol):
    response = client.post(f"{BASE}/{protocol['id']}/reports", json=action())
    assert response.status_code == 201, response.text
    return response.json()


def test_frozen_windows_costs_exact_assessment_and_immutable_reports(
    ready, database, portfolios_ready
):
    protocol, body = evaluated(ready, portfolios_ready[0])
    e = protocol["evaluation"]
    assert e["training"]["bars"] == e["holdout"]["bars"] == 7
    assert e["training"]["fills"][0]["time"] != e["holdout"]["fills"][0]["time"]
    assert e["registered_holdout_unseen"] and e["prior_owner_knowledge"] == "not_verifiable"
    assert protocol["frozen"]["portfolio_preferences"]["mandate"]
    assert protocol["frozen"]["dataset_context"]["quote_currency"] == "USDT"
    assert float(e["stresses"][0]["result"]["metrics"]["net_pnl"]) < float(
        e["holdout"]["metrics"]["net_pnl"]
    )
    incomplete = reported(ready, protocol)
    assert incomplete["reports"][0]["status"] == "incomplete"
    protocol, admission = admitted(ready, database, protocol)
    assert ready.post(f"{BASE}/{protocol['id']}/assessment", json=admission).json() == protocol
    original = finish(database, protocol)
    result = reported(ready, protocol)
    assert result["reports"][0]["status"] == "passed", result["reports"][0]["criteria"]
    assert result["reports"][1] == incomplete["reports"][0]
    assert result["reports"][0]["eligible_for_automatic_promotion"] is False
    assert original["reports"]["final_trade_decision"] == "Original daily fixture decision"
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(QualificationAdmission)) == 1
        # Only setup records existed; qualification never creates order/fill records.
        before = db.scalar(select(func.count()).select_from(TradingRecord))
    assert ready.post(BASE, json=body).json()["id"] == protocol["id"]
    assert ready.post(BASE, json={**body, "name": "Changed request"}).status_code == 409
    assert ready.post(f"{BASE}/{protocol['id']}/evaluate", json=action()).status_code == 409
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(TradingRecord)) == before
    for table in (
        "qualification_protocols",
        "qualification_evaluations",
        "qualification_admissions",
        "qualification_reports",
    ):
        for sql in (
            f"UPDATE {table} SET owner_id='changed'",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with database[1].begin() as db, pytest.raises(DBAPIError):
                db.execute(text(sql))


@pytest.mark.parametrize(
    "changes",
    [
        {"strategy_version_id": str(uuid4())},
        {"evidence_checksum": "wrong"},
        {"adapter_version": "daily_v1"},
        {"source": "current_daily_research"},
        {"intended_mode": "live_scalping"},
        {"execution_authority": True},
        {"decision": "BUY"},
        {"limitations": []},
    ],
)
def test_wrong_native_scope_cannot_pass(ready, database, portfolios_ready, changes):
    protocol, _ = evaluated(ready, portfolios_ready[0])
    protocol, _ = admitted(ready, database, protocol)
    finish(database, protocol, changes=changes)
    result = reported(ready, protocol)["reports"][0]
    assert (
        next(c for c in result["criteria"] if c["key"] == "exact_version_tauric")["status"]
        == "incomplete"
    )
    assert result["status"] != "passed"


@pytest.mark.parametrize(
    "decision,expected",
    [("unsuitable", "failed"), ("incomplete", "incomplete"), (None, "incomplete")],
)
def test_adverse_or_missing_strategy_verdict_does_not_use_daily_buy(
    ready, database, portfolios_ready, decision, expected
):
    protocol, _ = evaluated(ready, portfolios_ready[0])
    protocol, _ = admitted(ready, database, protocol)
    finish(database, protocol, decision=decision, omit=decision is None)
    assert reported(ready, protocol)["reports"][0]["status"] == expected


@pytest.mark.parametrize("mode", ["paper_closed_bar", "live_scalping"])
def test_ohlc_evidence_cannot_certify_execution_mode(ready, database, portfolios_ready, mode):
    protocol, _ = evaluated(ready, portfolios_ready[0], intended_mode=mode)
    protocol, _ = admitted(ready, database, protocol)
    finish(database, protocol)
    assert reported(ready, protocol)["reports"][0]["status"] == "incomplete"


def test_holdout_reuse_and_reimport_are_disclosed_and_fail(ready, database, portfolios_ready):
    first, body = evaluated(ready, portfolios_ready[0])
    data = ready.post("/api/v1/research/datasets/import", json=import_request(evidence())).json()
    second = ready.post(BASE, json={**body, **action(), "dataset_id": data["id"]}).json()
    second = ready.post(f"{BASE}/{second['id']}/evaluate", json=action()).json()
    assert second["evaluation"]["holdout_exposure"][0]["id"] == first["evaluation"]["id"]
    assert reported(ready, second)["reports"][0]["status"] == "failed"
    run = run_request(data["id"])
    assert ready.post("/api/v1/research/runs", json=run).status_code == 201
    third = ready.post(BASE, json={**body, **action()}).json()
    third = ready.post(f"{BASE}/{third['id']}/evaluate", json=action()).json()
    assert {e["kind"] for e in third["evaluation"]["holdout_exposure"]} == {
        "ordinary_replay",
        "previous_holdout_evaluation",
    }


def test_cost_failure_and_stale_mandate_are_explicit(ready, database, portfolios_ready):
    scope = portfolios_ready[0]
    protocol, _ = evaluated(
        ready, scope, stresses=[{"name": "Severe costs", "fee_bps": "5000", "slippage_bps": "5000"}]
    )
    result = reported(ready, protocol)["reports"][0]
    assert result["status"] == "failed"
    assert any(
        c["status"] == "failed" and c["key"].startswith("Severe costs:") for c in result["criteria"]
    )
    assert (
        ready.put(
            f"/api/v1/portfolios/{scope['id']}",
            json=preferences(scope, 1, mandate="Changed frozen mandate"),
        ).status_code
        == 200
    )
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    assert (
        ready.post(
            f"{BASE}/{protocol['id']}/assessment", json={**action(), "profile": "local"}
        ).status_code
        == 409
    )
    assert (
        next(
            c
            for c in reported(ready, protocol)["reports"][0]["criteria"]
            if c["key"] == "portfolio_scope"
        )["status"]
        == "incomplete"
    )


def test_native_failure_reuse_budget_and_expiry(ready, database, portfolios_ready, monkeypatch):
    protocol, _ = evaluated(ready, portfolios_ready[0])
    protocol, _ = admitted(ready, database, protocol)
    with Session(database[0]) as db:
        job_id = analysis.claim(db)
        assert analysis.finish(db, job_id, error="Offline failure")
    reused, _ = admitted(ready, database, protocol)
    assert len(reused["assessment_jobs"]) == 1
    assert reported(ready, reused)["reports"][0]["status"] == "incomplete"
    other, _ = evaluated(ready, portfolios_ready[1])
    other, _ = admitted(ready, database, other)
    finish(database, other)
    future = now() + timedelta(days=2)
    monkeypatch.setattr(qualification, "now", lambda: future)
    result = reported(ready, other)["reports"][0]
    assert (
        next(c for c in result["criteria"] if c["key"] == "exact_version_tauric")["detail"]
        == "Native strategy assessment expired."
    )


def test_no_future_leakage_and_owner_scope(ready, database, portfolios_ready):
    first, body = evaluated(ready, portfolios_ready[0])
    changed = copy.deepcopy(evidence())
    for bar in changed[7:]:
        bar.update(open="500", high="510", low="490", close="500", volume="1000")
    data = ready.post("/api/v1/research/datasets/import", json=import_request(changed)).json()
    second = ready.post(BASE, json={**body, **action(), "dataset_id": data["id"]}).json()
    second = ready.post(f"{BASE}/{second['id']}/evaluate", json=action()).json()
    assert first["evaluation"]["training"] == second["evaluation"]["training"]
    assert first["evaluation"]["holdout"] != second["evaluation"]["holdout"]
    with Session(database[0]) as db:
        other = SimpleNamespace(id=str(uuid4()))
        with pytest.raises(Exception) as exc:
            qualification.owned(db, other, QualificationProtocol, first["id"])
        assert exc.value.status_code == 404


@pytest.mark.parametrize("malformed", [False, True])
def test_native_extension_receives_frozen_evidence_and_never_retries(malformed):
    expected = {
        "strategy_version_id": "exact-version",
        "evidence_checksum": "evidence-hash",
        "intended_mode": "research_comparison",
    }
    value = {
        **expected,
        "decision": "suitable",
        "rationale": "Evidence reviewed by fixture",
        "limitations": ["No real model test"],
    }
    calls = []

    class LLM:
        def with_structured_output(self, schema):
            self.schema = schema
            return self

        def invoke(self, messages):
            calls.append(messages)
            return {**value, "evidence_checksum": "wrong"} if malformed else self.schema(**value)

    context = {
        "assessment_input": {
            "strategy": {"id": "exact-version", "rules": {"lookback": 2}},
            "intended_mode": "research_comparison",
            "stresses": ["frozen stress"],
        },
        "evidence_checksum": "evidence-hash",
    }
    result = native_strategy_assessment.assess(
        SimpleNamespace(deep_thinking_llm=LLM()),
        context,
        {"final_trade_decision": "Original background report"},
    )
    assert result["decision"] == ("incomplete" if malformed else "suitable")
    assert len(calls) == 1 and result["execution_authority"] is False
    supplied = json.loads(calls[0][1][1])
    assert supplied["frozen_evidence"] == context["assessment_input"]
    assert (
        supplied["daily_background_reports"]["final_trade_decision"] == "Original background report"
    )


def test_qualification_chat_is_bound_and_cannot_be_entry_approval(
    ready, database, portfolios_ready, setup
):
    first, second, *_ = portfolios_ready
    protocol, _ = evaluated(ready, first)
    protocol, _ = admitted(ready, database, protocol)
    finish(database, protocol)
    protocol = reported(ready, protocol)
    artifact_id = protocol["assessment_jobs"][0]["job"]["artifact_id"]
    turn, identity = active_turn(ready, database, first)
    context = ready.post("/internal/openclaw/chat-read", headers=HEADERS, json=identity)
    assert context.status_code == 200, context.text
    summary = context.json()["strategy_qualification"][0]
    assert (
        summary["latest_report"]["status"] == "passed" and summary["execution_authority"] is False
    )
    assert "engine_sources" not in json.dumps(summary)
    original = ready.post(
        "/internal/openclaw/chat-read", headers=HEADERS, json={**identity, "report_id": artifact_id}
    )
    assert original.status_code == 200, original.text
    with Session(database[0]) as db:
        row = db.get(ChatTurn, turn["id"])
        chat.finish(db, row.id, row.lease_id, result=("Offline fixture reply", {}))
    _, identity_b = active_turn(ready, database, second)
    assert (
        ready.post("/internal/openclaw/chat-read", headers=HEADERS, json=identity_b).json()[
            "strategy_qualification"
        ]
        == []
    )
    assert (
        ready.post(
            "/internal/openclaw/chat-read",
            headers=HEADERS,
            json={**identity_b, "report_id": artifact_id},
        ).status_code
        == 404
    )
    result = ready.post(
        "/api/v1/paper/proposals",
        json={
            **action(),
            "deployment_id": portfolios_ready[2]["id"],
            "stop_price": "90",
            "native_artifact_id": artifact_id,
        },
    )
    assert result.status_code == 422 and "qualification" in result.text, result.text


def test_shared_budget_blocks_qualification_admission(ready, database, portfolios_ready):
    from test_portfolio_research import budget

    budget(ready, 0)
    protocol, _ = evaluated(ready, portfolios_ready[0])
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    response = ready.post(
        f"{BASE}/{protocol['id']}/assessment", json={**action(), "profile": "local"}
    )
    assert response.status_code == 429, response.text
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(QualificationAdmission)) == 0


@pytest.mark.parametrize("change", ["model", "settings", "source", "account_policy"])
def test_changed_context_blocks_native_validation_and_fresh_report(
    ready, database, portfolios_ready, monkeypatch, change
):
    protocol, _ = evaluated(ready, portfolios_ready[0])
    protocol, _ = admitted(ready, database, protocol)
    finish(database, protocol)
    assert reported(ready, protocol)["reports"][0]["status"] == "passed"
    if change == "model":
        with Session(database[0]) as db:
            profiles, error = qualification.load_profiles(db)
        profiles["local"] = profiles["local"].model_copy(update={"deep_model": "different-model"})
        monkeypatch.setattr(qualification, "load_profiles", lambda db: (profiles, error))
    elif change == "settings":
        previous = qualification.services.setting_view

        def settings(db, owner, key):
            value = previous(db, owner, key)
            return (
                SimpleNamespace(effective_value=value.effective_value + 1)
                if key == "analysis.max_tool_rounds"
                else value
            )

        monkeypatch.setattr(qualification.services, "setting_view", settings)
    elif change == "source":
        monkeypatch.setattr(qualification, "sources", lambda: {"changed": "implementation"})
    else:
        from test_paper import P

        account_id = protocol["frozen"]["account_policies"][0]["account_id"]
        account = ready.get("/api/v1/paper").json()["accounts"][0]
        assert (
            ready.put(
                f"/api/v1/paper/accounts/{account_id}/policy",
                json={
                    "reason": "Verify changed account policy invalidates qualification",
                    "expected_revision": account["revision"],
                    "policy": {**P, "max_positions": 4},
                },
            ).status_code
            == 200
        )
    with Session(database[0]) as db:
        job = db.get(AnalysisJob, protocol["assessment_jobs"][0]["job"]["id"])
        with pytest.raises(ValueError, match="context changed"):
            qualification.validate_admission(db, job)
    result = reported(ready, protocol)["reports"][0]
    assert result["status"] == "incomplete"
    assert (
        next(c for c in result["criteria"] if c["key"] == "exact_version_tauric")["status"]
        == "incomplete"
    )
