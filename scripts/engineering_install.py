"""Install a fixed isolated executor from a checksum-verified release source bundle."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

SERVICE = "tauric-engineering.service"


def install(release, state):
    if os.geteuid() != 0:
        raise ValueError("The fixed host installer must run as root.")
    if any(
        not shutil.which(name)
        for name in (
            "docker",
            "mkfs.ext4",
            "mount",
            "umount",
            "mountpoint",
            "systemctl",
        )
    ):
        raise ValueError(
            "The host needs Docker, ext4/loop-mount tools and systemd before installation."
        )
    manifest = json.loads((release / "release-manifest.json").read_text())
    revision = manifest["revision"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("A verified source revision is required.")
    for name in (
        "engineering-image.json",
        "scripts/engineering_host.py",
        "scripts/project_tree.py",
    ):
        if hashlib.sha256((release / name).read_bytes()).hexdigest() != manifest[
            "files"
        ].get(name):
            raise ValueError("Engineering release source failed its checksum check.")
    private = state / "secrets" / "engineering"
    spool = private / "spool"
    for folder in ("requests", "cancel", "receipts"):
        target = spool / folder
        target.mkdir(parents=True, exist_ok=True)
        target.chmod(0o700 if folder != "receipts" else 0o755)
        if folder != "receipts":
            os.chown(target, 10001, 10001)
    pending = [
        p
        for p in (spool / "requests").glob("*.json")
        if not (spool / "receipts" / p.name).exists()
    ]
    if pending:
        raise ValueError(
            "Existing engineering executions must be reconciled before upgrading their executor."
        )
    public = private / "public"
    public.mkdir(exist_ok=True, mode=0o755)
    public.chmod(0o755)
    key = private / "private.pem"
    if not key.exists():
        secret = Ed25519PrivateKey.generate()
        key.write_bytes(
            secret.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )
        key.chmod(0o600)
        (public / "public.pem").write_bytes(
            secret.public_key().public_bytes(
                Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
            )
        )
        (public / "public.pem").chmod(0o644)
    metadata = json.loads((release / "engineering-image.json").read_text())
    image = metadata["image"]
    if metadata["revision"] != revision or not re.fullmatch(
        r"ghcr.io/[a-z0-9./_-]+-engineering@sha256:[a-f0-9]{64}", image
    ):
        raise ValueError("Engineering image does not match the verified release.")
    inspected = json.loads(
        subprocess.check_output(["docker", "image", "inspect", image])
    )[0]
    if inspected["Config"]["Labels"]["org.opencontainers.image.revision"] != revision:
        raise ValueError(
            "Published engineering image does not match its release source."
        )
    subprocess.run(["systemctl", "stop", SERVICE], check=False, capture_output=True)
    runtime = private / "runtime"
    runtime.mkdir(exist_ok=True, mode=0o700)
    launcher = runtime / "engineering_host.py"
    shutil.copyfile(release / "scripts/engineering_host.py", launcher)
    launcher.chmod(0o600)
    shutil.copyfile(release / "scripts/project_tree.py", runtime / "project_tree.py")
    identity = public / "identity.json"
    previous = json.loads(identity.read_text()) if identity.exists() else {"images": {}}
    previous["images"][inspected["Id"]] = revision
    identity.write_text(json.dumps(previous, sort_keys=True))
    identity.chmod(0o644)
    unit = f"""[Unit]
Description=Tauric isolated engineering executor
After=docker.service
Requires=docker.service
[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 {launcher} --spool {spool} --workspace-root {private / "workspaces"} --image {image} --key {key}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
# Keep host mount propagation: the Docker daemon must see the bounded ext4 mount.
# Submitted code is isolated by container_args, never this privileged launcher.
[Install]
WantedBy=multi-user.target
"""
    Path("/etc/systemd/system/" + SERVICE).write_text(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", SERVICE], check=True)
    result = {
        "source_revision": revision,
        "image_id": inspected["Id"],
        "image_digest": image,
        "service": SERVICE,
        "financial_authority": False,
        "workspace_capacity": 8,
        "workspace_bytes": 512 * 1024**2,
    }
    (private / "install.json").write_text(json.dumps(result, sort_keys=True))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            install(args.release.resolve(), args.state.resolve()), sort_keys=True
        )
    )


if __name__ == "__main__":
    main()
