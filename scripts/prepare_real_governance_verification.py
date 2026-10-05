"""Copy preserved real research and privately re-encrypt the chosen model for an isolated check."""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
from tauric.config import runtime_config
from tauric.model_profiles import connection_head, decrypt_connection
from tauric.models import AnalysisArtifact, AnalysisJob, Owner, identifier, now
from tauric.owner_settings import save_connection
from tauric.schemas import ModelConnectionChange


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--fresh-analysis", action="store_true")
    source.add_argument("--source-artifact", type=UUID)
    args = parser.parse_args()
    fixture = json.loads((root / "artifacts/browser-test.json").read_text())
    if not fixture["database_name"].startswith("tauric_browser_"):
        raise RuntimeError("An isolated verification database is required.")
    values = dict(
        line.split("=", 1)
        for line in (root / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    url = URL.create(
        "postgresql+psycopg",
        username="tauric_app",
        password=values["TAURIC_DB_PASSWORD"],
        host="127.0.0.1",
        port=int(values.get("DB_PORT", "54330")),
        database="tauric",
    )
    primary = create_engine(url)
    os.environ["TAURIC_MODEL_KEY_FILE"] = str(root / ".env.model-key")
    runtime_config.cache_clear()
    information = {"fixture": True, "fresh_analysis": args.fresh_analysis}
    with Session(primary) as db:
        if not args.fresh_analysis:
            source = (
                db.get(AnalysisArtifact, str(args.source_artifact))
                if args.source_artifact
                else db.scalar(
                    select(AnalysisArtifact)
                    .join(AnalysisJob, AnalysisArtifact.job_id == AnalysisJob.id)
                    .where(
                        AnalysisJob.instrument_id == "binance:spot:BTCUSDT",
                        AnalysisJob.status == "succeeded",
                    )
                    .order_by(AnalysisArtifact.created_at.desc())
                    .limit(1)
                )
            )
            if not source:
                raise ValueError("No successful original report is available.")
            source_job = db.get(AnalysisJob, source.job_id)
            payload, checksum, request = (
                source.payload,
                source.checksum,
                source_job.request,
            )
            assert (
                source_job.status == "succeeded"
                and request["trade_date"] == now().date().isoformat()
            )
            assert (
                hashlib.sha256(
                    json.dumps(payload, sort_keys=True, allow_nan=False).encode()
                ).hexdigest()
                == checksum
            )
        profile = decrypt_connection(connection_head(db, "local"))
        assert profile.configured()
        information["model"] = profile.quick_model
    primary.dispose()
    os.environ["TAURIC_MODEL_KEY_FILE"] = str(root / "artifacts/browser-model-key")
    os.environ["TAURIC_MODEL_PROFILES_FILE"] = str(root / ".env.models.json")
    runtime_config.cache_clear()
    temporary = create_engine(fixture["database_url"])
    with Session(temporary) as db:
        owner = db.scalar(select(Owner))
        save_connection(
            "local",
            ModelConnectionChange(
                expected_revision=0,
                provider=profile.provider,
                base_url=profile.base_url,
                quick_model=profile.quick_model,
                deep_model=profile.deep_model,
                key_action="replace",
                api_key=profile.api_key.get_secret_value(),
            ),
            db,
            owner,
        )
        if not args.fresh_analysis:
            job = AnalysisJob(
                owner_id=owner.id,
                request_id=identifier(),
                instrument_id=source_job.instrument_id,
                profile="local",
                status="succeeded",
                request={
                    **request,
                    "verification_import": {
                        "source_artifact_id": source.id,
                        "source_job_id": source_job.id,
                        "checksum": checksum,
                    },
                },
                progress=[
                    "Preserved real report imported for isolated governance verification"
                ],
            )
            db.add(job)
            db.flush()
            artifact = AnalysisArtifact(
                owner_id=owner.id, job_id=job.id, payload=payload, checksum=checksum
            )
            db.add(artifact)
            db.flush()
            information.update(
                {
                    "artifact_id": artifact.id,
                    "source_artifact_id": source.id,
                    "source_checksum": checksum,
                }
            )
        db.commit()
    temporary.dispose()
    fd = os.open(
        root / "artifacts/real-governance-input.json",
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o600,
    )
    with os.fdopen(fd, "w") as handle:
        json.dump(information, handle)
    print(
        "Verification prepared; model credential re-encrypted in the disposable database. No model call yet."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 -- redact secrets at the verification boundary
        raise SystemExit(
            "Real-governance preparation failed. No credentials printed."
        ) from None
