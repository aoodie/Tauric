"""Release authority requires scoped tasks and independently signed outcomes."""

import base64
import hashlib
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_chat import ready as ready
from test_chat import send

from tauric import agent_tasks, paper, release_jobs
from tauric.agent_contracts import JobReference, TaskControl, TaskCreate
from tauric.agent_models import AgentTask, EngineeringJob
from tauric.config import runtime_config
from tauric.models import AssistantRecord, Owner, now
from tauric.release_contracts import ReleaseInvocation


@pytest.fixture
def release_fixture(database, monkeypatch, tmp_path):
    signing = Ed25519PrivateKey.generate()
    public = signing.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    for name in ("engineering", "release"):
        root = tmp_path / name
        for sub in ("requests", "receipts", "public"):
            (root / sub).mkdir(parents=True)
        (root / "public/public.pem").write_bytes(public)
    engineering_root = tmp_path / "engineering"
    (engineering_root / "public/identity.json").write_text(
        json.dumps({"images": {"sha256:" + "a" * 64: "b" * 40}})
    )
    monkeypatch.setenv(
        "TAURIC_ENGINEERING_PUBLIC_KEY_FILE", str(engineering_root / "public/public.pem")
    )
    monkeypatch.setenv("TAURIC_ENGINEERING_SPOOL_DIRECTORY", str(engineering_root))
    monkeypatch.setenv("TAURIC_RELEASE_SPOOL_DIRECTORY", str(tmp_path / "release"))
    runtime_config.cache_clear()
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        task = agent_tasks.create(
            db,
            owner,
            TaskCreate(
                request_id=uuid4(),
                objective="Develop, test and deploy a bounded update",
                criteria=[
                    {
                        "description": "Installed services and source verified",
                        "proof": "deployment_verified",
                    }
                ],
            ),
        )
        inputs = {
            "workspace_id": task["root_id"],
            "task_id": task["id"],
            "script": "node --test scripts/tests/example.mjs",
            "purpose": "test",
            "timeout_seconds": 120,
        }
        job = EngineeringJob(
            id=str(uuid4()),
            owner_id=owner.id,
            task_id=task["id"],
            request_key="real-test",
            inputs=inputs,
            checksum=paper.checksum(inputs),
            status="queued",
            result={},
        )
        db.add(job)
        db.commit()
        bundle = {
            "job_id": job.id,
            "workspace_id": task["root_id"],
            "image_id": "sha256:" + "a" * 64,
            "tree_checksum": "c" * 64,
            "files": {"docs/release-example.md": "Verified source\n"},
            "deleted": [],
            "base_files": {"docs/release-example.md": None},
        }
        source = engineering_root / "receipts" / (task["root_id"] + "-" + job.id + ".source.json")
        source.write_text(json.dumps(bundle))
        result = {
            "id": job.id,
            "workspace_id": task["root_id"],
            "image_id": bundle["image_id"],
            "request_checksum": job.checksum,
            "exit_code": 0,
            "test_runner": True,
            "tests_executed": 2,
            "source_tree_checksum": bundle["tree_checksum"],
            "source_artifact_checksum": hashlib.sha256(
                json.dumps(bundle, sort_keys=True).encode()
            ).hexdigest(),
        }

        def signed(path, value):
            path.write_text(
                json.dumps(
                    {
                        "result": value,
                        "signature": base64.b64encode(
                            signing.sign(json.dumps(value, sort_keys=True).encode())
                        ).decode(),
                    }
                )
            )

        signed(engineering_root / "receipts" / (job.id + ".json"), result)
        body = ReleaseInvocation(
            task_id=task["id"],
            test_job_id=job.id,
            effect_key="same-release",
            agent_id="tauric-chat",
            channel="webchat",
            sender_is_owner=True,
            account_id="default",
            session_key="fixture",
            tool_call_id="first-call",
        )
        yield db, owner, task, job, body, tmp_path, signed
    runtime_config.cache_clear()


def test_release_reconciles_same_identity_without_requeueing_other_source(release_fixture):
    db, owner, task, job, body, root, _ = release_fixture
    first = release_jobs.enqueue(db, owner, None, body)
    repeated = release_jobs.enqueue(
        db, owner, None, body.model_copy(update={"tool_call_id": "different-call"})
    )
    assert first == repeated and first["status"] == "queued"
    rows = list(
        db.scalars(
            select(AssistantRecord).where(AssistantRecord.operation == release_jobs.OPERATION)
        )
    )
    assert len(rows) == 1
    request = root / "release/requests" / (first["id"] + ".json")
    assert json.loads(request.read_text()) == {"id": first["id"], "checksum": rows[0].checksum}
    request.unlink()
    release_jobs.observe(db, owner, first["id"])
    assert request.exists()
    with pytest.raises(HTTPException, match="different source"):
        release_jobs.enqueue(db, owner, None, body.model_copy(update={"test_job_id": uuid4()}))


@pytest.mark.parametrize(
    "change", ["scope", "paused", "expired", "foreign_source", "missing_bridge", "failed_test"]
)
def test_release_rejects_missing_authority_or_evidence_before_persisting(release_fixture, change):
    db, owner, task, job, body, root, signed = release_fixture
    row = db.get(AgentTask, task["id"])
    if change == "scope":
        row.scope = ["engineering"]
    elif change == "paused":
        row.status = "paused"
    elif change == "expired":
        row.deadline = now() - timedelta(seconds=1)
    elif change == "foreign_source":
        other = agent_tasks.create(
            db,
            owner,
            TaskCreate(
                request_id=uuid4(),
                objective="A separate project goal",
                criteria=[{"description": "A command passes", "proof": "command_success"}],
            ),
        )
        job.task_id = other["id"]
    elif change == "missing_bridge":
        (root / "release/public/public.pem").unlink()
    else:
        path = root / "engineering/receipts" / (job.id + ".json")
        result = json.loads(path.read_text())["result"]
        signed(path, {**result, "exit_code": 1})
    db.commit()
    with pytest.raises(HTTPException):
        release_jobs.enqueue(db, owner, None, body)
    assert not db.scalar(
        select(AssistantRecord.id).where(AssistantRecord.operation == release_jobs.OPERATION)
    )
    assert not list((root / "release/requests").glob("*.json"))


def test_host_authorization_observes_pause_resume_cancel_and_deadline(release_fixture):
    db, owner, task, _, body, _, _ = release_fixture
    queued = release_jobs.enqueue(db, owner, None, body)
    intent = release_jobs.intent(db, owner, queued["id"])
    assert release_jobs.authorization(db, owner, intent)["status"] == "active"
    for action, status in (("pause", "paused"), ("resume", "active"), ("cancel", "cancelled")):
        row = db.get(AgentTask, task["id"])
        agent_tasks.control(
            db,
            owner,
            row.id,
            TaskControl(
                action=action,
                expected_revision=row.revision,
                reason="Owner controls this original goal",
            ),
        )
        assert release_jobs.authorization(db, owner, intent)["status"] == status


def test_only_signed_installed_outcomes_can_prove_completion(release_fixture):
    db, owner, task, _, body, root, signed = release_fixture
    job = release_jobs.enqueue(db, owner, None, body)
    ref = JobReference(kind="release", id=job["id"])
    assert agent_tasks.reference(db, owner, ref)["proofs"] == []
    path = root / "release/receipts" / (job["id"] + ".json")
    result = {
        "id": job["id"],
        "task_id": task["id"],
        "request_checksum": job["input_checksum"],
        "status": "succeeded",
        "deployment_verified": False,
    }
    signed(path, result)
    with pytest.raises(HTTPException, match="signature or identity"):
        agent_tasks.reference(db, owner, ref)

    result["deployment_verified"] = True
    signed(path, result)
    assert agent_tasks.reference(db, owner, ref)["proofs"] == ["deployment_verified"]
    envelope = json.loads(path.read_text())
    envelope["result"]["request_checksum"] = "d" * 64
    path.write_text(json.dumps(envelope))
    with pytest.raises(HTTPException, match="signature or identity"):
        agent_tasks.reference(db, owner, ref)


def test_release_endpoint_binds_current_owner_and_filters_extra_authority(ready, release_fixture):
    db, owner, _, _, body, _, _ = release_fixture
    turn = send(ready, "Deploy the existing tested project change").json()
    assert agent_tasks.chat.claim(db)
    identity = body.model_dump(mode="json")
    identity["session_key"] = (
        f"agent:tauric-chat:openai-user:tauric-web-{owner.id}-{turn['conversation_id']}"
    )
    headers = {"Authorization": "Bearer fixture-private-" + "x" * 50}
    assert ready.post("/internal/openclaw/release", json=identity).status_code == 401
    for change, code in (
        ({"sender_is_owner": False}, 403),
        ({"script": "forbidden host command"}, 422),
    ):
        assert (
            ready.post(
                "/internal/openclaw/release", headers=headers, json={**identity, **change}
            ).status_code
            == code
        )
    response = ready.post("/internal/openclaw/release", headers=headers, json=identity)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "queued" and response.json()["financial_authority"] is False
