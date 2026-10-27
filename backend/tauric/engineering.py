"""Queue general code in a separate, credential-free execution environment."""

import base64
import hashlib
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import HTTPException
from sqlalchemy import func, select

from . import agent_tasks, paper
from .agent_models import AgentTask, EngineeringJob
from .config import runtime_config
from .models import now


def directories():
    root = Path(runtime_config().engineering_spool_directory)
    return root / "requests", root / "receipts", root / "cancel"


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    temporary.write_text(json.dumps(data, sort_keys=True))
    temporary.replace(path)


def enqueue(db, owner, turn, body):
    if body.purpose == "verify" and (body.script != "api_smoke" or body.timeout_seconds < 45):
        raise HTTPException(422, "Independent verification requires script=api_smoke and at least 45 seconds; initialize the workspace first.")
    current = agent_tasks.for_turn(db, turn, effect=True)
    db.execute(select(func.pg_advisory_xact_lock(913570080)))
    owned = agent_tasks.owned(db, owner, body.task_id)
    root = agent_tasks.owned(db, owner, owned.root_id, lock=True)
    task = agent_tasks.owned(db, owner, body.task_id, lock=True)
    if current and current.id != task.id:
        raise HTTPException(403, "Engineering work must belong to the active project task.")
    if (
        "engineering" not in task.scope
        or task.status not in {"queued", "running"}
        or root.status in agent_tasks.TERMINAL | {"paused", "requires_owner"}
        or task.deadline <= now()
    ):
        raise HTTPException(403, "This task does not authorize active engineering work.")
    values = {
        "script": body.script,
        "timeout_seconds": body.timeout_seconds,
        "purpose": body.purpose,
        "workspace_id": task.root_id,
        "task_id": task.id,
    }
    key = paper.checksum({"effect_key": body.effect_key})
    job_id = str(uuid5(NAMESPACE_URL, "tauric-engineering:" + task.id + ":" + key))
    row = db.get(EngineeringJob, job_id)
    if row:
        if row.inputs != values or row.checksum != paper.checksum(values):
            raise HTTPException(409, "This engineering effect key already has different inputs.")
        observe(db, owner, job_id)
        db.commit()
        return view(row)
    count = db.scalar(
        select(func.count())
        .select_from(EngineeringJob)
        .join(AgentTask, AgentTask.id == EngineeringJob.task_id)
        .where(AgentTask.root_id == task.root_id)
    )
    if count >= task.max_wakes * 4:
        raise HTTPException(409, "This goal's shared engineering allowance is exhausted.")
    requests, _, _ = directories()
    public = Path(runtime_config().engineering_public_key_file)
    if (
        not requests.is_dir()
        or not public.is_file()
        or not public.with_name("identity.json").is_file()
    ):
        raise HTTPException(503, "The isolated engineering executor is not installed.")
    row = EngineeringJob(
        id=job_id,
        owner_id=owner.id,
        task_id=task.id,
        request_key=key,
        inputs=values,
        checksum=paper.checksum(values),
        status="queued",
        result={},
    )
    db.add(row)
    agent_tasks.event(db, task, "engineering_queued", {"job_id": row.id, "checksum": row.checksum})
    db.commit()  # Persist identity before handing off; observation repairs an interrupted handoff.
    atomic(requests / (row.id + ".json"), {"id": row.id, "checksum": row.checksum, **values})
    return view(row)


def observe(db, owner, job_id):
    row = db.get(EngineeringJob, str(job_id))
    if not row or row.owner_id != owner.id or paper.checksum(row.inputs) != row.checksum:
        raise HTTPException(404, "Engineering job is missing or failed its integrity check.")
    requests, receipts, cancel = directories()
    task = agent_tasks.owned(db, owner, row.task_id)
    root = agent_tasks.owned(db, owner, task.root_id)
    stopped = (
        task.status in agent_tasks.TERMINAL | {"paused", "requires_owner"}
        or root.status in agent_tasks.TERMINAL | {"paused", "requires_owner"}
        or task.deadline <= now()
    )
    if stopped and row.status == "queued":
        atomic(cancel / (row.id + ".json"), {"id": row.id})
    path = receipts / (row.id + ".json")
    if not path.is_file():
        if row.status in {"succeeded", "failed", "cancelled"}:
            raise HTTPException(409, "The signed engineering receipt is unavailable.")
        if not stopped and requests.is_dir() and not (requests / (row.id + ".json")).exists():
            atomic(
                requests / (row.id + ".json"),
                {"id": row.id, "checksum": row.checksum, **row.inputs},
            )
        return row
    if path.stat().st_size > 100000:
        raise HTTPException(409, "Engineering receipt exceeds its limit.")
    try:
        envelope = json.loads(path.read_text())
        result = envelope["result"]
        public = load_pem_public_key(
            Path(runtime_config().engineering_public_key_file).read_bytes()
        )
        public.verify(
            base64.b64decode(envelope["signature"], validate=True),
            json.dumps(result, sort_keys=True).encode(),
        )
        identities = json.loads(
            Path(runtime_config().engineering_public_key_file)
            .with_name("identity.json")
            .read_text()
        )["images"]
        if (
            result["id"] != row.id
            or result["request_checksum"] != row.checksum
            or result["workspace_id"] != row.inputs["workspace_id"]
            or not result["image_id"].startswith("sha256:")
            or result["image_id"] not in identities
            or type(result["exit_code"]) is not int
        ):
            raise ValueError("Engineering receipt binding mismatch")
    except Exception:
        raise HTTPException(409, "Engineering receipt signature or identity failed.") from None
    row.result = {
        **result,
        "receipt_verified": True,
        "source_revision": identities[result["image_id"]],
    }
    row.status = (
        "cancelled"
        if result.get("cancelled")
        else "succeeded"
        if result["exit_code"] == 0
        else "failed"
    )
    row.updated_at = now()
    db.flush()
    return row


def view(row):
    result = dict(row.result)
    if row.inputs.get("purpose") == "test":
        result.update(tests_reported=result.get("tests_reported", result.get("tests_executed", 0)),
                      tests_executed=0, test_runner=False, test_evidence="workspace_report_only")
    return {
        "id": row.id,
        "task_id": row.task_id,
        "status": row.status,
        "input_checksum": row.checksum,
        "result": result,
        "financial_authority": False,
        "runtime_code_changed": False,
    }


def source(db, owner, job_id):
    row = observe(db, owner, str(job_id))
    expected = row.result.get("source_artifact_checksum")
    if not expected or not row.result.get("receipt_verified"):
        raise HTTPException(409, "No verified source artifact is available for this execution.")
    _, receipts, _ = directories()
    path = receipts / (row.inputs["workspace_id"] + "-" + row.id + ".source.json")
    if not path.is_file() or path.stat().st_size > 16 * 1024**2:
        raise HTTPException(409, "The executed source artifact is unavailable or oversized.")
    bundle = json.loads(path.read_text())
    if (
        hashlib.sha256(json.dumps(bundle, sort_keys=True).encode()).hexdigest() != expected
        or bundle.get("job_id") != row.id
        or bundle.get("workspace_id") != row.inputs["workspace_id"]
        or bundle.get("tree_checksum") != row.result.get("source_tree_checksum")
        or bundle.get("image_id") != row.result.get("image_id")
    ):
        raise HTTPException(409, "The executed source artifact failed its signed binding check.")
    return {
        **bundle,
        "source_revision": row.result["source_revision"],
        "artifact_checksum": expected,
    }
