"""Insert explicitly labelled original test evidence in the disposable database."""

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from tauric.models import AnalysisArtifact, AnalysisJob, Owner

fixture = json.loads((root / "artifacts/browser-test.json").read_text())
if not fixture["database_name"].startswith("tauric_browser_"):
    raise SystemExit("A disposable browser fixture is required.")
original = json.loads(sys.stdin.read())
assert original.get("fixture") is True
engine = create_engine(fixture["database_url"])
with Session(engine) as db:
    owner = db.scalar(select(Owner))
    job = AnalysisJob(
        owner_id=owner.id,
        request_id=str(uuid4()),
        instrument_id="binance:spot:BTCUSDT",
        profile="cloud",
        status="succeeded",
        request={
            "fixture": True,
            "trade_date": datetime.now(timezone.utc).date().isoformat(),
        },
        progress=["Isolated fixture"],
    )
    db.add(job)
    db.flush()
    artifact = AnalysisArtifact(
        job_id=job.id,
        owner_id=owner.id,
        payload=original,
        checksum=hashlib.sha256(
            json.dumps(original, sort_keys=True, allow_nan=False).encode()
        ).hexdigest(),
    )
    db.add(artifact)
    db.flush()
    identifier = artifact.id
    db.commit()
print(identifier)
engine.dispose()
