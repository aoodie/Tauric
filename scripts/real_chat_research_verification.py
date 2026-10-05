"""Run inside the local API container; records evidence, never credentials."""

import json
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from tauric import chat
from tauric.chat_contracts import ChatSend
from tauric.database import engine
from tauric.models import (
    AnalysisArtifact,
    AnalysisJob,
    ChatResearch,
    ChatTurn,
    Owner,
    PaperAccount,
    StrategyVersion,
    TradingRecord,
)


def financial(db):
    return {
        m.__tablename__: db.scalar(select(func.count()).select_from(m))
        for m in (PaperAccount, StrategyVersion, TradingRecord)
    }


with Session(engine()) as db:
    owner = db.scalar(select(Owner))
    assert owner
    baseline = financial(db)
    original = {row.id: row.checksum for row in db.scalars(select(AnalysisArtifact))}
    turn = chat.enqueue(
        db,
        owner,
        ChatSend(
            request_id=uuid4(),
            conversation_id=uuid4(),
            text="Run current daily BTC research now. Use tauric_request_research with binance:spot:BTCUSDT and return the real job ID. Do not place any trades.",
        ),
    )
    turn_id = turn.id
    print(json.dumps({"phase": "owner_chat_queued", "turn_id": turn_id}), flush=True)

deadline = time.monotonic() + 1300
last_state = None
while time.monotonic() < deadline:
    with Session(engine()) as db:
        turn = db.get(ChatTurn, turn_id)
        admissions = list(
            db.scalars(select(ChatResearch).where(ChatResearch.turn_id == turn_id))
        )
        job = db.get(AnalysisJob, admissions[0].job_id) if admissions else None
        state = (turn.status, job.status if job else None)
        if state != last_state:
            print(
                json.dumps(
                    {
                        "phase": "real_pipeline",
                        "chat": state[0],
                        "research": state[1],
                        "job_id": job.id if job else None,
                    }
                ),
                flush=True,
            )
            last_state = state
        if turn.status in ("failed", "interrupted") or (
            turn.status == "succeeded" and not job
        ):
            raise RuntimeError(
                "Real chat did not admit native research: "
                + (turn.error or turn.assistant_text or "No admission")
            )
        if job and job.status in ("failed", "interrupted"):
            raise RuntimeError(
                "Native research did not complete: " + (job.error or job.status)
            )
        if job and job.status == "succeeded" and turn.status == "succeeded":
            artifact = db.scalar(
                select(AnalysisArtifact).where(AnalysisArtifact.job_id == job.id)
            )
            assert artifact and artifact.payload["execution_authority"] is False
            assert artifact.payload["governance"] is None
            assert financial(db) == baseline
            assert all(
                db.get(AnalysisArtifact, key).checksum == value
                for key, value in original.items()
            )
            evidence = {
                "chat_turn_id": turn_id,
                "conversation_id": turn.conversation_id,
                "job_id": job.id,
                "artifact_id": artifact.id,
                "status": "succeeded",
                "checksum": artifact.checksum,
                "report_fields": list(artifact.payload["reports"]),
                "chat_usage": turn.usage,
                "native_elapsed_seconds": round(
                    (job.finished_at - job.started_at).total_seconds()
                ),
                "channel": admissions[0].channel,
                "charged": admissions[0].charged,
                "connection_revision": job.request["chat_context"][
                    "connection_revision"
                ],
                "original_reports_unchanged": True,
                "financial_counts_unchanged": baseline,
                "execution_authority": False,
                "upstream_revision": artifact.payload["upstream_revision"],
            }
            target = Path("/tmp/tauric-real-chat-research.json")
            target.write_text(json.dumps(evidence, indent=2) + "\n")
            target.chmod(0o600)
            print(
                json.dumps(
                    {
                        "phase": "verified",
                        "job_id": job.id,
                        "reports": len(evidence["report_fields"]),
                        "elapsed_seconds": evidence["native_elapsed_seconds"],
                    }
                ),
                flush=True,
            )
            break
    time.sleep(3)
else:
    raise TimeoutError(
        "Real verification timed out; inspect the job without automatic retry."
    )
