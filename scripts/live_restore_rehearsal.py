"""Restore the private backup to a disposable database and rehearse the upgrade."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def snapshots(engine):
    result = {}
    with engine.connect() as connection:
        tables = connection.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version' ORDER BY tablename"
            )
        ).all()
        for table in tables:
            rows = connection.scalars(
                text(f'SELECT to_jsonb(t)::text FROM "{table}" t')
            ).all()
            canonical = sorted(
                json.dumps(json.loads(row), sort_keys=True, separators=(",", ":"))
                for row in rows
            )
            result[table] = {
                "count": len(rows),
                "checksum": hashlib.sha256("\n".join(canonical).encode()).hexdigest(),
            }
    return result


def main():
    manifest_path = ROOT / "artifacts/live-upgrade-backup.json"
    manifest = json.loads(manifest_path.read_text())
    backup = Path(manifest["backup"])
    with backup.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != manifest["sha256"]:
            raise RuntimeError("The saved backup differs from its verified checksum.")
    values = dict(
        line.split("=", 1)
        for line in (ROOT / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    url = URL.create(
        "postgresql+psycopg",
        username="tauric_admin",
        password=values["POSTGRES_PASSWORD"],
        host="127.0.0.1",
        port=int(values.get("DB_PORT", "54330")),
        database="postgres",
    )
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    name = "tauric_live_restore_" + uuid4().hex[:12]
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    restored = create_engine(url.set(database=name))
    try:
        compose = [
            "docker",
            "compose",
            "--env-file",
            str(ROOT / ".env"),
            "-f",
            str(ROOT / "deploy/compose.dev.yml"),
            "exec",
            "-T",
            "db",
        ]
        with backup.open("rb") as source:
            result = subprocess.run(
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
                capture_output=True,
                check=False,
            )
        if result.returncode:
            raise RuntimeError("Isolated backup restoration failed.")
        before = snapshots(restored)
        with restored.connect() as connection:
            previous = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        os.environ["TAURIC_DATABASE_URL"] = url.set(database=name).render_as_string(
            hide_password=False
        )
        from tauric.config import runtime_config

        runtime_config.cache_clear()
        config = Config(str(ROOT / "backend/alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "backend/migrations"))
        command.upgrade(config, "head")
        after = snapshots(restored)
        if not all(after[table] == original for table, original in before.items()):
            raise RuntimeError("The rehearsed upgrade changed existing saved rows.")
        with restored.connect() as connection:
            revision = connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
        if (
            revision != "0032_live_readiness"
            or after["trading_readiness_records"]["count"] != 0
        ):
            raise RuntimeError(
                "The rehearsed readiness schema differs from its expected state."
            )
        report = {
            "backup_restored": True,
            "existing_table_hashes_preserved": len(before),
            "old_schema": previous,
            "new_schema": revision,
            "readiness_records_created": 0,
            "services_started_against_restored_policies": False,
        }
        (ROOT / "artifacts/live-restore-rehearsal.json").write_text(
            json.dumps(report, indent=2) + "\n"
        )
        manifest["restore_rehearsal"] = True
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps(report))
    finally:
        restored.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, SQLAlchemyError, CommandError):
        raise SystemExit(
            "The isolated restoration/upgrade rehearsal failed. No workstation migration was attempted."
        ) from None
