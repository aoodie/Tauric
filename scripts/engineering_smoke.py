"""Exercise the real isolated Linux executor without production data or model calls."""

import argparse
import base64
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

import engineering_host as host
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)


def run(image, output):
    if os.geteuid() != 0:
        raise ValueError("Run this disposable Linux acceptance test as root.")
    secret = Ed25519PrivateKey.generate()
    with tempfile.TemporaryDirectory(
        prefix="tauric-engineering-acceptance-"
    ) as directory:
        root = Path(directory)
        for name in ("requests", "receipts", "cancel"):
            (root / name).mkdir()
        key = root / "private.pem"
        key.write_bytes(
            secret.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
        )
        host.WORKSPACE_ROOT = root / "workspaces"
        workspace = str(uuid4())

        def job(script, purpose="develop", cancelled=False):
            values = {
                "script": script,
                "timeout_seconds": 90,
                "purpose": purpose,
                "workspace_id": workspace,
                "task_id": workspace,
            }
            data = {"id": str(uuid4()), "checksum": host.checksum(values), **values}
            request = root / "requests" / (data["id"] + ".json")
            request.write_text(json.dumps(data))
            if cancelled:
                (root / "cancel" / request.name).write_text("{}")
            host.process(request, root, image, key)
            receipt = root / "receipts" / request.name
            envelope = json.loads(receipt.read_text())
            result = envelope["result"]
            secret.public_key().verify(
                base64.b64decode(envelope["signature"]), host.canonical(result)
            )
            before = receipt.read_bytes()
            host.process(request, root, image, key)
            assert receipt.read_bytes() == before, "An observed execution was replayed"
            return result

        try:
            developed = job("""python - <<'PY'
import os, pathlib, socket
assert os.getuid() == 10001
assert not any(key in os.environ for key in ('TAURIC_DATABASE_URL', 'POSTGRES_PASSWORD', 'GH_TOKEN'))
assert not pathlib.Path('/var/run/docker.sock').exists()
assert not pathlib.Path('/run/secrets').exists()
assert not pathlib.Path('/private.pem').exists()
try:
    pathlib.Path('/source/README.md').write_text('forbidden')
except OSError:
    pass
else:
    raise AssertionError('Image source was writable')
try:
    socket.create_connection(('1.1.1.1', 443), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('Network isolation failed')
p = pathlib.Path('scripts/tests/test_owner_acceptance.py')
p.write_text('def test_actual_code():\\n    assert sum([2, 3]) == 5\\n')
PY""")
            assert developed["exit_code"] == 0, developed
            assert (
                "scripts/tests/test_owner_acceptance.py" in developed["changed_paths"]
            )
            tested = job(
                "python -m pytest scripts/tests/test_owner_acceptance.py -q", "test"
            )
            assert tested["exit_code"] == 0 and tested["test_runner"], tested
            assert tested["tests_executed"] == 1, tested
            artifact = json.loads(
                (
                    root
                    / "receipts"
                    / (workspace + "-" + tested["id"] + ".source.json")
                ).read_text()
            )
            assert host.checksum(artifact) == tested["source_artifact_checksum"]
            assert artifact["tree_checksum"] == developed["source_tree_checksum"]
            assert artifact["files"][
                "scripts/tests/test_owner_acceptance.py"
            ].startswith("def test_actual_code")
            integration = job(
                "python -m pytest backend/tests/test_agent_tasks.py::test_task_owner_controls_require_login_and_csrf -q",
                "test",
            )
            assert (
                integration["exit_code"] == 0 and integration["tests_executed"] == 1
            ), integration
            stopped = job("touch scripts/should-never-execute.py", cancelled=True)
            assert stopped["cancelled"] and stopped["exit_code"] == 125
            folder = host.WORKSPACE_ROOT / workspace
            assert (folder / "disk.ext4").stat().st_size == host.WORKSPACE_BYTES
            assert (
                subprocess.run(
                    ["mountpoint", "-q", str(folder / "mount")], check=False
                ).returncode
                != 0
            )
            report = {
                "image": image,
                "image_id": tested["image_id"],
                "model_calls": 0,
                "broker_calls": 0,
                "tests_executed": tested["tests_executed"],
                "source_tree_checksum": tested["source_tree_checksum"],
                "checks": [
                    "real isolated code change",
                    "executed pytest",
                    "actual backend/API checks on an isolated disposable PostgreSQL database",
                    "signed exact source artifact",
                    "persistent quota workspace",
                    "receipt reconciliation without replay",
                    "cancellation before start",
                    "no network, Docker socket or production credentials",
                ],
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(report, indent=2) + "\n")
        finally:
            containers = (
                host.docker("ps", "-aq", "--filter", "label=" + host.LABEL + "=v1")
                .decode()
                .split()
            )
            for container in containers:
                inspected = json.loads(host.docker("inspect", container))[0]
                if any(
                    mount.get("Source", "").startswith(str(root))
                    for mount in inspected["Mounts"]
                ):
                    host.docker("rm", "-f", container)
            for folder in host.WORKSPACE_ROOT.glob("*/mount"):
                subprocess.run(
                    ["umount", str(folder)], capture_output=True, check=False
                )
            shutil.rmtree(host.WORKSPACE_ROOT, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.image, args.output)


if __name__ == "__main__":
    main()
