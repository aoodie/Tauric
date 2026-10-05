"""Public-only discovery evidence using saved connections; no registry or broker writes."""

import json
import os
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

root = Path(__file__).resolve().parents[1]
values = dict(
    line.split("=", 1)
    for line in (root / ".env").read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)
os.environ["TAURIC_DATABASE_URL"] = URL.create(
    "postgresql+psycopg",
    username="tauric_app",
    password=values["TAURIC_DB_PASSWORD"],
    host="127.0.0.1",
    port=int(values.get("DB_PORT", "54330")),
    database="tauric",
).render_as_string(hide_password=False)
os.environ["TAURIC_MODEL_KEY_FILE"] = str(root / ".env.model-key")
os.environ["TAURIC_MARKET_CONNECTIONS_FILE"] = str(root / ".env.market-data.json")
sys.path.insert(0, str(root / "backend"))
from tauric.database import engine
from tauric.instrument_discovery import discover
from tauric.models import AnalysisJob, Instrument, now

try:
    with Session(engine()) as db:
        before = {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Instrument, AnalysisJob)
        }
        results = {}
        for provider in ("oanda", "alpaca"):
            first = discover(db, provider, limit=100)
            sample = discover(
                db, provider, "EUR/GBP" if provider == "oanda" else "NVDA"
            )
            results[provider] = {
                "source": first.source,
                "checked_at": first.checked_at,
                "total": first.total,
                "connection_revision": first.connection_revision,
                "sample": [row.model_dump(mode="json") for row in sample.items[:5]],
            }
        after = {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (Instrument, AnalysisJob)
        }
        assert before == after
        db.rollback()
    evidence = {
        "checked_at": now().isoformat(),
        "read_only": True,
        "registry_and_jobs_unchanged": True,
        "providers": results,
    }
    (root / "artifacts/instrument-discovery-live.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print(json.dumps(evidence))
except Exception as error:  # noqa: BLE001 -- suppress private diagnostics for any provider/config error
    # Connection strings, credentials and raw provider error bodies stay private.
    print(
        json.dumps(
            {
                "verified": False,
                "error_type": type(error).__name__,
                "validation_fields": [
                    {"field": str(item["loc"]), "type": item["type"]}
                    for item in error.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                ]
                if hasattr(error, "errors")
                else [],
                "detail": getattr(
                    error,
                    "detail",
                    "Live discovery failed; private diagnostics withheld.",
                ),
            }
        )
    )
    raise SystemExit(1) from None
