"""Private primary backup and isolated migration rehearsal; never upgrade the primary here."""

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
backup = (
    root
    / "artifacts/backups"
    / ("before-instrument-discovery-" + uuid4().hex[:8] + ".dump")
)
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
name = "tauric_discovery_restore_" + uuid4().hex[:12]
with admin.connect() as connection:
    connection.execute(text(f'CREATE DATABASE "{name}"'))
restored = create_engine(admin_url.set(database=name))


def snapshots(columns=None):
    with restored.connect() as connection:
        schema = {}
        if columns is None:
            for table in connection.scalars(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename"
                )
            ):
                schema[table] = connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=:table ORDER BY ordinal_position"
                    ),
                    {"table": table},
                ).all()
        else:
            schema = columns
        result = {}
        for table, fields in schema.items():
            rows = connection.scalars(
                text(f'SELECT to_jsonb(t)::text FROM "{table}" t')
            ).all()
            values = [
                {key: row[key] for key in fields} for row in map(json.loads, rows)
            ]
            canonical = sorted(
                json.dumps(row, sort_keys=True, separators=(",", ":")) for row in values
            )
            result[table] = {
                "count": len(rows),
                "checksum": hashlib.sha256("\n".join(canonical).encode()).hexdigest(),
            }
        return result, schema


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
    before, columns = snapshots()
    os.environ["TAURIC_DATABASE_URL"] = admin_url.set(database=name).render_as_string(
        hide_password=False
    )
    from tauric.config import runtime_config

    runtime_config.cache_clear()
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    command.upgrade(config, "0027_instrument_discovery")
    after, _ = snapshots(columns)
    assert before == after
    with restored.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0027_instrument_discovery"
        )
        assert connection.scalar(
            text("SELECT has_table_privilege('tauric_app','instruments','INSERT')")
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_trigger WHERE tgrelid='instruments'::regclass AND tgname IN ('immutable_history','immutable_truncate')"
                )
            )
            == 2
        )
    evidence = {
        "restored_backup": True,
        "existing_tables_preserved": len(before),
        "schema": "0027_instrument_discovery",
        "instrument_rows_preserved": before["instruments"]["count"],
        "immutable_instrument_guards_retained": True,
        "primary_database_unchanged": True,
    }
    (root / "artifacts/instrument-discovery-backup-verification.json").write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
    print(json.dumps(evidence))
finally:
    restored.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()
