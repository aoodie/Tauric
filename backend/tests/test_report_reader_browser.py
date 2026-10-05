"""Read saved native reports in the built UI without provider or model requests."""

import shutil
import socket
import subprocess
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from tauric.app import app
from tauric.config import runtime_config
from tauric.models import AnalysisArtifact, AnalysisJob, Owner, identifier
from tauric.native_runner import artifact


@pytest.mark.browser
def test_read_saved_reports(database):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required")
    stamp = datetime.now(UTC)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        artifacts = []
        for i in range(32):
            request = {
                "instrument": {"symbol": "BTCUSDT", "asset_type": "crypto_spot"},
                "trade_date": "2026-10-03",
            }
            if i == 1:
                request["mode"] = "strategy_qualification"
            job = AnalysisJob(
                owner_id=owner.id,
                request_id=identifier(),
                instrument_id="binance:spot:BTCUSDT",
                profile="local",
                status="failed" if i == 2 else "succeeded",
                request=request,
                created_at=stamp - timedelta(minutes=i),
                error="Offline fixture failure" if i == 2 else None,
            )
            db.add(job)
            db.flush()
            if i == 2:
                continue
            payload = artifact(
                {
                    "final_trade_decision": "## Research conclusion\n\n**Hold** while the range holds.\n\n- Watch liquidity\n- Account for fees\n\n| Level | Price | Evidence |\n| --- | --- | --- |\n| Support | $82,437 | Repeated tests |\n| Resistance | $87,146 | Prior high |\n\n<script>window.reportInjected=true</script>\n[Unsafe link](javascript:alert(1))\n![Invisible tracker](https://example.invalid/tracker.png)",
                    "market_report": "## Market structure\n\nOriginal market narrative preserved.\n\n"
                    + "Long analysis with readable paragraphs and **key evidence**.\n\n" * 30,
                    "news_report": "No news report evidence supplied in this offline fixture.",
                },
                "Hold",
                request,
                {},
                "0.5.2",
            )
            if i == 1:
                payload["strategy_assessment"] = {
                    "decision": "incomplete",
                    "rationale": "Forward execution evidence is missing.",
                    "limitations": ["Scalping fills have not been validated."],
                    "strategy_version_id": "synthetic-version",
                    "intended_mode": "research_comparison",
                }
            row = AnalysisArtifact(
                owner_id=owner.id, job_id=job.id, payload=payload, checksum="a" * 64
            )
            db.add(row)
            db.flush()
            artifacts.append(row.id)
        db.commit()
        before = [(r.id, r.payload, r.checksum) for r in db.scalars(select(AnalysisArtifact))]
    routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    runtime_config().allowed_origins = [url]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        result = subprocess.run(
            [python, str(root / "scripts/report_reader_browser_test.py"), url, artifacts[0]],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        with Session(database[0]) as db:
            assert [
                (r.id, r.payload, r.checksum) for r in db.scalars(select(AnalysisArtifact))
            ] == before
            assert len(list(db.scalars(select(AnalysisJob)))) == 32
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
