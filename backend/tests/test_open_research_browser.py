"""Research navigation must work without native eligibility or configured AI workers."""

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from tauric.app import app
from tauric.config import runtime_config
from tauric.models import Instrument


@pytest.mark.browser
def test_open_research_without_native_support(database, signed_in):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required for browser verification")
    dist = Path(os.environ.get("TAURIC_BROWSER_DIST", root / "frontend/dist"))
    assert (dist / "index.html").exists(), "Build the frontend first"
    with Session(database[0]) as db:
        original = db.get(Instrument, "oanda:fx:EUR_USD")
        instrument_id = "test:unclassified:EUR_USD"
        db.add(
            Instrument(
                id=instrument_id,
                definition={
                    **original.definition,
                    "id": instrument_id,
                    "classification_source": "unclassified",
                },
            )
        )
        db.commit()
    routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=dist, html=True))
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
            [python, str(root / "scripts/open_research_browser_test.py"), url],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = routes
