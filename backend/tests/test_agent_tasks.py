"""Durable project supervision cannot turn receipts or restarts into authority."""

import base64
import hashlib
import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_chat import ready as ready
from test_chat import send

from tauric import agent_tasks, engineering, paper
from tauric.agent_contracts import (
    EngineeringInvocation,
    JobReference,
    TaskComplete,
    TaskControl,
    TaskCreate,
    TaskInvocation,
)
from tauric.agent_models import AgentTask, AgentTaskWake, EngineeringJob
from tauric.config import runtime_config
from tauric.models import AnalysisJob, ChatTurn, Owner, now


def create(db, owner, **extra):
    return agent_tasks.create(
        db,
        owner,
        TaskCreate(
            request_id=uuid4(),
            objective="Verify a bounded development task",
            criteria=[{"description": "Actual test command passes", "proof": "tests_passed"}],
            **extra,
        ),
    )


def invocation(task_id, action, parameters):
    return TaskInvocation(
        action=action,
        task_id=task_id,
        parameters=parameters,
        agent_id="tauric-chat",
        channel="webchat",
        sender_is_owner=True,
        account_id="default",
        session_key="fixture",
        tool_call_id="fixture",
    )


def test_queued_command_is_not_completed_evidence(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = create(db, owner)
        row = EngineeringJob(
            id=str(uuid4()),
            owner_id=owner.id,
            task_id=task["id"],
            request_key="initial",
            inputs={},
            checksum=paper.checksum({}),
            result={},
            status="queued",
        )
        db.add(row)
        db.commit()
        proof = agent_tasks.reference(db, owner, JobReference(kind="engineering", id=row.id))
        assert proof["status"] == "queued" and proof["proofs"] == []
        with pytest.raises(HTTPException, match="criterion"):
            agent_tasks.invoke(
                db,
                owner,
                None,
                invocation(
                    task["id"],
                    "complete",
                    TaskComplete(
                        evidence=[{"kind": "engineering", "id": row.id}],
                        summary="A submission is not a completed test",
                    ).model_dump(mode="json"),
                ),
            )


def test_api_spooled_request_passes_real_host_integrity_validation(database, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    import engineering_host

    public = tmp_path / "public.pem"
    public.write_text("Presence is sufficient for the API handoff; execution validates the host key")
    public.with_name("identity.json").write_text('{"images":{}}')
    (tmp_path / "requests").mkdir()
    monkeypatch.setenv("TAURIC_ENGINEERING_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("TAURIC_ENGINEERING_SPOOL_DIRECTORY", str(tmp_path))
    runtime_config.cache_clear()

    class IntegrityPassed(Exception):
        pass

    def stop_before_docker(*_):
        raise IntegrityPassed()

    monkeypatch.setattr(engineering_host, "docker", stop_before_docker)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = create(db, owner)
        job = engineering.enqueue(
            db,
            owner,
            None,
            EngineeringInvocation(
                task_id=task["id"], effect_key="real-api-host-handoff", script="printf 'actual request\\n'",
                purpose="inspect", agent_id="tauric-chat", channel="webchat", sender_is_owner=True,
                account_id="default", session_key="handoff", tool_call_id="handoff",
            ),
        )
        request = tmp_path / "requests" / (job["id"] + ".json")
        with pytest.raises(IntegrityPassed):
            engineering_host.process(request, tmp_path, "sha256:" + "a" * 64, tmp_path / "host-key")
    runtime_config.cache_clear()


def test_signed_actual_result_and_tampered_receipt(database, tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    public = tmp_path / "public.pem"
    public.write_bytes(
        key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )
    (tmp_path / "identity.json").write_text(
        json.dumps({"images": {"sha256:" + "a" * 64: "b" * 40}})
    )
    monkeypatch.setenv("TAURIC_ENGINEERING_PUBLIC_KEY_FILE", str(public))
    monkeypatch.setenv("TAURIC_ENGINEERING_SPOOL_DIRECTORY", str(tmp_path))
    runtime_config.cache_clear()
    (tmp_path / "receipts").mkdir()
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = create(db, owner)
        inputs = {"purpose": "test", "workspace_id": task["root_id"]}
        row = EngineeringJob(
            id=str(uuid4()),
            owner_id=owner.id,
            task_id=task["id"],
            request_key="test",
            inputs=inputs,
            checksum=paper.checksum(inputs),
            result={},
            status="queued",
        )
        db.add(row)
        db.commit()
        result = {
            "id": row.id,
            "request_checksum": row.checksum,
            "workspace_id": task["root_id"],
            "image_id": "sha256:" + "a" * 64,
            "exit_code": 0,
            "test_runner": True,
            "tests_executed": 2,
        }
        bundle = {
            "job_id": row.id,
            "workspace_id": task["root_id"],
            "image_id": result["image_id"],
            "tree_checksum": "c" * 64,
            "files": {"scripts/example.py": "assert True\n"},
            "deleted": [],
            "base_files": {"scripts/example.py": None},
        }
        source = tmp_path / "receipts" / (task["root_id"] + "-" + row.id + ".source.json")
        source.write_text(json.dumps(bundle))
        result["source_tree_checksum"] = bundle["tree_checksum"]
        result["source_artifact_checksum"] = hashlib.sha256(
            json.dumps(bundle, sort_keys=True).encode()
        ).hexdigest()
        signature = base64.b64encode(key.sign(json.dumps(result, sort_keys=True).encode())).decode()
        receipt = tmp_path / "receipts" / (row.id + ".json")
        receipt.write_text(
            json.dumps({"result": {**result, "exit_code": 1}, "signature": signature})
        )
        with pytest.raises(HTTPException, match="signature"):
            engineering.observe(db, owner, row.id)
        receipt.write_text(json.dumps({"result": result, "signature": signature}))
        assert engineering.source(db, owner, row.id)["files"] == bundle["files"]
        source.write_text(json.dumps({**bundle, "files": {"scripts/example.py": "assert False\n"}}))
        with pytest.raises(HTTPException, match="signed binding"):
            engineering.source(db, owner, row.id)
        source.write_text(json.dumps(bundle))
        done = agent_tasks.invoke(
            db,
            owner,
            None,
            invocation(
                task["id"],
                "complete",
                {
                    "evidence": [{"kind": "engineering", "id": row.id}],
                    "summary": "The separately observed test command passed",
                },
            ),
        )
        assert done["status"] == "completed"
        assert done["result"]["verified_evidence"][0]["proofs"] == [
            "command_success",
            "tests_passed",
        ]
    runtime_config.cache_clear()


def test_pause_propagates_and_stale_wake_loses_effect_authority(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        root = create(db, owner)
        child = create(db, owner, parent_id=root["id"])
        task = db.get(AgentTask, child["id"])
        task.status = "running"
        turn = ChatTurn(
            id=str(uuid4()),
            owner_id=owner.id,
            request_id=str(uuid4()),
            conversation_id=task.conversation_id,
            user_text="Authorized continuation",
            status="running",
            heartbeat_at=now(),
            profile="local",
            routing_fingerprint="a" * 64,
        )
        db.add(turn)
        db.flush()
        db.add(AgentTaskWake(turn_id=turn.id, task_id=task.id, task_revision=task.revision))
        db.commit()
        assert agent_tasks.for_turn(db, turn, effect=True, operation="run_backtest").id == task.id
        with pytest.raises(HTTPException, match="financial"):
            agent_tasks.for_turn(db, turn, effect=True, operation="save_broker_policy")
        agent_tasks.control(
            db,
            owner,
            root["id"],
            TaskControl(
                action="pause", expected_revision=root["revision"], reason="Owner paused this work"
            ),
        )
        with pytest.raises(HTTPException, match="stopped"):
            agent_tasks.for_turn(db, turn, effect=True, operation="run_backtest")
        assert db.get(AgentTask, child["id"]).status == "paused"
        resumed = agent_tasks.control(
            db,
            owner,
            root["id"],
            TaskControl(action="resume", expected_revision=2, reason="Resume only the root task"),
        )
        assert resumed["status"] == "queued"
        assert db.get(AgentTask, child["id"]).status == "queued"


def test_children_inherit_scope_deadline_and_budget(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        root = create(db, owner, scope=["research"], max_wakes=2, max_attempts=1)
        with pytest.raises(HTTPException, match="broaden"):
            create(db, owner, parent_id=root["id"], scope=["engineering"])
        db.rollback()
        child = create(
            db,
            owner,
            parent_id=root["id"],
            scope=["research"],
            max_wakes=40,
            max_attempts=5,
            deadline_hours=168,
        )
        assert child["max_wakes"] == 2 and child["max_attempts"] == 1
        assert child["deadline"] == root["deadline"]


def test_finished_wake_is_followed_after_supervisor_restart(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = create(db, owner, max_attempts=1)
        row = db.get(AgentTask, task["id"])
        row.status = "running"
        turn = ChatTurn(
            id=str(uuid4()),
            owner_id=owner.id,
            request_id=str(uuid4()),
            conversation_id=row.conversation_id,
            user_text="Continue this existing goal",
            status="interrupted",
            heartbeat_at=now() - timedelta(minutes=2),
            profile="local",
            routing_fingerprint="a" * 64,
        )
        db.add(turn)
        db.flush()
        db.add(AgentTaskWake(turn_id=turn.id, task_id=row.id, task_revision=1))
        db.commit()
        turn_id = turn.id
    with Session(database[0]) as resumed:
        assert agent_tasks.tick(resumed)
        row = resumed.get(AgentTask, task["id"])
        assert row.status == "requires_owner" and row.attempts == 1
        assert resumed.get(AgentTaskWake, turn_id).processed


def test_unavailable_saved_chat_configuration_uses_persisted_recovery_budget(database, monkeypatch):
    def unavailable(*_):
        raise HTTPException(503, "Chat settings cannot be unlocked.")

    monkeypatch.setattr(agent_tasks.chat, "config_view", unavailable)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = create(db, owner, max_attempts=1)
        assert agent_tasks.tick(db)
        row = db.get(AgentTask, task["id"])
        assert row.attempts == 1 and "connection unavailable" in row.error.lower()
        assert row.next_check_at > now()
        assert not db.scalar(select(ChatTurn.id))
        row.next_check_at = now() - timedelta(seconds=1)
        db.commit()
    with Session(database[0]) as resumed:
        assert agent_tasks.tick(resumed)
        row = resumed.get(AgentTask, task["id"])
        assert row.status == "requires_owner" and row.attempts == 1
        assert "budget" in row.error.lower()
        assert not resumed.scalar(select(ChatTurn.id))


def test_deadline_and_unfinished_child_prevent_false_completion(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        root = create(db, owner)
        row = db.get(AgentTask, root["id"])
        row.deadline = now() - timedelta(seconds=1)
        db.commit()
        assert agent_tasks.tick(db)
        assert row.status == "requires_owner"
        assert "deadline" in row.error


def test_cancellation_is_terminal(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = create(db, owner)
        agent_tasks.control(
            db,
            owner,
            task["id"],
            TaskControl(action="cancel", expected_revision=1, reason="Owner cancelled this goal"),
        )
        with pytest.raises(HTTPException, match="cancelled"):
            agent_tasks.control(
                db,
                owner,
                task["id"],
                TaskControl(
                    action="resume", expected_revision=2, reason="Do not resurrect cancellation"
                ),
            )


def test_original_analysis_writer_is_accepted_as_completed_evidence(database):
    from tauric import analysis

    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        job = AnalysisJob(
            id=str(uuid4()),
            owner_id=owner.id,
            request_id=str(uuid4()),
            instrument_id="oanda:fx:EUR_USD",
            profile="local",
            request={},
            status="running",
        )
        db.add(job)
        db.commit()
        assert analysis.finish(
            db,
            job.id,
            payload={
                "reports": {"final_trade_decision": "Explicit offline fixture; no market claim."},
                "execution_authority": False,
            },
        )
        proof = agent_tasks.reference(db, owner, JobReference(kind="analysis", id=job.id))
        assert proof["proofs"] == ["job_success", "research_report"]


def test_explicit_child_pause_survives_parent_resume(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        root = create(db, owner)
        child = create(db, owner, parent_id=root["id"])
        agent_tasks.control(
            db,
            owner,
            child["id"],
            TaskControl(action="pause", expected_revision=1, reason="Owner paused just this child"),
        )
        agent_tasks.control(
            db,
            owner,
            root["id"],
            TaskControl(action="pause", expected_revision=1, reason="Pause the parent goal"),
        )
        agent_tasks.control(
            db,
            owner,
            root["id"],
            TaskControl(action="resume", expected_revision=2, reason="Resume inherited work only"),
        )
        assert db.get(AgentTask, child["id"]).status == "paused"


def test_task_owner_controls_require_login_and_csrf(signed_in):
    response = signed_in.post(
        "/api/v1/assistant/tasks",
        json={
            "request_id": str(uuid4()),
            "objective": "Owner-authorized offline test task",
            "criteria": [{"description": "An actual command succeeds", "proof": "command_success"}],
        },
    )
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["executor"] == "tauric-chat"
    signed_in.headers.pop("X-CSRF-Token")
    assert (
        signed_in.post(
            f"/api/v1/assistant/tasks/{task['id']}/control",
            json={"action": "pause", "expected_revision": 1, "reason": "No CSRF authorization"},
        ).status_code
        == 403
    )
    signed_in.cookies.clear()
    assert signed_in.get("/api/v1/assistant/tasks").status_code == 401


def test_child_creation_reconciles_stable_effect_across_tool_calls(database):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        root = create(db, owner, scope=["engineering"])
        row = db.get(AgentTask, root["id"])
        row.status = "running"
        turn = ChatTurn(
            id=str(uuid4()),
            owner_id=owner.id,
            request_id=str(uuid4()),
            conversation_id=row.conversation_id,
            user_text="Continue the authorized goal",
            status="running",
            heartbeat_at=now(),
            profile="local",
            routing_fingerprint="a" * 64,
        )
        db.add(turn)
        db.flush()
        db.add(AgentTaskWake(turn_id=turn.id, task_id=row.id, task_revision=row.revision))
        db.commit()
        body = invocation(
            None,
            "create",
            {
                "objective": "Run the independently checked child",
                "criteria": [
                    {"description": "An actual command succeeds", "proof": "command_success"}
                ],
            },
        ).model_copy(update={"effect_key": "bounded-child"})
        # A malformed model argument must be correctable without consuming the
        # stable identity or creating a child with partial inputs.
        with pytest.raises(HTTPException) as rejected:
            agent_tasks.invoke(
                db, owner, turn,
                body.model_copy(update={"parameters": {**body.parameters, "reason": "private-input"}}),
            )
        assert rejected.value.status_code == 422
        assert "reason: Extra inputs" in rejected.value.detail
        assert "private-input" not in rejected.value.detail
        assert not db.scalar(select(AgentTask.id).where(AgentTask.parent_id == root["id"]))
        first = agent_tasks.invoke(db, owner, turn, body)
        second = agent_tasks.invoke(
            db, owner, turn, body.model_copy(update={"tool_call_id": "new-call"})
        )
        assert first["id"] == second["id"] and first["root_id"] == root["id"]
        assert first["scope"] == ["engineering"]
        with pytest.raises(HTTPException, match="stable effect_key"):
            agent_tasks.invoke(db, owner, turn, body.model_copy(update={"effect_key": None}))


@pytest.mark.parametrize(
    "action,parameters,field",
    [
        ("create", {"objective": "A bounded child", "criteria": [{"description": "Run tests", "proof": "tests_passed"}], "reason": "private-input"}, "reason"),
        ("wait", {"job": {"kind": "engineering", "id": "private-input"}, "reason": "Follow original job"}, "job.id"),
        ("complete", {"evidence": [], "summary": {"secret": "private-input"}}, "summary"),
    ],
)
def test_nested_tool_validation_returns_json_without_effects(ready, database, action, parameters, field):
    turn = send(ready, "Run a bounded engineering task").json()
    with Session(database[0]) as db:
        chat_turn = agent_tasks.chat.claim(db)
        assert chat_turn
        owner = db.scalar(select(Owner))
        task = create(db, owner)
        owner_id = owner.id
    response = ready.post(
        "/internal/openclaw/task",
        headers={"Authorization": "Bearer fixture-private-" + "x" * 50},
        json={
            "action": action, "task_id": task["id"], "parameters": parameters,
            "effect_key": "correctable-effect", "channel": "webchat", "agent_id": "tauric-chat",
            "sender_is_owner": True, "account_id": "default", "tool_call_id": "bad-fields",
            "session_key": f"agent:tauric-chat:openai-user:tauric-web-{owner_id}-{turn['conversation_id']}",
        },
    )
    assert response.status_code == 422, response.text
    assert field in response.json()["detail"]
    assert "private-input" not in response.text
    with Session(database[0]) as db:
        assert list(db.scalars(select(AgentTask.id))) == [task["id"]]
        row = db.get(AgentTask, task["id"])
        assert row.status == "queued" and row.revision == 1 and not row.result and not row.waiting
        assert not db.scalar(select(EngineeringJob.id))


def test_async_research_observation_survives_a_supervisor_restart(database, monkeypatch):
    from tauric import analysis, chat

    monkeypatch.setattr(
        chat,
        "config_view",
        lambda *_: SimpleNamespace(gateway_ready=False, route_ready=False, worker_ready=False),
    )
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = agent_tasks.create(
            db,
            owner,
            TaskCreate(
                request_id=uuid4(),
                objective="Follow the original research job to its artifact",
                criteria=[
                    {"description": "The original report is available", "proof": "research_report"}
                ],
            ),
        )
        job = AnalysisJob(
            id=str(uuid4()),
            owner_id=owner.id,
            request_id=str(uuid4()),
            instrument_id="oanda:fx:EUR_USD",
            profile="local",
            request={},
            status="running",
        )
        db.add(job)
        db.commit()
        job_id, owner_id = job.id, owner.id
        agent_tasks.invoke(
            db,
            owner,
            None,
            invocation(
                task["id"],
                "wait",
                {
                    "job": {"kind": "analysis", "id": job.id},
                    "reason": "Observe the existing asynchronous job",
                },
            ),
        )
        db.get(AgentTask, task["id"]).next_check_at = now()
        db.commit()
        assert not agent_tasks.tick(db)
        assert db.get(AgentTask, task["id"]).status == "waiting"
    with Session(database[0]) as restarted:
        assert analysis.finish(
            restarted,
            job_id,
            payload={
                "reports": {"final_trade_decision": "Offline acceptance artifact"},
                "execution_authority": False,
            },
        )
        restarted.get(AgentTask, task["id"]).next_check_at = now()
        restarted.commit()
        assert agent_tasks.tick(restarted)
        row = restarted.get(AgentTask, task["id"])
        assert row.status == "queued"
        assert row.result["last_observation"]["id"] == job_id
        assert row.result["last_observation"]["proofs"] == ["job_success", "research_report"]
        owner = restarted.get(Owner, owner_id)
        done = agent_tasks.invoke(
            restarted,
            owner,
            None,
            invocation(
                task["id"],
                "complete",
                {
                    "evidence": [{"kind": "analysis", "id": job_id}],
                    "summary": "The original artifact was independently observed after restart",
                },
            ),
        )
        assert done["status"] == "completed"
