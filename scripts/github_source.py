"""Prepare a source-only GitHub upload without workstation data or credentials."""

import argparse
import hashlib
import io
import json
import tarfile
from pathlib import Path

from server import ROOT, read_env

DIRECTORIES = (
    "backend",
    "frontend",
    "openclaw",
    "deploy",
    "docs",
    "scripts",
    ".github",
)
ROOT_FILES = ("README.md", ".gitignore", ".dockerignore", ".env.example")
EXCLUDED = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", "dist"}


def source_files():
    files = [ROOT / name for name in ROOT_FILES]
    for name in DIRECTORIES:
        for path in (ROOT / name).rglob("*"):
            if EXCLUDED.intersection(path.relative_to(ROOT).parts):
                continue
            if path.is_symlink():
                raise ValueError("Source upload refuses symlinks.")
            if (
                path.is_file()
                and not path.name.endswith((".pyc", ".tsbuildinfo", ".log", ".dump"))
                and not path.name.startswith(".env")
                and path.name != ".DS_Store"
            ):
                files.append(path)
    return sorted(files)


def known_secrets():
    values = []
    if (ROOT / ".env").exists():
        values.extend(
            value
            for key, value in read_env(ROOT / ".env").items()
            if "PASSWORD" in key or "TOKEN" in key or "KEY" in key
        )
    key = ROOT / ".env.model-key"
    if key.exists():
        values.append(key.read_text().strip())

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and any(
                    word in key.lower() for word in ("password", "token", "key")
                ):
                    values.append(item)
                else:
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for filename in (
        ".env.models.json",
        ".env.market-data.json",
        ".env.openclaw-private.json",
    ):
        path = ROOT / filename
        if path.exists():
            collect(json.loads(path.read_text()))
    return {value.encode() for value in values if len(value) >= 12}


def bundle(output):
    if output.exists():
        raise ValueError("Source archive already exists.")
    private = known_secrets()
    entries = []
    for path in source_files():
        data = path.read_bytes()
        if any(value in data for value in private):
            raise ValueError(
                f"Private credential detected in source file: {path.relative_to(ROOT)}"
            )
        entries.append((str(path.relative_to(ROOT)), data, path.stat().st_mode & 0o777))
    manifest = {
        "files": {name: hashlib.sha256(data).hexdigest() for name, data, _ in entries},
        "known_private_credentials_checked": True,
        "workstation_data_excluded": True,
        "git_history_included": False,
    }
    entries.append(
        (
            "github-source-manifest.json",
            (json.dumps(manifest, indent=2) + "\n").encode(),
            0o644,
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as target, tarfile.open(fileobj=target, mode="w:gz") as tar:
        for name, data, mode in entries:
            member = tarfile.TarInfo("tauric/" + name)
            member.size = len(data)
            member.mode = mode
            tar.addfile(member, io.BytesIO(data))
    return {
        "source_archive": str(output),
        "source_files": len(entries) - 1,
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "known_private_credentials_checked": True,
        "workstation_data_excluded": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(bundle(args.output.resolve()), indent=2))


if __name__ == "__main__":
    main()
