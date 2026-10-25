"""Backward-compatible encryption keyrings and non-activating rotation staging."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, MultiFernet

MAX_KEYS = 32


def key_material(data: bytes) -> list[bytes]:
    keys = [line.strip() for line in data.splitlines() if line.strip()]
    if not 1 <= len(keys) <= MAX_KEYS or len(set(keys)) != len(keys):
        raise ValueError("Invalid credential encryption keyring.")
    try:
        for key in keys:
            Fernet(key)
    except (TypeError, ValueError):
        raise ValueError("Invalid credential encryption keyring.") from None
    return keys


def encryption(data: bytes) -> MultiFernet:
    # New writes use the first key; immutable older revisions remain decryptable.
    return MultiFernet([Fernet(key) for key in key_material(data)])


def stage_rotation(source: Path, destination: Path) -> dict:
    if source.is_symlink() or not source.is_file():
        raise ValueError("Rotation requires a regular existing key file.")
    keys = key_material(source.read_bytes())
    if len(keys) >= MAX_KEYS:
        raise ValueError("The retained keyring is full; historical-key recovery needs review.")
    data = b"\n".join([Fernet.generate_key(), *keys]) + b"\n"
    # Validate before writing. Never replace the active file or an existing stage.
    encryption(data)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    return {"key_count": len(keys) + 1, "activated": False, "historical_ciphertexts_rewritten": False}
