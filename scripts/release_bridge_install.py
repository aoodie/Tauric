"""Install the fixed operations bridge after a trusted checked deployment."""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from release_identity import GitHubApp
from release_publish import save

FILES = (
    "release_bridge.py",
    "release_identity.py",
    "release_publish.py",
    "release_workflow.py",
    "release_install.py",
)
SERVICE = "tauric-release-bridge.service"


def install(release):
    if os.geteuid() != 0:
        raise ValueError("The trusted bridge installer must run as root.")
    manifest = json.loads((release / "release-manifest.json").read_text())
    revision = manifest["revision"]
    if release != Path(
        "/srv/tauric/releases/openclaw-owner-" + revision[:12] + "/tauric-release"
    ):
        raise ValueError("The bridge requires the normal checked release location.")
    for name, digest in manifest["files"].items():
        if hashlib.sha256((release / name).read_bytes()).hexdigest() != digest:
            raise ValueError(
                "Release checksum verification failed before bridge installation."
            )
    state = Path("/srv/tauric-private")
    operations = state / "secrets/operations"
    if operations.is_symlink() or operations.stat().st_mode & 0o077:
        raise ValueError("The private approved operations identity must already exist.")
    client = GitHubApp(operations)
    client.access_token()  # Validate selected repository and narrowed permissions.
    image = json.loads(
        subprocess.check_output(["docker", "inspect", "tauric_server-api-1"])
    )[0]
    metadata = json.loads(
        subprocess.check_output(["docker", "image", "inspect", image["Image"]])
    )[0]
    if (
        metadata["Config"]["Labels"].get("org.opencontainers.image.revision")
        != revision
    ):
        raise ValueError(
            "The bridge can only install against the exact running checked API release."
        )
    for request in (operations / "spool/requests").glob("*.json"):
        journal = operations / "jobs" / request.name
        if not journal.exists() or not json.loads(journal.read_text()).get("terminal"):
            raise ValueError(
                "Reconcile existing release work before upgrading its fixed executor."
            )
    for name in ("requests", "receipts", "public"):
        folder = operations / "spool" / name
        folder.mkdir(
            mode=0o755 if name != "requests" else 0o700, parents=True, exist_ok=True
        )
        folder.chmod(0o755 if name != "requests" else 0o700)
        if name == "requests":
            os.chown(folder, 10001, 10001)
    key_path = operations / "release-private.pem"
    public_path = operations / "spool/public/public.pem"
    if not key_path.exists():
        if public_path.exists():
            raise ValueError(
                "An existing public signing identity cannot be replaced automatically."
            )
        key = Ed25519PrivateKey.generate()
        fd = os.open(
            key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
        public_path.write_bytes(
            key.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        public_path.chmod(0o644)
    else:
        if key_path.is_symlink() or key_path.stat().st_mode & 0o077:
            raise ValueError("The release signing key is not private.")
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        expected = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        if public_path.read_bytes() != expected:
            raise ValueError("The installed release signing identities do not match.")
    subprocess.run(["systemctl", "stop", SERVICE], capture_output=True, check=False)
    runtime = operations / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    for name in FILES:
        source = release / "scripts" / name
        if "scripts/" + name not in manifest["files"]:
            raise ValueError("The checked release omitted a fixed bridge component.")
        shutil.copyfile(source, runtime / name)
        (runtime / name).chmod(0o600)
    save(
        operations / "active-release.json",
        {"revision": revision, "release_root": str(release)},
    )
    unit = f"""[Unit]
Description=Tauric signed source and checked release bridge
After=docker.service
Requires=docker.service
[Service]
Type=simple
User=root
ExecStart=/usr/bin/python3 {runtime / "release_bridge.py"}
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
[Install]
WantedBy=multi-user.target
"""
    Path("/etc/systemd/system/" + SERVICE).write_text(unit)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", SERVICE], check=True)
    subprocess.run(["systemctl", "is-active", "--quiet", SERVICE], check=True)
    result = {
        "source_revision": revision,
        "service": SERVICE,
        "repository": "aoodie/Tauric",
        "keys_mounted_in_application_or_engineering": False,
        "end_to_end_acceptance_complete": False,
    }
    save(operations / "install.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(install(args.release.resolve()), sort_keys=True))


if __name__ == "__main__":
    main()
