"""Disposable browser chat fixture. No Gateway, bot network or model calls."""

import asyncio
import json
import os
import sys
import threading
from pathlib import Path

import uvicorn
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
fixture_path = Path(
    os.environ.get("TAURIC_BROWSER_FIXTURE", root / "artifacts/browser-test.json")
)
fixture = json.loads(fixture_path.read_text())
assert fixture["database_name"].startswith("tauric_browser_")
profiles = fixture_path.with_suffix(".profiles.json")
profiles.write_text(
    json.dumps(
        {
            "local": {
                "provider": "openai_compatible",
                "base_url": "http://127.0.0.1:9/v1",
                "quick_model": "browser-offline-model",
                "deep_model": "browser-offline-model",
            }
        }
    )
)
os.environ.update(
    TAURIC_DATABASE_URL=fixture["database_url"],
    TAURIC_MODEL_KEY_FILE=str(
        fixture_path.with_suffix(".key")
        if "TAURIC_BROWSER_FIXTURE" in os.environ
        else root / "artifacts/browser-model-key"
    ),
    TAURIC_MODEL_PROFILES_FILE=str(profiles),
    TAURIC_OPENCLAW_PRIVATE_FILE=str(root / ".env.openclaw-private.json"),
    TAURIC_SECURE_COOKIES="false",
    TAURIC_ALLOWED_ORIGINS=json.dumps(
        [
            os.environ.get("TAURIC_BROWSER_URL", "http://127.0.0.1:5173"),
            "http://localhost:5173",
        ]
    ),
)
sys.path.insert(0, str(root / "backend"))
from tauric import analysis, chat, chat_research, chat_worker, governance
from tauric.chat_contracts import ResearchInvocation
from tauric.database import engine
from tauric.models import AnalysisJob, GatewayStatus, Owner, now
from tauric.native_runner import artifact


async def finish_fixture(job_id, failed=False):
    await asyncio.sleep(4)
    with Session(engine()) as db:
        analysis.claim(db)
        job = db.get(AnalysisJob, job_id)
        if failed:
            analysis.finish(
                db,
                job_id,
                error="Offline fixture research failure; no automatic retry.",
            )
        else:
            result = artifact(
                {
                    "market_report": "Untouched offline native market report.",
                    "final_trade_decision": "Untouched offline Hold report.",
                },
                "Hold",
                job.request,
                {"llm_provider": "openai_compatible"},
                "0.5.2",
            )
            analysis.finish(db, job_id, result)


async def offline_reply(body, timeout):
    message = body["messages"][-1]["content"]
    if message.startswith("Run daily"):
        with Session(engine()) as db:
            owner = db.scalar(select(Owner))
            result = chat_research.request(
                db,
                owner,
                ResearchInvocation(
                    instrument_id="binance:spot:ETHUSDT"
                    if "failure" in message
                    else "binance:spot:BTCUSDT",
                    channel="webchat",
                    agent_id="tauric-chat",
                    sender_is_owner=True,
                    session_key="agent:tauric-chat:openai-user:" + body["user"],
                    tool_call_id="fixture-" + body["user"],
                ),
            )
        if not result["reused"]:
            asyncio.create_task(
                finish_fixture(result["job"]["id"], "failure" in message)
            )
        return "Queued offline fixture native research: " + result["job"]["id"], {
            "total_tokens": 42
        }
    await asyncio.sleep(2)
    return (
        "Offline browser fixture reply: web, Telegram and Discord use the same restricted assistant with separate histories.",
        {"total_tokens": 42},
    )


chat_worker.gateway_call = offline_reply


async def fixture_worker():
    while True:
        with Session(engine()) as db:
            owner = db.scalar(select(Owner))
            fingerprint = governance.routing(db, owner)["fingerprint"]
            db.execute(
                insert(GatewayStatus)
                .values(id="openclaw", fingerprint=fingerprint, heartbeat_at=now())
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"fingerprint": fingerprint, "heartbeat_at": now()},
                )
            )
            db.commit()
            chat.heartbeat(db)
            analysis.heartbeat(db, "0.5.2-offline-fixture")
            claimed = chat.claim(db)
        if claimed:
            await chat_worker.process(*claimed)
        await asyncio.sleep(0.5)


threading.Thread(target=lambda: asyncio.run(fixture_worker()), daemon=True).start()
try:
    uvicorn.run(
        "tauric.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("TAURIC_BROWSER_API_PORT", "8000")),
        proxy_headers=False,
    )
finally:
    profiles.unlink(missing_ok=True)
