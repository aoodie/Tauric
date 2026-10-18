"""Private database backup before the readiness migration; never print secrets."""

import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
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


def main():
    folder = ROOT / "artifacts" / "private"
    folder.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = folder / ("before-live-readiness-" + stamp + ".dump")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        result = subprocess.run(
            COMPOSE + ["pg_dump", "-U", "tauric_admin", "-d", "tauric", "-Fc"],
            stdout=output,
            stderr=subprocess.PIPE,
            check=False,
        )
    if result.returncode or target.stat().st_size == 0:
        raise RuntimeError(
            "Private database backup failed. Migration was not attempted."
        )
    with target.open("rb") as source:
        listing = subprocess.run(
            COMPOSE + ["pg_restore", "--list"],
            stdin=source,
            capture_output=True,
            check=False,
        )
    if listing.returncode:
        raise RuntimeError(
            "Backup directory could not be verified. Migration was not attempted."
        )
    with target.open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    result = {
        "backup": str(target),
        "bytes": target.stat().st_size,
        "sha256": digest,
        "directory_verified": True,
        "restore_rehearsal": False,
    }
    (ROOT / "artifacts/live-upgrade-backup.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
