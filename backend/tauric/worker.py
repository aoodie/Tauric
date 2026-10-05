"""A single leased native worker; interrupted/billed work is never retried silently."""

import json
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy.orm import Session

from . import (
    analysis,
    autonomous,
    broker_setup,
    candidate_research,
    chat_research,
    portfolio_research,
    qualification,
)
from .config import runtime_config
from .database import engine
from .models import AnalysisJob

STOP = False


def stop(*_):
    global STOP
    STOP = True


def process(job_id, version):
    config = runtime_config()
    directory = Path(config.runtime_directory) / "jobs" / job_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with Session(engine()) as db:
        job = db.get(AnalysisJob, job_id)
        try:
            autonomous.validate_admission(db, job)
            candidate_research.validate_admission(db, job)
            chat_research.validate_admission(db, job)
            portfolio_research.validate_admission(db, job)
            qualification.validate_admission(db, job)
            broker_setup.validate_admission(db, job)
        except Exception:
            analysis.finish(
                db,
                job_id,
                error="Research admission expired or its connection/context changed. No model request was started.",
            )
            return
        (directory / "request.json").write_text(
            json.dumps({"profile": job.profile, "request": job.request})
        )
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen(
            [sys.executable, "-m", "tauric.native_runner", str(directory / "request.json")],
            stdout=output,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
        )
        started = time.monotonic()
        failure = None
        try:
            while child.poll() is None:
                if STOP or time.monotonic() - started > config.analysis_timeout_seconds:
                    failure = "Analysis was stopped or reached its time budget. It was not automatically retried."
                    break
                progress = []
                try:
                    progress = json.loads((directory / "progress.json").read_text())
                except (OSError, ValueError):
                    pass
                with Session(engine()) as db:
                    analysis.heartbeat(db, version)
                    if not analysis.renew(db, job_id, progress):
                        failure = "The analysis lease was lost. Results were not finalized."
                        break
                time.sleep(2)
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        output.seek(0)
        raw = output.read(2_000_001)
        payload = None
        if not failure and child.returncode == 0 and len(raw) <= 2_000_000:
            try:
                payload = json.loads(raw)
                if not payload.get("reports", {}).get("final_trade_decision"):
                    payload = None
            except ValueError:
                pass
        error = (
            failure
            or "Native analysis failed. Verify model access and configuration; no retry was made."
        )
        with Session(engine()) as db:
            analysis.finish(db, job_id, payload, None if payload is not None else error)


def main():
    import tradingagents

    if tradingagents.__version__ != "0.5.2":
        raise SystemExit("Pinned Tauric runtime is required.")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("Native analysis worker started; one run at a time. No automatic execution.", flush=True)
    while not STOP:
        try:
            with Session(engine()) as db:
                analysis.heartbeat(db, tradingagents.__version__)
                job_id = analysis.claim(db)
            if job_id:
                process(job_id, tradingagents.__version__)
            else:
                time.sleep(2)
        except Exception:
            # Reconnect after storage outages. Details stay out of logs to protect secrets.
            print("Analysis storage unavailable; waiting to reconnect.", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
