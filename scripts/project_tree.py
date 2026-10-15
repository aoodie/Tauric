"""Bounded source export. Never follow workspace symlinks into host files."""

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path

PREFIXES = (
    "backend/",
    "frontend/",
    "openclaw/",
    "docs/",
    "scripts/",
    "deploy/",
    ".github/workflows/",
)
EXACT = {
    "README.md",
    "AGENTS.md",
    ".gitignore",
    ".dockerignore",
    "frontend/package.json",
    "frontend/package-lock.json",
    "frontend/tsconfig.json",
    "frontend/tsconfig.app.json",
    "frontend/tsconfig.node.json",
    "frontend/vite.config.ts",
    "openclaw/config.mjs",
    "openclaw/package.json",
    "openclaw/package-lock.json",
    "openclaw/plugin/package.json",
    "openclaw/plugin/tsconfig.json",
}
SUFFIXES = {
    ".py",
    ".tsx",
    ".ts",
    ".css",
    ".md",
    ".txt",
    ".mjs",
    ".json",
    ".toml",
    ".ini",
    ".lock",
    ".yml",
    ".yaml",
    ".sh",
    ".sql",
    ".html",
    ".svg",
}
MAX_SOURCE = 16 * 1024**2


def canonical(value):
    return json.dumps(value, sort_keys=True).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def collect(root, *, contents=False):
    root = root.resolve()
    files, total = {}, 0
    for folder, directories, names in os.walk(root, followlinks=False):
        if Path(folder) == root:
            directories[:] = [
                d
                for d in directories
                if d
                in {
                    "backend",
                    "frontend",
                    "openclaw",
                    "docs",
                    "scripts",
                    "deploy",
                    ".github",
                }
            ]
        directories[:] = sorted(
            d
            for d in directories
            if d
            not in {
                ".git",
                "node_modules",
                "__pycache__",
                ".pytest_cache",
                ".ruff_cache",
                "dist",
                "artifacts",
            }
            and not (Path(folder) / d).is_symlink()
        )
        for name in sorted(names):
            path = Path(folder) / name
            relative = path.relative_to(root).as_posix()
            if relative not in EXACT and not (
                relative.startswith(PREFIXES)
                and (
                    path.suffix in SUFFIXES
                    or name.startswith(("Dockerfile", "Caddyfile"))
                )
            ):
                continue
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024**2:
                raise ValueError("Source files must be regular bounded files.")
            if len(files) >= 2500 or total + info.st_size > MAX_SOURCE:
                raise ValueError("Source export allowance exceeded.")
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as stream:
                data = stream.read(2 * 1024**2 + 1)
            total += len(data)
            if total > MAX_SOURCE or len(data) > 2 * 1024**2:
                raise ValueError("Source export allowance exceeded.")
            text = data.decode("utf-8")
            files[relative] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                **({"content": text} if contents else {}),
            }
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    files = collect(args.root)
    args.output.write_bytes(canonical({"files": files, "tree_checksum": digest(files)}))


if __name__ == "__main__":
    main()
