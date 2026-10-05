"""Portfolio isolation, atomic chat effects and immutable history on disposable PostgreSQL."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_capital import allocation, strategy
from test_paper import action, buy
from test_paper import setup as setup

from tauric import analysis, chat, paper, portfolios, services
from tauric.models import (
    AnalysisArtifact,
    ChatTurn,
    Owner,
    PaperAccount,
    PortfolioActionReceipt,
    PortfolioPolicyVersion,
    TradingRecord,
    now,
)
from tauric.portfolio_contracts import PortfolioSave

pytest_plugins = ["test_chat"]
HEADERS = {"Authorization": "Bearer fixture-private-" + "x" * 50}
ACTION = "/internal/openclaw/portfolio-action"
READ = "/internal/openclaw/chat-read"


def preferences(scope, revision=0, **changes):
    return {
        **action(),
        "expected_revision": revision,
        "allocation_ids": [scope["id"]],
        "preferences": {
            "mandate": "Scalp liquid crypto within existing account risk limits",
            "research_objective": "net_return",
            "capital_mode": "fixed",
            "chat_edit_preferences": True,
            "chat_control_deployments": True,
            **changes,
        },
    }


@pytest.fixture
def portfolios_ready(ready, setup):
    a, _ = allocation(ready, setup[0], "Scalping", budget="4000")
    b, _ = allocation(ready, setup[0], "Mixed", budget="4000")
    da = strategy(ready, setup[3], a)
    db = strategy(ready, setup[3], b)
    for scope in (a, b):
        r = ready.put(f"/api/v1/portfolios/{scope['id']}", json=preferences(scope))
        assert r.status_code == 200, r.text
    return a, b, da, db


def active_turn(client, database, scope):
    r = client.post(
        "/api/v1/chat/messages",
        json={
            "request_id": str(uuid4()),
            "conversation_id": str(uuid4()),
            "portfolio_id": scope["id"],
            "text": "Explain my portfolio",
        },
    )
    assert r.status_code == 202, r.text
    with Session(database[0]) as db:
        chat.claim(db)
        owner = db.scalar(select(Owner))
        identity = {
            "agent_id": "tauric-chat",
            "channel": "webchat",
            "sender_is_owner": True,
            "session_key": f"agent:tauric-chat:openai-user:tauric-web-{owner.id}-{r.json()['conversation_id']}",
            "tool_call_id": uuid4().hex,
        }
    return r.json(), identity


def test_settings_cas_retries_account_links_and_database_guards(ready, setup, database):
    a, _ = allocation(ready, setup[0], "Scalping", budget="4000")
    request = preferences(a)
    path = f"/api/v1/portfolios/{a['id']}"
    assert ready.put(path, json=request).json()["revision"] == 1
    assert ready.put(path, json=request).json()["revision"] == 1
    assert ready.put(path, json=preferences(a)).status_code == 409
    assert ready.put(path, json={**request, "reason": "Different retry"}).status_code == 409
    second = ready.post(
        "/api/v1/paper/accounts", json={**setup[2], **action(), "name": "Other account"}
    ).json()
    c, _ = allocation(ready, second, "Linked allocation")
    body = preferences(a, 1, capital_mode="managed")
    body["allocation_ids"].append(c["id"])
    result = ready.put(path, json=body)
    assert result.status_code == 200, result.text
    result = result.json()
    assert {v["id"] for v in result["accounts"]} == {setup[0]["id"], second["id"]}
    assert result["capabilities"]["managed_reallocation"] is False
    assert ready.get("/api/v1/portfolios").json()[0]["id"] == a["id"]
    assert ready.get(f"/api/v1/portfolios/{c['id']}").status_code == 409
    assert [v["revision"] for v in ready.get(path + "/history").json()] == [2, 1]
    for table in (
        "portfolio_policy_versions",
        "chat_portfolio_bindings",
        "portfolio_action_receipts",
    ):
        for sql in (
            f"UPDATE {table} SET owner_id='changed'",
            f"DELETE FROM {table}",
            f"TRUNCATE {table}",
        ):
            with database[0].begin() as db, pytest.raises(DBAPIError):
                db.execute(text(sql))


def test_conversation_binding_history_and_context_are_permanent(ready, database, portfolios_ready):
    a, b, da, db_dep = portfolios_ready
    turn, identity = active_turn(ready, database, a)
    context = ready.post(READ, headers=HEADERS, json=identity)
    assert context.status_code == 200, context.text
    data = context.json()
    assert data["portfolio"]["id"] == a["id"]
    assert {d["id"] for d in data["deployments"]} == {da["id"]}
    assert {v["id"] for v in data["allocations"]} == {a["id"]}
    assert db_dep["id"] not in context.text
    assert data["live_execution_enabled"] is False
    assert data["intelligence_dependency"] == "tauric"
    assert ready.get("/api/v1/chat/conversations").json() == []
    assert (
        len(ready.get("/api/v1/chat/conversations", params={"portfolio_id": a["id"]}).json()) == 1
    )
    assert (
        ready.get(
            f"/api/v1/chat/conversations/{turn['conversation_id']}",
            params={"portfolio_id": b["id"]},
        ).json()
        == []
    )
    with Session(database[0]) as db:
        row = db.get(ChatTurn, turn["id"])
        chat.finish(db, row.id, row.lease_id, result=("Fixture reply", {}))
    request = {
        "request_id": str(uuid4()),
        "conversation_id": turn["conversation_id"],
        "portfolio_id": b["id"],
        "text": "Switch context",
    }
    assert ready.post("/api/v1/chat/messages", json=request).status_code == 409
    request["portfolio_id"] = None
    assert ready.post("/api/v1/chat/messages", json=request).status_code == 409
    with database[1].begin() as db, pytest.raises(DBAPIError):
        db.execute(text("UPDATE chat_turns SET portfolio_id=:id"), {"id": b["id"]})


def test_chat_preferences_permissions_conflict_receipt_and_retry(ready, database, portfolios_ready):
    a, _, _, _ = portfolios_ready
    turn, identity = active_turn(ready, database, a)
    body = {
        **identity,
        "action": "preferences",
        "expected_revision": 1,
        "research_objective": "consistency",
        "reason": "Prioritise consistent research",
    }
    response = ready.post(ACTION, headers=HEADERS, json=body)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["result"]["before"]["revision"] == 1
    assert receipt["result"]["after"]["revision"] == 2
    assert ready.post(ACTION, headers=HEADERS, json=body).json()["id"] == receipt["id"]
    assert (
        ready.post(ACTION, headers=HEADERS, json={**body, "reason": "Different action"}).status_code
        == 409
    )
    assert (
        ready.post(ACTION, headers=HEADERS, json={**body, "tool_call_id": "stale"}).status_code
        == 409
    )
    turns = ready.get(
        f"/api/v1/chat/conversations/{turn['conversation_id']}", params={"portfolio_id": a["id"]}
    ).json()
    assert turns[0]["action_receipts"][0]["id"] == receipt["id"]
    changed = preferences(a, 2, chat_edit_preferences=False)
    assert ready.put(f"/api/v1/portfolios/{a['id']}", json=changed).status_code == 200
    assert (
        ready.post(
            ACTION,
            headers=HEADERS,
            json={**body, "expected_revision": 3, "tool_call_id": "disabled"},
        ).status_code
        == 403
    )
    assert (
        ready.post(
            ACTION, headers=HEADERS, json={**body, "chat_edit_preferences": True}
        ).status_code
        == 422
    )
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(PortfolioActionReceipt)) == 1


def test_pause_resume_scope_retries_and_no_orders(ready, database, portfolios_ready):
    a, _, da, db_dep = portfolios_ready
    fill = buy(ready, da)
    _, identity = active_turn(ready, database, a)
    body = {
        **identity,
        "action": "pause",
        "expected_revision": 0,
        "deployment_id": da["id"],
        "reason": "Pause this paper strategy",
    }
    assert (
        ready.post(
            ACTION, headers=HEADERS, json={**body, "deployment_id": db_dep["id"]}
        ).status_code
        == 404
    )
    with Session(database[0]) as db:
        acct = db.get(PaperAccount, da["account_id"])
        before = paper.ledger(db, acct)
        assert fill["id"] in before[2]
        position_payloads = {key: row.payload for key, row in before[2].items()}
        count = db.scalar(select(func.count()).select_from(TradingRecord))
    result = ready.post(ACTION, headers=HEADERS, json=body)
    assert result.status_code == 200, result.text
    assert result.json()["result"]["after"]["enabled"] is False
    assert ready.post(ACTION, headers=HEADERS, json=body).json()["id"] == result.json()["id"]
    assert (
        ready.post(
            ACTION, headers=HEADERS, json={**body, "action": "resume", "tool_call_id": "stale"}
        ).status_code
        == 409
    )
    resumed = ready.post(
        ACTION,
        headers=HEADERS,
        json={**body, "action": "resume", "expected_revision": 1, "tool_call_id": "resume"},
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["result"]["after"]["enabled"] is True
    with Session(database[0]) as db:
        after = paper.ledger(db, db.get(PaperAccount, da["account_id"]))
        assert (after[0], after[1], after[3]) == (before[0], before[1], before[3])
        assert {key: row.payload for key, row in after[2].items()} == position_payloads
        assert db.scalar(select(func.count()).select_from(TradingRecord)) == count
    for table in ("portfolio_action_receipts", "chat_portfolio_bindings"):
        with database[1].begin() as db, pytest.raises(DBAPIError):
            db.execute(text(f"UPDATE {table} SET owner_id=owner_id"))


@pytest.mark.parametrize(
    "fault,code", [("owner", 403), ("expired", 409), ("other_session", 403), ("token", 401)]
)
def test_private_actions_require_current_owner_invocation(
    ready, database, portfolios_ready, fault, code
):
    a, _, _, _ = portfolios_ready
    turn, identity = active_turn(ready, database, a)
    body = {
        **identity,
        "action": "preferences",
        "expected_revision": 1,
        "mandate": "Adjusted mandate",
        "reason": "Owner test change",
    }
    headers = HEADERS
    if fault == "owner":
        body["sender_is_owner"] = False
    if fault == "other_session":
        body["session_key"] = "agent:tauric-chat:openai-user:tauric-web-other-owner"
    if fault == "token":
        headers = {}
    if fault == "expired":
        with Session(database[0]) as db:
            db.get(ChatTurn, turn["id"]).heartbeat_at = now() - timedelta(seconds=60)
            db.commit()
    assert ready.post(ACTION, headers=headers, json=body).status_code == code
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(PortfolioActionReceipt)) == 0


def test_failed_receipt_rolls_back_control_and_audit(
    ready, database, portfolios_ready, monkeypatch
):
    a, _, da, _ = portfolios_ready
    _, identity = active_turn(ready, database, a)
    audit = services.audit

    def fail_receipt(db, owner, event, *args, **kwargs):
        if event == "portfolio.chat_action":
            raise RuntimeError("Fixture receipt failure")
        return audit(db, owner, event, *args, **kwargs)

    monkeypatch.setattr(services, "audit", fail_receipt)
    with pytest.raises(RuntimeError, match="Fixture receipt failure"):
        ready.post(
            ACTION,
            headers=HEADERS,
            json={
                **identity,
                "action": "pause",
                "expected_revision": 0,
                "deployment_id": da["id"],
                "reason": "Atomic pause test",
            },
        )
    context = ready.get(f"/api/v1/portfolios/{a['id']}").json()
    assert context["deployments"][0]["enabled"] is True
    assert context["deployments"][0]["control_revision"] == 0
    assert context["receipts"] == []


def test_competing_settings_edits_have_one_winner(ready, database, portfolios_ready):
    a, _, _, _ = portfolios_ready

    def write(objective):
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            try:
                portfolios.save(
                    db,
                    owner,
                    a["id"],
                    PortfolioSave.model_validate(preferences(a, 1, research_objective=objective)),
                )
                return "saved"
            except Exception as e:
                db.rollback()
                return getattr(e, "status_code", type(e).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, ["balanced", "consistency"]), key=str) == [409, "saved"]
    with Session(database[0]) as db:
        assert (
            db.scalar(
                select(func.count())
                .select_from(PortfolioPolicyVersion)
                .where(PortfolioPolicyVersion.portfolio_id == a["id"])
            )
            == 2
        )


def test_portfolio_research_cannot_read_or_request_other_scope(ready, database, portfolios_ready):
    from tauric.native_runner import artifact

    a, b, _, _ = portfolios_ready
    _, identity = active_turn(ready, database, a)
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    request = {**identity, "instrument_id": "binance:spot:ETHUSDT"}
    assert (
        ready.post("/internal/openclaw/research", headers=HEADERS, json=request).status_code == 403
    )
    request["instrument_id"] = "binance:spot:BTCUSDT"
    run = ready.post("/internal/openclaw/research", headers=HEADERS, json=request)
    assert run.status_code == 202, run.text
    job = run.json()["job"]
    with Session(database[0]) as db:
        assert analysis.claim(db) == job["id"]
        original = artifact(
            {
                "market_report": "Untouched native portfolio research",
                "final_trade_decision": "Original Hold",
            },
            "Hold",
            job["request"],
            {},
            "0.5.2",
        )
        assert analysis.finish(db, job["id"], original)
        report = db.scalar(select(AnalysisArtifact).where(AnalysisArtifact.job_id == job["id"]))
        report_id = report.id
        original_checksum = report.checksum
    read_report = ready.post(READ, headers=HEADERS, json={**identity, "report_id": report_id})
    assert read_report.status_code == 200, read_report.text
    assert read_report.json()["original_report"]["payload"] == original
    assert (
        ready.post(READ, headers=HEADERS, json={**identity, "job_id": job["id"]}).status_code == 200
    )
    with Session(database[0]) as db:
        row = db.scalar(select(ChatTurn).where(ChatTurn.status == "running"))
        chat.finish(db, row.id, row.lease_id, result=("Fixture daily research requested", {}))
    _, other = active_turn(ready, database, b)
    assert (
        ready.post(READ, headers=HEADERS, json={**other, "report_id": report_id}).status_code == 404
    )
    with Session(database[0]) as db:
        report = db.get(AnalysisArtifact, report_id)
        assert report.payload == original and report.checksum == original_checksum
    assert ready.post(READ, headers=HEADERS, json={**other, "job_id": job["id"]}).status_code == 404
    assert ready.get(f"/api/v1/portfolios/{b['id']}").json()["research_jobs"] == []
