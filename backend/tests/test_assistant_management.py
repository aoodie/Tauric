"""Manager effects preserve ownership, native authority and immutable review history."""

import copy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_capital import allocation
from test_chat import config, send
from test_chat import ready as ready
from test_paper import setup as setup
from test_portfolio_chat import active_turn, preferences
from test_scanner import scanner_feed as scanner_feed

from tauric import assistant, chat
from tauric.models import AnalysisArtifact, AssistantRecord, ChatTurn, Owner, StrategyVersion, now

HEADERS = {"Authorization": "Bearer fixture-private-" + "x" * 50}
PATH = "/internal/openclaw/project-action"
READ = "/internal/openclaw/project-read"


@pytest.fixture
def invocation(ready, database):
    turn = send(ready, "Review my strategies and prepare improvements").json()
    with Session(database[0]) as db:
        chat.claim(db)
        owner = db.scalar(select(Owner))
        return {
            "channel": "webchat",
            "agent_id": "tauric-chat",
            "sender_is_owner": True,
            "session_key": f"agent:tauric-chat:openai-user:tauric-web-{owner.id}-{turn['conversation_id']}",
            "account_id": "default",
            "tool_call_id": "manager-" + uuid4().hex,
        }


def strategy():
    return {
        "name": "Quant hypothesis",
        "parameters": {
            "label": "Initial",
            "description": "Test a simple baseline with explicit costs",
            "quantity_step": "0.00001",
            "capital_limit": "1000",
            "risk_percent": "1",
            "rules": None,
        },
    }


def action(client, identity, operation="create_strategy", parameters=None):
    return client.post(
        PATH,
        headers=HEADERS,
        json={
            **identity,
            "operation": operation,
            "parameters": parameters if parameters is not None else strategy(),
            "reason": "Owner-requested research upgrade",
        },
    )


def counts(database):
    with Session(database[0]) as db:
        return tuple(
            db.scalar(
                select(func.count()).select_from(m).where(AssistantRecord.stage == "completed")
                if m is AssistantRecord
                else select(func.count()).select_from(m)
            )
            for m in (AssistantRecord, StrategyVersion, AnalysisArtifact)
        )


@pytest.mark.parametrize(
    "change,status",
    [
        ({"sender_is_owner": False}, 403),
        ({"sender_is_owner": "true"}, 422),
        ({"agent_id": "governor-local"}, 422),
        ({"channel": "cron"}, 422),
        ({"account_id": "other"}, 403),
        ({"session_key": "agent:tauric-chat:unbound"}, 403),
    ],
)
def test_management_needs_trusted_owner_identity(ready, database, invocation, change, status):
    assert action(ready, {**invocation, **change}).status_code == status
    assert counts(database) == (0, 0, 0)


def test_requires_private_token_and_fresh_lease(ready, database, invocation):
    assert (
        ready.post(
            PATH,
            json={
                **invocation,
                "operation": "create_strategy",
                "parameters": strategy(),
                "reason": "Owner asked",
            },
        ).status_code
        == 401
    )
    with Session(database[0]) as db:
        row = db.scalar(select(ChatTurn))
        row.heartbeat_at = now() - timedelta(seconds=50)
        db.commit()
    assert action(ready, invocation).status_code == 409
    assert counts(database) == (0, 0, 0)


def test_strategy_creation_revision_and_immutable_receipt(ready, database, invocation):
    first = action(ready, invocation)
    assert first.status_code == 200, first.text
    saved = first.json()
    assert saved["status"] == "saved" and saved["result"]["revision"] == 1
    assert action(ready, invocation).json()["id"] == saved["id"]
    changed = strategy()
    changed["parameters"]["risk_percent"] = "2"
    assert action(ready, invocation, parameters=changed).status_code == 409
    assert counts(database) == (1, 1, 0)
    revise = {
        "strategy_id": saved["result"]["strategy_id"],
        "expected_revision": 1,
        "parameters": {**strategy()["parameters"], "label": "Second"},
    }
    second = action(ready, {**invocation, "tool_call_id": "second"}, "revise_strategy", revise)
    assert second.status_code == 200, second.text
    assert second.json()["result"]["revision"] == 2
    assert (
        action(
            ready, {**invocation, "tool_call_id": "stale"}, "revise_strategy", revise
        ).status_code
        == 409
    )
    assert counts(database) == (2, 2, 0)
    assert len(ready.get("/api/v1/assistant").json()["receipts"]) == 2
    conversation = ready.get("/api/v1/chat/conversations").json()[0]["id"]
    turns = ready.get(f"/api/v1/chat/conversations/{conversation}").json()
    assert len(turns[0]["project_receipts"]) == 2
    with Session(database[0]) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE assistant_records SET title='overridden'"))
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("DELETE FROM assistant_records"))


@pytest.mark.parametrize(
    "operation",
    [
        "place_order",
        "approve_strategy",
        "write_native_report",
        "apply_patch",
        "change_risk_limits",
        "run_shell",
    ],
)
def test_no_execution_or_override_operation_exists(ready, database, invocation, operation):
    assert action(ready, invocation, operation, {}).status_code == 422
    assert counts(database) == (0, 0, 0)


def test_typed_contract_and_identity_cannot_be_overridden(ready, database, invocation):
    params = strategy()
    params["native_approved"] = True
    assert action(ready, invocation, parameters=params).status_code == 422
    params.pop("native_approved")
    params["request_id"] = str(uuid4())
    assert action(ready, invocation, parameters=params).status_code == 422
    params.pop("request_id")
    params["parameters"]["risk_percent"] = "101"
    assert action(ready, invocation, parameters=params).status_code == 422
    assert counts(database) == (0, 0, 0)


def note():
    return {
        "title": "Compare two simple rules",
        "hypothesis": "A closed-bar rule improves cost-adjusted stability",
        "evidence": ["Saved deterministic baseline"],
        "counter_evidence": ["Forward sample is small"],
        "test_plan": [
            "Freeze an independent evaluation period",
            "Compare explicit costs and drawdown",
        ],
        "decision": "test",
    }


def test_upgrade_is_checksum_bound_and_never_applies_source(ready, database, invocation):
    identity = {**invocation, "resource": "source", "path": "frontend/src/scanner/Scanner.tsx"}
    source = ready.post(READ, headers=HEADERS, json=identity)
    assert source.status_code == 200, source.text
    source = source.json()
    assert len(source["lines"]) <= 160
    before = assistant.source_path(identity["path"]).read_bytes()
    proposal = {
        **note(),
        "files": [{"path": identity["path"], "sha256": source["sha256"]}],
        "changes": ["Simplify scanner labels"],
        "patch": "--- a/frontend/src/scanner/Scanner.tsx\n+++ b/frontend/src/scanner/Scanner.tsx\n@@ -1 +1 @@\n-old\n+new\n",
    }
    rejected = copy.deepcopy(proposal)
    rejected["files"][0]["sha256"] = "0" * 64
    assert (
        action(
            ready, {**invocation, "tool_call_id": "stale-proposal"}, "prepare_upgrade", rejected
        ).status_code
        == 409
    )
    saved = action(ready, invocation, "prepare_upgrade", proposal)
    assert saved.status_code == 200, saved.text
    saved = saved.json()
    assert saved["status"] == "review_required"
    assert saved["result"]["validation"] == "not_run"
    assert saved["result"]["code_changes_applied"] is False
    assert assistant.source_path(identity["path"]).read_bytes() == before
    assert counts(database) == (1, 0, 0)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "frontend/src/.env.models.json",
        "backend/tauric/../../.env.models.json",
        "/etc/passwd",
        "frontend/src/../../../VisitFest.de/package.json",
    ],
)
def test_source_cannot_read_runtime_secrets_or_other_projects(ready, invocation, path):
    assert (
        ready.post(
            READ, headers=HEADERS, json={**invocation, "resource": "source", "path": path}
        ).status_code
        == 422
    )


def test_source_rejects_escape_symlink_and_schemas_are_real(
    ready, invocation, monkeypatch, tmp_path
):
    (tmp_path / "frontend/src").mkdir(parents=True)
    (tmp_path / "frontend/src/config.ts").symlink_to("/etc/passwd")
    monkeypatch.setattr(assistant, "source_root", lambda: tmp_path)
    assert (
        ready.post(
            READ,
            headers=HEADERS,
            json={**invocation, "resource": "source", "path": "frontend/src/config.ts"},
        ).status_code
        == 422
    )
    schemas = ready.post(
        READ,
        headers=HEADERS,
        json={**invocation, "resource": "schemas", "operation": "save_scanner"},
    ).json()
    schema = schemas["parameters_schemas"]["save_scanner"]
    assert schema["additionalProperties"] is False
    assert "request_id" not in schema["properties"] and "reason" not in schema["properties"]
    assert "expected_revision" in schema["required"]


def test_general_tools_cannot_escape_portfolio_scope(ready, database, setup):
    scope, _ = allocation(ready, setup[0], "Research portfolio", budget="4000")
    assert (
        ready.put(f"/api/v1/portfolios/{scope['id']}", json=preferences(scope)).status_code == 200
    )
    _, identity = active_turn(ready, database, scope)
    assert action(ready, identity).status_code == 403
    assert ready.post(READ, headers=HEADERS, json=identity).status_code == 403
    assert counts(database) == (0, 0, 0)


def test_owner_direct_messages_share_management_receipts(ready, invocation):
    assert ready.put("/api/v1/chat/settings", json=config()).status_code == 200
    identity = {
        **invocation,
        "channel": "telegram",
        "session_key": "agent:tauric-chat:telegram:default:direct:12345678",
        "sender_id": "telegram:12345678",
    }
    saved = action(ready, identity, "save_research_note", note())
    assert saved.status_code == 200, saved.text
    assert saved.json()["turn_id"] is None
    assert action(ready, identity, "save_research_note", note()).json()["id"] == saved.json()["id"]
    assert (
        ready.post(
            READ, headers=HEADERS, json={**identity, "sender_id": "telegram:999"}
        ).status_code
        == 403
    )


def test_lost_receipt_recovers_same_effect_and_binds_original_inputs(
    ready, database, invocation, monkeypatch
):
    audit = assistant.services.audit

    def fail_receipt(db, owner, event, *args, **kwargs):
        if event == "assistant.create_strategy":
            raise RuntimeError("Synthetic lost receipt")
        return audit(db, owner, event, *args, **kwargs)

    monkeypatch.setattr(assistant.services, "audit", fail_receipt)
    with pytest.raises(RuntimeError, match="Synthetic lost receipt"):
        action(ready, invocation)
    assert counts(database) == (0, 1, 0)
    assert action(ready, invocation, "save_research_note", note()).status_code == 409
    monkeypatch.setattr(assistant.services, "audit", audit)
    recovered = action(ready, invocation)
    assert recovered.status_code == 200, recovered.text
    assert counts(database) == (1, 1, 0)
    assert action(ready, invocation).json()["id"] == recovered.json()["id"]


def test_manager_scans_watches_and_runs_owned_research(ready, database, invocation, scanner_feed):
    def call(key, operation, params):
        result = action(ready, {**invocation, "tool_call_id": key}, operation, params)
        assert result.status_code == 200, result.text
        return result.json()["result"]

    profile = call(
        "profile",
        "save_scanner",
        {
            "name": "Independent quant scan",
            "expected_revision": 0,
            "parameters": {
                "enabled": True,
                "universe": ["binance:spot:BTCUSDT"],
                "interval": "15m",
                "detector_id": "closed_bar_breakout_v1",
                "lookback": 3,
                "minimum_volume_ratio": "2",
                "candidate_ttl_seconds": 600,
                "max_analyses_per_day": 0,
                "model_profile": "local",
            },
        },
    )
    scanned = call("scan", "run_scanner", {"profile_version_id": profile["id"]})
    assert scanned["results"][0]["status"] == "matched"
    candidate_id = scanned["results"][0]["candidate_id"]
    watched = call("watch", "candidate_action", {"candidate_id": candidate_id, "action": "watch"})
    assert watched["status"] == "watch"
    assert (
        action(
            ready,
            {**invocation, "tool_call_id": "no-budget"},
            "research_candidate",
            {"candidate_id": candidate_id, "profile_version_id": profile["id"]},
        ).status_code
        == 429
    )
    with Session(database[0]) as db:
        snapshot = db.scalar(
            select(assistant.MarketSnapshot).order_by(assistant.MarketSnapshot.received_at.desc())
        )
        snapshot_id = snapshot.id
    dataset = call(
        "dataset",
        "capture_dataset",
        {"name": "Synthetic independent dataset", "snapshot_id": snapshot_id},
    )
    backtest = call(
        "test",
        "run_backtest",
        {
            "dataset_id": dataset["id"],
            "name": "Synthetic baseline",
            "hypothesis": "Compare a simple closed-bar crossover",
            "parameters": {"fast_window": 1, "slow_window": 2},
        },
    )
    assert backtest["result"]["bars"] == 4 and "trades" not in backtest["result"]
    overview = ready.post(READ, headers=HEADERS, json=invocation)
    assert overview.status_code == 200, overview.text
    overview = overview.json()
    assert overview["execution_authority"] is False and overview["native_reports_writable"] is False
    assert overview["datasets"][0]["id"] == dataset["id"]
    assert counts(database) == (5, 0, 0)


@pytest.mark.parametrize(
    "patch",
    [
        "--- a/frontend/src/App.tsx\n+++ b/frontend/src/App.tsx\n@@ -1 +1 @@\n-old\n+new\n",
        "--- a/.env.models.json\n+++ b/.env.models.json\n@@ -1 +1 @@\n-old\n+new\n",
        "plain text pretending to be a patch",
    ],
)
def test_upgrade_patch_targets_must_match_checked_source(ready, database, invocation, patch):
    path = "frontend/src/scanner/Scanner.tsx"
    digest = assistant.file_hash(assistant.source_path(path))
    proposal = {
        **note(),
        "files": [{"path": path, "sha256": digest}],
        "changes": ["Review a scanner change"],
        "patch": patch,
    }
    assert action(ready, invocation, "prepare_upgrade", proposal).status_code == 422
    assert counts(database) == (0, 0, 0)


def test_performance_read_preserves_its_closed_trade_basis(ready, invocation, setup):
    result = ready.post(
        READ,
        headers=HEADERS,
        json={**invocation, "resource": "performance", "resource_id": setup[0]["id"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["basis"] == "closed_paper_trades"
    assert len(result.json()["accounts"]) == 1
    assert (
        ready.post(
            READ,
            headers=HEADERS,
            json={**invocation, "resource": "performance", "resource_id": str(uuid4())},
        ).status_code
        == 404
    )
