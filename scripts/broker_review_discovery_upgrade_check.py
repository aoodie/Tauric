"""Private pre-review backup and isolated restore rehearsal; upgrade to 0026."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
values = dict(
    line.split("=", 1)
    for line in (root / ".env").read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)
compose = [
    "docker",
    "compose",
    "--env-file",
    str(root / ".env"),
    "-f",
    str(root / "deploy/compose.dev.yml"),
    "exec",
    "-T",
    "db",
]
backup = root / "artifacts/backups/before-broker-review-and-discovery.dump"
backup.parent.mkdir(parents=True, exist_ok=True)
fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "wb") as output:
    subprocess.run(
        compose + ["pg_dump", "-U", "tauric_admin", "-Fc", "tauric"],
        stdout=output,
        check=True,
    )
admin_url = URL.create(
    "postgresql+psycopg",
    username="tauric_admin",
    password=values["POSTGRES_PASSWORD"],
    host="127.0.0.1",
    port=int(values.get("DB_PORT", "54330")),
    database="postgres",
)
admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
name = "tauric_review_restore_" + uuid4().hex[:12]
with admin.connect() as connection:
    connection.execute(text(f'CREATE DATABASE "{name}"'))
restored = create_engine(admin_url.set(database=name))


def snapshots(*, original_columns=False):
    with restored.connect() as connection:
        tables = connection.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename"
            )
        ).all()
        result = {}
        for table in tables:
            rows = connection.scalars(
                text(f'SELECT to_jsonb(t)::text FROM "{table}" t')
            ).all()
            parsed = [json.loads(row) for row in rows]
            if original_columns:
                added = {
                    "broker_order_intents": "review_decision_id",
                    "gateway_status": "capabilities",
                }
                for row in parsed:
                    if table in added:
                        row.pop(added[table], None)
            canonical = sorted(
                json.dumps(row, sort_keys=True, separators=(",", ":")) for row in parsed
            )
            result[table] = {
                "count": len(rows),
                "checksum": hashlib.sha256("\n".join(canonical).encode()).hexdigest(),
            }
        return result


try:
    with backup.open("rb") as source:
        subprocess.run(
            compose
            + [
                "pg_restore",
                "--exit-on-error",
                "--no-owner",
                "-U",
                "tauric_admin",
                "-d",
                name,
            ],
            stdin=source,
            check=True,
        )
    before = snapshots()
    os.environ["TAURIC_DATABASE_URL"] = admin_url.set(database=name).render_as_string(
        hide_password=False
    )
    from tauric.config import runtime_config

    runtime_config.cache_clear()
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    command.upgrade(config, "head")
    after = snapshots(original_columns=True)
    assert all(after[table] == original for table, original in before.items())
    assert (
        after["broker_review_jobs"]["count"]
        == after["broker_review_decisions"]["count"]
        == 0
    )
    with restored.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == "0027_instrument_discovery"
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM broker_order_intents WHERE review_decision_id IS NOT NULL"
                )
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM gateway_status WHERE capabilities::jsonb <> '[]'::jsonb"
                )
            )
            == 0
        )
    result = {
        "restored_backup": True,
        "existing_tables_preserved": len(before),
        "schema": revision,
        "broker_policies_preserved": before["broker_policy_versions"]["count"],
        "broker_intents_preserved": before["broker_order_intents"]["count"],
        "new_review_records": 0,
        "new_intent_review_links": 0,
        "new_gateway_capabilities": [],
    }
    (
        root / "artifacts/broker-review-and-discovery-backup-verification.json"
    ).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
finally:
    restored.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()
