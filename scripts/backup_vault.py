"""Stream authenticated encrypted backups and verify recovery before cleanup."""

import argparse
import base64
import hashlib
import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HEADER = b"TAURICVAULT1\n"
BLOCK = 1024 * 1024


def key_material(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o077:
        raise ValueError("The backup key must be a private regular file.")
    key = base64.b64decode(path.read_bytes().strip(), altchars=b"-_", validate=True)
    if len(key) != 32:
        raise ValueError("A 256-bit backup encryption key is required.")
    return key


def recover(archive, key, target=None):
    """Authenticate the complete archive; callers must not expose partial output."""
    digest, size = hashlib.sha256(), 0
    with archive.open("rb") as source:
        header, nonce = source.read(len(HEADER)), source.read(12)
        length = archive.stat().st_size - len(HEADER) - 12 - 16
        if header != HEADER or len(nonce) != 12 or length < 0:
            raise ValueError("Invalid encrypted backup header.")
        source.seek(-16, os.SEEK_END)
        decrypt = Cipher(algorithms.AES(key), modes.GCM(nonce, source.read(16))).decryptor()
        decrypt.authenticate_additional_data(header + nonce)
        source.seek(len(HEADER) + 12)
        while length:
            block = source.read(min(BLOCK, length))
            if not block:
                raise ValueError("Encrypted backup is truncated.")
            data = decrypt.update(block)
            digest.update(data)
            size += len(data)
            if target is not None:
                target.write(data)
            length -= len(block)
        tail = decrypt.finalize()
        digest.update(tail)
        size += len(tail)
        if target is not None:
            target.write(tail)
    return {"plaintext_sha256": digest.hexdigest(), "plaintext_bytes": size}


def seal(source, destination, key_path):
    if source.is_symlink() or not source.is_file() or destination.is_symlink():
        raise ValueError("Backup input and destination must be regular files.")
    if destination.exists():
        raise ValueError("Encrypted backup exists; reconcile the original archive.")
    key = key_material(key_path)
    pending = destination.with_name(destination.name + ".pending")
    nonce = os.urandom(12)
    encrypt = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
    encrypt.authenticate_additional_data(HEADER + nonce)
    digest, size = hashlib.sha256(), 0
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output, source.open("rb") as original:
        output.write(HEADER + nonce)
        for block in iter(lambda: original.read(BLOCK), b""):
            digest.update(block)
            size += len(block)
            output.write(encrypt.update(block))
        output.write(encrypt.finalize())
        output.write(encrypt.tag)
        output.flush()
        os.fsync(output.fileno())
    expected = {"plaintext_sha256": digest.hexdigest(), "plaintext_bytes": size}
    if recover(pending, key) != expected:
        raise ValueError("Encrypted backup recovery differs from its source.")
    pending.replace(destination)
    return {"format": "tauric-aes256-gcm-v1", **expected, "authenticated_roundtrip_verified": True,
            "source_removed": False, "encrypted_bytes": destination.stat().st_size}


def restore(archive, destination, key_path):
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore requires a new private destination.")
    # No named plaintext file exists until authentication succeeds.
    with tempfile.TemporaryFile(dir=destination.parent) as temporary:
        result = recover(archive, key_material(key_path), temporary)
        temporary.seek(0)
        fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            for block in iter(lambda: temporary.read(BLOCK), b""):
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seal", "verify", "restore"])
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--key-file", required=True, type=Path)
    args = parser.parse_args()
    if args.action == "verify":
        result = recover(args.source, key_material(args.key_file))
    else:
        if args.destination is None:
            parser.error("A new destination is required.")
        result = (seal if args.action == "seal" else restore)(args.source, args.destination, args.key_file)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
