"""Upgrade populated legacy scanner history without changing evidence or allowances."""

from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_scanner import candidate, configured, escalate
from test_scanner import ready_model as ready_model
from test_scanner import scanner_feed as scanner_feed

from tauric import scanner
from tauric.config import runtime_config
from tauric.models import (
    AnalysisJob,
    MarketSnapshot,
    Owner,
    ScannerAnalysis,
    ScannerCandidate,
    ScannerEvent,
    ScannerMatch,
    ScannerProfileVersion,
    ScannerRun,
    StrategyVersion,
)
from tauric.scanner_contracts import ScannerSave


def test_populated_legacy_scanner_upgrade(
    signed_in, database, scanner_feed, ready_model, monkeypatch
):
    profile, request = configured(signed_in)
    found = candidate(signed_in, profile)
    response, _ = escalate(signed_in, found, profile)
    assert response.status_code == 200, response.text
    # Copy the dependency records into a separate database at the previous schema.
    models = (
        Owner,
        StrategyVersion,
        MarketSnapshot,
        AnalysisJob,
        ScannerProfileVersion,
        ScannerRun,
        ScannerCandidate,
        ScannerMatch,
        ScannerEvent,
        ScannerAnalysis,
    )
    with database[0].connect() as connection:
        records = {
            model: [dict(row) for row in connection.execute(select(model.__table__)).mappings()]
            for model in models
        }
    for record in records[ScannerProfileVersion]:
        record.pop("profile_id")
    name = "tauric_test_scanner_upgrade_" + uuid4().hex[:12]
    url = database[1].url.set(database=name)
    administrator = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    legacy = create_engine(url)
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    with administrator.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        with monkeypatch.context() as patch:
            patch.setenv("TAURIC_DATABASE_URL", url.render_as_string(hide_password=False))
            runtime_config.cache_clear()
            command.upgrade(config, "0022_tradingview_mcp")
            metadata = MetaData()
            tables = {m: Table(m.__tablename__, metadata, autoload_with=legacy) for m in models}
            with legacy.begin() as connection:
                for model in models:
                    if records[model]:
                        connection.execute(tables[model].insert(), records[model])
            command.upgrade(config, "head")
            with Session(legacy) as db:
                owner = db.get(Owner, records[Owner][0]["id"])
                migrated = db.get(ScannerProfileVersion, profile["id"])
                assert migrated.profile_id == profile["strategy_version_id"]
                assert migrated.payload == records[ScannerProfileVersion][0]["payload"]
                assert migrated.checksum == profile["checksum"]
                assert scanner.profile_view(db, owner, migrated).charged_analyses_today == 1
                assert scanner.save(db, owner, ScannerSave(**request)).id == profile["id"]
                original_candidate = db.get(ScannerCandidate, found["id"])
                assert original_candidate.checksum == found["checksum"]
                scanner.validate_admission(db, db.get(AnalysisJob, response.json()["id"]))
                revised = scanner.save(
                    db,
                    owner,
                    ScannerSave(
                        **{
                            **request,
                            "profile_id": migrated.profile_id,
                            "strategy_version_id": None,
                            "name": "Independent after upgrade",
                            "request_id": str(uuid4()),
                            "expected_revision": 1,
                        }
                    ),
                )
                assert revised.revision == 2 and revised.charged_analyses_today == 1
            # The backfill must restore the immutable-history trigger.
            with legacy.begin() as connection, pytest.raises(DBAPIError, match="immutable"):
                connection.execute(
                    text("UPDATE scanner_profile_versions SET profile_id='tampered'")
                )
    finally:
        runtime_config.cache_clear()
        legacy.dispose()
        with administrator.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        administrator.dispose()
