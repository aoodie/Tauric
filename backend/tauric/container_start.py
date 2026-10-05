"""Copy a host-private connection file, then drop privileges before starting services."""

import os
import shutil
import sys
import tempfile
from pathlib import Path


def copy_private(source, target):
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(target.parent, 10001, 10001)
    fd, temporary = tempfile.mkstemp(dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as output, source.open("rb") as original:
            shutil.copyfileobj(original, output)
        os.chown(temporary, 10001, 10001)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


if os.getuid() == 0:
    for name in ("model-profiles.json", "model-key", "openclaw-private.json", "market-data.json"):
        source = Path("/run/setup-" + name)
        if source.is_file():
            target = Path("/run/tauric") / name
            copy_private(source, target)
    if password_file := os.environ.get("TAURIC_DB_PASSWORD_FILE"):
        target = Path("/run/tauric/database-password")
        copy_private(Path(password_file), target)
        os.environ["TAURIC_DB_PASSWORD_FILE"] = str(target)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
os.execvp(sys.argv[1], sys.argv[1:])
