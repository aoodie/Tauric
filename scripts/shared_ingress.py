"""Verify host-owned shared routing independently of application releases."""

import hashlib
import json
import re
from pathlib import Path


def override(directory, values):
    enabled = values.get("TAURIC_SHARED_INGRESS", "false")
    if enabled not in {"true", "false"}:
        raise ValueError("Invalid shared ingress selection.")
    if enabled == "false":
        return None
    if not values.get("TAURIC_DOMAIN"):
        raise ValueError("Shared ingress requires the configured HTTPS domain.")
    private = Path(values["TAURIC_SECRETS_DIR"])
    if private.resolve() != directory.resolve() / "secrets" or private.is_symlink():
        raise ValueError("Shared ingress must belong to this private server state.")
    root = private / "shared-ingress"
    if root.is_symlink() or not root.is_dir() or root.stat().st_mode & 0o022:
        raise ValueError("Shared ingress must be a protected host-owned directory.")
    manifest = root / "manifest.json"
    if manifest.is_symlink() or not manifest.is_file():
        raise ValueError("Shared ingress needs its independent host manifest.")
    saved = json.loads(manifest.read_text())
    if saved.get("format") != 1 or set(saved.get("files", {})) != {"Caddyfile", "compose.yml"}:
        raise ValueError("Invalid shared ingress inventory.")
    for name, digest in saved["files"].items():
        path = root / name
        if (
            not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            or path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("Host-owned shared ingress changed; reconcile it before release.")
    return root / "compose.yml"


def command(base, directory, values):
    path = override(directory, values)
    if path is not None and str(path) not in base:
        return [*base, "-f", str(path)]
    return base
