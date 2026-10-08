"""Create a server-only release bundle from four published image digests."""

import argparse
import hashlib
import json
import re
import shutil
import tarfile
from pathlib import Path

from server import IMAGE_KEYS, ROOT, validate_images

FILES = (
    "deploy/compose.server.yml",
    "deploy/compose.https.yml",
    "deploy/compose.public-ip.yml",
    "deploy/compose.gateway-recovery.yml",
    "openclaw/start.mjs",
    "openclaw/config.mjs",
    "backend/tauric/tradingview_connection.py",
    "backend/tauric/tradingview_mcp.py",
    "deploy/init-db.sh",
    "deploy/start-db.sh",
    "deploy/Caddyfile.https",
    "deploy/Caddyfile.public-ip",
    "deploy/model-profiles.example.json",
    "deploy/market-data.example.json",
    "scripts/server.py",
    "scripts/server_soak.py",
    "scripts/acceptance_research.py",
    "docs/ACCEPTANCE_PLAN.md",
    "docs/GITHUB_DEPLOYMENT.md",
    "docs/SERVER_READINESS.md",
    "docs/SERVER_DEPLOYMENT_VERIFICATION.md",
)


def build(images_dir, output, repository, revision):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("A full source commit is required.")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("A GitHub owner/repository is required.")
    if output.exists():
        raise ValueError("Release output already exists.")
    values = {}
    for kind, key in zip(("api", "worker", "web", "openclaw"), IMAGE_KEYS, strict=True):
        entry = json.loads((images_dir / f"{kind}.json").read_text())
        if entry["revision"] != revision or entry["repository"] != repository.lower():
            raise ValueError(
                "Image publication belongs to a different source revision."
            )
        values[key] = entry["image"]
    validate_images(values)
    output.mkdir(parents=True)
    for name in FILES:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    (output / "images.env").write_text(
        "".join(f"{key}={value}\n" for key, value in values.items())
    )
    files = {
        str(path.relative_to(output)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in output.rglob("*")
        if path.is_file()
    }
    (output / "release-manifest.json").write_text(
        json.dumps(
            {
                "format": 1,
                "repository": repository,
                "revision": revision,
                "platforms": ["linux/amd64", "linux/arm64"],
                "images": values,
                "files": files,
                "status": "deployment-candidate",
                "production_acceptance_complete": False,
            },
            indent=2,
        )
        + "\n"
    )
    archive = output.with_suffix(".tar.gz")
    with archive.open("xb") as target, tarfile.open(fileobj=target, mode="w:gz") as tar:
        tar.add(output, arcname="tauric-release")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    print(build(args.images_dir, args.output, args.repository, args.revision))


if __name__ == "__main__":
    main()
