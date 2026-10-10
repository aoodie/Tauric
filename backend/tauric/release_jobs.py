"""Durable release intents and independently signed operations results.

Application containers can queue bounded identities and read public receipts.
Only the separately installed root bridge holds repository and signing keys.
"""

import argparse
import base64
import json
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import HTTPException
from sqlalchemy import func, select

from . import agent_tasks, engineering, paper
from .agent_contracts import JobReference
from .agent_models import AgentTask, EngineeringJob
from .config import runtime_config
from .models import AssistantRecord, Owner, now

OPERATION = "project_release"
STAGE = "requested"


def paths():
    root = Path(runtime_config().release_spool_directory)
    return root / "requests", root / "receipts", root / "public" / "public.pem"


def intent(db, owner, job_id):
    row = db.get(AssistantRecord, str(job_id))
    if (
        not row
        or row.owner_id != owner.id
        or row.operation != OPERATION
        or row.stage != STAGE
        or paper.checksum(row.payload) != row.checksum
    ):
        raise HTTPException(404, "Release job is missing or failed its integrity check.")
    return row


def authorization(db, owner, row):
    """Recheck current owner controls independently of the originating chat lease."""
    task = agent_tasks.owned(db, owner, row.payload["task_id"])
    root = agent_tasks.owned(db, owner, task.root_id)
    source = db.get(EngineeringJob, row.payload["test_job_id"])
    if not source or source.owner_id != owner.id:
        raise HTTPException(403, "Release source does not belong to this owner.")
    source_task = agent_tasks.owned(db, owner, source.task_id)
    if source_task.root_id != task.root_id or paper.checksum(source.inputs) != source.checksum:
        raise HTTPException(403, "Release evidence must belong to the same project goal.")
    status = "active"
    if task.status == "cancelled" or root.status == "cancelled":
        status = "cancelled"
    elif task.status in agent_tasks.TERMINAL or root.status in agent_tasks.TERMINAL:
        status = "finished"
    elif task.deadline <= now() or root.deadline <= now():
        status = "expired"
    elif task.status in {"paused", "requires_owner"} or root.status in {"paused", "requires_owner"}:
        status = "paused"
    if not {"engineering", "operations"} <= set(task.scope):
        raise HTTPException(403, "A release requires engineering and operations scope.")
    return {
        "id": row.id,
        "checksum": row.checksum,
        "inputs": row.payload,
        "task_id": task.id,
        "root_id": root.id,
        "status": status,
        "deadline": min(task.deadline, root.deadline).isoformat(),
        "engineering_request_checksum": source.checksum,
        "engineering_workspace_id": source.inputs["workspace_id"],
    }


def enqueue(db, owner, turn, body):
    current = agent_tasks.for_turn(db, turn, effect=True)
    db.execute(select(func.pg_advisory_xact_lock(913570080)))
    task = agent_tasks.owned(db, owner, body.task_id)
    agent_tasks.owned(db, owner, task.root_id, lock=True)
    task = agent_tasks.owned(db, owner, task.id, lock=True)
    if current and current.id != task.id:
        raise HTTPException(403, "Release work must belong to the current project task.")
    payload = {"task_id": task.id, "test_job_id": str(body.test_job_id)}
    job_id = str(uuid5(NAMESPACE_URL, "tauric-release:" + task.id + ":" + body.effect_key))
    old = db.get(AssistantRecord, job_id)
    if old:
        old = intent(db, owner, job_id)
        if old.payload != payload:
            raise HTTPException(409, "This release identity is bound to different source inputs.")
        return observe(db, owner, old.id)
    candidate = AssistantRecord(
        id=job_id,
        request_id=job_id,
        owner_id=owner.id,
        turn_id=turn.id if turn else None,
        operation=OPERATION,
        stage=STAGE,
        title="Verified project release",
        payload=payload,
        checksum=paper.checksum(payload),
    )
    access = authorization(db, owner, candidate)
    if access["status"] != "active" or task.status not in {"queued", "running"}:
        raise HTTPException(403, "This project goal does not authorize a new release.")
    proof = agent_tasks.reference(db, owner, JobReference(kind="engineering", id=body.test_job_id))
    if "tests_passed" not in proof["proofs"]:
        raise HTTPException(409, "A release requires signed successful executed tests.")
    source = engineering.source(db, owner, body.test_job_id)
    if not source.get("files") and not source.get("deleted"):
        raise HTTPException(422, "The tested workspace contains no source change to release.")
    requests, _, public = paths()
    if not requests.is_dir() or not public.is_file():
        raise HTTPException(503, "The fixed operations release bridge is not installed.")
    count = db.scalar(
        select(func.count())
        .select_from(AssistantRecord)
        .where(
            AssistantRecord.operation == OPERATION,
            AssistantRecord.stage == STAGE,
            AssistantRecord.owner_id == owner.id,
            AssistantRecord.payload["task_id"]
            .as_string()
            .in_(select(AgentTask.id).where(AgentTask.root_id == task.root_id)),
        )
    )
    if count >= 3:
        raise HTTPException(409, "This goal's shared release allowance is exhausted.")
    db.add(candidate)
    agent_tasks.event(
        db, task, "release_queued", {"job_id": job_id, "test_job_id": str(body.test_job_id)}
    )
    db.commit()
    engineering.atomic(
        requests / (job_id + ".json"), {"id": job_id, "checksum": candidate.checksum}
    )
    return observe(db, owner, job_id)


def observe(db, owner, job_id):
    row = intent(db, owner, job_id)
    requests, receipts, public = paths()
    path = receipts / (row.id + ".json")
    result = {
        "id": row.id,
        "task_id": row.payload["task_id"],
        "status": "queued",
        "input_checksum": row.checksum,
        "result": {},
        "financial_authority": False,
    }
    if not path.exists():
        access = authorization(db, owner, row)
        if access["status"] == "active" and requests.is_dir():
            request = requests / (row.id + ".json")
            if not request.exists():
                engineering.atomic(request, {"id": row.id, "checksum": row.checksum})
        return result
    try:
        if path.stat().st_size > 100000:
            raise ValueError("Receipt limit")
        envelope = json.loads(path.read_text())
        value = envelope["result"]
        key = load_pem_public_key(public.read_bytes())
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("Signing identity")
        key.verify(
            base64.b64decode(envelope["signature"], validate=True),
            json.dumps(value, sort_keys=True).encode(),
        )
        if (
            value.get("id") != row.id
            or value.get("request_checksum") != row.checksum
            or value.get("task_id") != row.payload["task_id"]
            or value.get("status") not in {"queued", "running", "succeeded", "failed", "cancelled"}
            or value.get("status") == "succeeded"
            and value.get("deployment_verified") is not True
        ):
            raise ValueError("Receipt identity")
    except Exception:
        raise HTTPException(409, "Release result signature or identity failed.") from None
    return {**result, "status": value["status"], "result": {**value, "receipt_verified": True}}


def main():
    """Fixed read-only host check; no command, SQL or path comes from source code."""
    from sqlalchemy.orm import Session

    from .database import engine

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["authorize"])
    parser.add_argument("job_id", type=UUID)
    parser.add_argument("checksum")
    args = parser.parse_args()
    with Session(engine()) as db:
        owner = db.scalar(select(Owner))
        row = intent(db, owner, args.job_id)
        if row.checksum != args.checksum:
            raise SystemExit("Release request integrity failed")
        print(json.dumps(authorization(db, owner, row), sort_keys=True))


if __name__ == "__main__":
    main()
