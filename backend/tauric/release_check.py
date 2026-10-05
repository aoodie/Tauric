"""Read-only release diagnostics; output only counts, hashes and readiness."""

import argparse
import hashlib
import json

from sqlalchemy import text

from .config import SCHEMA_REVISION
from .database import engine


def snapshot():
    # Repeatable read ensures every table belongs to the same database snapshot.
    with engine().connect().execution_options(isolation_level="REPEATABLE READ") as db:
        db.execute(text("SET TRANSACTION READ ONLY"))
        names = db.scalars(
            text("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        ).all()
        tables = {}
        for name in names:
            quoted = db.dialect.identifier_preparer.quote(name)
            rows = db.execution_options(stream_results=True).scalars(
                text(
                    f'SELECT to_jsonb(t)::text FROM {quoted} t ORDER BY to_jsonb(t)::text COLLATE "C"'
                )
            )
            digest = hashlib.sha256()
            count = 0
            for row in rows:
                if count:
                    digest.update(b"\n")
                digest.update(
                    json.dumps(json.loads(row), sort_keys=True, separators=(",", ":")).encode()
                )
                count += 1
            tables[name] = {
                "count": count,
                "sha256": digest.hexdigest(),
            }
        revision = db.scalar(text("SELECT version_num FROM alembic_version"))
        return {"schema": revision, "expected_schema": SCHEMA_REVISION, "tables": tables}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["snapshot"])
    parser.parse_args()
    print(json.dumps(snapshot(), sort_keys=True))


if __name__ == "__main__":
    main()
