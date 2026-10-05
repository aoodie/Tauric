"""Built portfolio workspace on a disposable DB, with an explicitly offline chat gateway."""

import asyncio
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_paper import setup as setup
from test_portfolio_chat import portfolios_ready as portfolios_ready

from tauric import chat, chat_research, chat_worker, governance, portfolios
from tauric.app import app
from tauric.config import runtime_config
from tauric.database import engine
from tauric.models import ChatTurn, GatewayStatus, Owner, now
from tauric.portfolio_contracts import PortfolioChatAction

pytest_plugins = ["test_chat"]


@pytest.mark.browser
def test_built_portfolio_chat_workflow(ready, database, portfolios_ready, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    python = shutil.which("python3")
    if (
        not python
        or subprocess.run([python, "-c", "import playwright"], capture_output=True).returncode
    ):
        pytest.skip("Python Playwright is required for the optional browser check.")
    a, b, da, _ = portfolios_ready
    original_routes = list(app.router.routes)
    app.mount("/", StaticFiles(directory=root / "frontend/dist", html=True))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    runtime_config().allowed_origins = [url]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    stopped = threading.Event()
    errors = []

    async def offline_gateway(body, timeout):
        # Exercise the real worker's binding and receipts, with no network/model request.
        conversation = body["user"][-36:]
        with Session(engine()) as db:
            turn = db.scalar(
                select(ChatTurn).where(
                    ChatTurn.conversation_id == conversation, ChatTurn.status == "running"
                )
            )
            owner = db.get(Owner, turn.owner_id)
            assert turn.portfolio_id == a["id"]
            assert b["id"] not in body["messages"][0]["content"]
            identity = {
                "agent_id": "tauric-chat",
                "channel": "webchat",
                "sender_is_owner": True,
                "session_key": "agent:tauric-chat:openai-user:" + body["user"],
                "tool_call_id": "offline-browser-pause",
            }
            config, _ = chat.load_config(db)
            action = PortfolioChatAction.model_validate(
                {
                    **identity,
                    "action": "pause",
                    "expected_revision": 0,
                    "deployment_id": da["id"],
                    "reason": "Explicit offline browser pause request",
                }
            )
            authorized = chat_research.authorize(db, owner, action, config)
            receipt = portfolios.chat_action(db, owner, authorized, action)
            return ("Offline portfolio fixture: pause saved. Receipt " + receipt.id, {})

    monkeypatch.setattr(chat_worker, "gateway_call", offline_gateway)
    monkeypatch.setattr(chat_worker, "STOP", False)

    def monitor():
        while not stopped.wait(0.2):
            try:
                with Session(engine()) as db:
                    owner = db.scalar(select(Owner))
                    gateway = db.get(GatewayStatus, "openclaw")
                    gateway.fingerprint = governance.routing(db, owner)["fingerprint"]
                    gateway.heartbeat_at = now()
                    db.commit()
                    chat.heartbeat(db)
                    claimed = chat.claim(db)
                if claimed:
                    asyncio.run(chat_worker.process(*claimed))
            except Exception as e:
                errors.append(type(e).__name__)

    worker = threading.Thread(target=monitor, daemon=True)
    try:
        thread.start()
        worker.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        result = subprocess.run(
            [python, str(root / "scripts/portfolio_browser_test.py"), url, a["id"], b["id"]],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert not errors, errors
    finally:
        stopped.set()
        worker.join(timeout=10)
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()
        app.router.routes[:] = original_routes
