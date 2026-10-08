"""Durable, cancellable research queue. Run separately from trading workers."""

import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import paper, quant_features, quant_runtime
from .database import engine
from .models import WorkerStatus, now
from .quant_models import QuantRecord
from .research_models import ResearchRecord

VERSION = "quant-lab-v1"


def update(row, **values):
    row.payload = {**row.payload, **values}
    row.checksum = paper.checksum(row.payload)


def heartbeat(db, engine_name):
    row = db.get(WorkerStatus, "lab_" + engine_name)
    if row:
        row.heartbeat_at, row.version = now(), VERSION
    else:
        db.add(WorkerStatus(id="lab_" + engine_name, heartbeat_at=now(), version=VERSION))
    db.commit()


def claim(db, engine_name):
    # A lost lease is failed visibly, never silently re-run with potentially changed software.
    stale = db.scalars(
        select(QuantRecord)
        .where(
            QuantRecord.kind == "quant_job",
            QuantRecord.payload["engine"].as_string() == engine_name,
            QuantRecord.payload["status"].as_string() == "running",
        )
        .with_for_update(skip_locked=True)
    )
    for row in stale:
        lease = row.payload.get("lease_at")
        if lease and now() - quant_features.stamp(lease) > timedelta(minutes=2):
            update(
                row,
                status="failed",
                error="Research worker stopped; submit a new experiment",
                finished_at=now().isoformat(),
            )
    db.commit()
    row = db.scalar(
        select(QuantRecord)
        .where(
            QuantRecord.kind == "quant_job",
            QuantRecord.payload["engine"].as_string() == engine_name,
            QuantRecord.payload["status"].as_string() == "queued",
        )
        .order_by(QuantRecord.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not row:
        return None
    source = db.get(ResearchRecord, row.parent_id)
    valid = (
        paper.checksum(row.payload) == row.checksum
        and source is not None
        and source.owner_id == row.owner_id
        and source.kind == "dataset"
        and paper.checksum(source.payload) == source.checksum == row.payload["dataset_checksum"]
        and paper.checksum(row.payload["features"]) == row.payload["feature_checksum"]
    )
    if not valid:
        update(
            row,
            status="failed",
            error="Frozen research input integrity failed",
            finished_at=now().isoformat(),
        )
        db.commit()
        return None
    token = str(uuid4())
    update(
        row,
        status="running",
        claim_token=token,
        started_at=now().isoformat(),
        lease_at=now().isoformat(),
    )
    job = {
        **row.payload,
        "candles": source.payload["candles"],
        "data_context": source.payload.get("data_context"),
    }
    identity = row.id
    db.commit()
    return identity, token, job


def renew(db, identity, token, engine_name):
    row = db.get(QuantRecord, identity, with_for_update=True)
    if not row or row.payload.get("status") != "running" or row.payload.get("claim_token") != token:
        db.rollback()
        return False
    update(row, lease_at=now().isoformat())
    heartbeat(db, engine_name)
    return True


def finish(db, identity, token, result=None, error=None):
    row = db.get(QuantRecord, identity, with_for_update=True)
    if not row or row.payload.get("status") != "running" or row.payload.get("claim_token") != token:
        db.rollback()
        return False
    update(
        row,
        status="failed" if error else "completed",
        result=result,
        error=error,
        finished_at=now().isoformat(),
    )
    db.commit()
    return True


def execute(identity, token, job, args):
    folder = args.artifacts.resolve() / str(identity)
    folder.mkdir(parents=True, exist_ok=False)
    (folder / "input.json").write_text(json.dumps(job, allow_nan=False))
    if args.engine == "lean":
        quant_runtime.prepare_lean(folder, job)
    # Preserve the venv's executable path: resolving its symlink bypasses the venv.
    command = quant_runtime.command(args.engine, folder, str(args.python.absolute()))
    # Do not leak API, database or broker credentials into the engine process.
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(folder),
        "OPENBLAS_NUM_THREADS": "2",
        "OMP_NUM_THREADS": "2",
        "LC_ALL": "C.UTF-8",
    }
    started, next_renew = time.monotonic(), 0.0
    with (folder / "engine.log").open("w") as output:
        process = subprocess.Popen(command, stdout=output, stderr=output, env=env, cwd=folder)
        try:
            while process.poll() is None:
                if time.monotonic() - started > 900:
                    raise TimeoutError("Research engine exceeded the 15-minute limit")
                if time.monotonic() >= next_renew:
                    with Session(engine()) as db:
                        if not renew(db, identity, token, args.engine):
                            raise InterruptedError("Research experiment cancelled")
                    next_renew = time.monotonic() + 15
                time.sleep(1)
            if process.returncode != 0:
                raise RuntimeError("Research engine failed; inspect its local engine.log")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                if args.engine == "lean":
                    subprocess.run(
                        ["docker", "rm", "-f", "tauric-lab-" + folder.name],
                        capture_output=True,
                        timeout=15,
                    )
    path = folder / "result.json"
    if not path.exists() or path.stat().st_size > 5_000_000:
        raise ValueError("Research engine must produce a bounded result")
    result = json.loads(
        path.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value))
    )
    if result.get("engine") != args.engine or result.get("authority") != "research_only":
        raise ValueError("Unexpected engine result")
    result["provenance"] = {
        "runtime": quant_runtime.LEAN_IMAGE
        if args.engine == "lean"
        else "pyqlib==0.9.7; lightgbm==4.6.0",
        "runner_sha256": hashlib.sha256(
            (
                quant_runtime.RUNNERS
                / ("lean_algorithm.py" if args.engine == "lean" else "qlib_runner.py")
            ).read_bytes()
        ).hexdigest(),
        "dataset_checksum": job["dataset_checksum"],
        "feature_checksum": job["feature_checksum"],
        "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("lean", "qlib"), required=True)
    parser.add_argument("--python", type=Path, default=Path(os.sys.executable))
    parser.add_argument("--artifacts", type=Path, default=Path("../artifacts/quant"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    quant_runtime.probe(args.engine, str(args.python.absolute()))
    while True:
        with Session(engine()) as db:
            heartbeat(db, args.engine)
            claimed = claim(db, args.engine)
        if claimed:
            identity, token, job = claimed
            try:
                result = execute(identity, token, job, args)
                with Session(engine()) as db:
                    finish(db, identity, token, result=result)
            except Exception as error:
                with Session(engine()) as db:
                    finish(db, identity, token, error=str(error)[:500])
        if args.once:
            return
        time.sleep(5)


if __name__ == "__main__":
    main()
