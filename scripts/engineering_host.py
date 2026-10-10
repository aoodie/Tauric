"""Fixed host launcher. Submitted code receives neither Docker nor production access.

Install as a root-owned system service. IMAGE must be a verified digest pinned by
the release installer; neither an image nor a host path is accepted from a job.
"""

import argparse
import base64
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from project_tree import collect

LABEL = "io.tauric.engineering"
WORKSPACE_ROOT = Path("/var/lib/tauric-engineering/workspaces")
MAX_WORKSPACES = 8
WORKSPACE_BYTES = 512 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True).encode()


def checksum(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def request_checksum(value):
    # Requests use Tauric's compact canonical record encoding; signed source
    # artifacts retain their existing separate serialization contract.
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def docker(*args, **kwargs):
    return subprocess.run(
        ["docker", *args], check=True, capture_output=True, **kwargs
    ).stdout


def container_args(image, job):
    job_id, workspace = str(UUID(job["id"])), str(UUID(job["workspace_id"]))
    if not re.fullmatch(r"(?:.+@)?sha256:[a-f0-9]{64}", image):
        raise ValueError("Use a release-verified digest-pinned engineering image.")
    return [
        "create",
        "--name",
        "tauric-engineering-" + job_id,
        "--label",
        LABEL + "=v1",
        "--label",
        "io.tauric.request=" + job["checksum"],
        "--interactive",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--pids-limit",
        "128",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--user",
        "10001:10001",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=128m",
        "--mount",
        "type=bind,source="
        + str(WORKSPACE_ROOT / workspace / "mount")
        + ",target=/workspace",
        "--log-opt",
        "max-size=1m",
        "--log-opt",
        "max-file=1",
        image,
    ]


def prepare_workspace(job, image_id):
    """A fixed-size filesystem bounds writes independently of submitted code."""
    workspace = str(UUID(job["workspace_id"]))
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder = WORKSPACE_ROOT / workspace
    if not folder.exists():
        if len(list(WORKSPACE_ROOT.iterdir())) >= MAX_WORKSPACES:
            raise ValueError(
                "Engineering workspace capacity exhausted; retain or archive results."
            )
        if shutil.disk_usage(WORKSPACE_ROOT).free < 2 * 1024**3:
            raise ValueError("Engineering host disk reserve is unavailable.")
        folder.mkdir(mode=0o700)
    provenance = folder / "image.json"
    if (
        provenance.exists()
        and json.loads(provenance.read_text())["image_id"] != image_id
    ):
        raise ValueError("An existing workspace is bound to a different release image.")
    disk, target = folder / "disk.ext4", folder / "mount"
    if not disk.exists():
        with disk.open("xb") as stream:
            stream.truncate(WORKSPACE_BYTES)
        subprocess.run(
            ["mkfs.ext4", "-F", "-m", "0", str(disk)], check=True, capture_output=True
        )
    if disk.stat().st_size != WORKSPACE_BYTES:
        raise ValueError("Engineering workspace capacity binding failed.")
    target.mkdir(exist_ok=True, mode=0o700)
    mounted = (
        subprocess.run(["mountpoint", "-q", str(target)], check=False).returncode == 0
    )
    if not mounted:
        subprocess.run(
            ["mount", "-o", "loop,nosuid,nodev", str(disk), str(target)],
            check=True,
            capture_output=True,
        )
    lost = target / "lost+found"
    if lost.is_dir():
        lost.rmdir()
    subprocess.run(["chown", "10001:10001", str(target)], check=True)
    provenance.write_text(json.dumps({"image_id": image_id, "workspace_id": workspace}))
    return target


def cancelled_result(job, image_id):
    return {
        "id": job["id"],
        "request_checksum": job["checksum"],
        "workspace_id": job["workspace_id"],
        "image_id": image_id,
        "exit_code": 125,
        "cancelled": True,
        "output": "",
        "test_runner": False,
        "tests_executed": 0,
        "finished_at": datetime.now(UTC).isoformat(),
        "error": "Owner stopped this work before execution.",
    }


def source_export(name, target, receipt_dir, job, image_id):
    with tempfile.TemporaryDirectory(prefix="tauric-source-manifest-") as folder:
        path = Path(folder) / "source.json"
        docker("cp", name + ":/source-manifest.json", str(path))
        if path.stat().st_size > 2 * 1024**2:
            raise ValueError("Source manifest is oversized.")
        baseline = json.loads(path.read_text())["files"]
    current = collect(target, contents=True)
    hashes = {key: {"sha256": item["sha256"]} for key, item in current.items()}
    changed = {
        key: item["content"]
        for key, item in current.items()
        if item["sha256"] != baseline.get(key, {}).get("sha256")
    }
    deleted = sorted(set(baseline) - set(current))
    bundle = {
        "job_id": job["id"],
        "workspace_id": job["workspace_id"],
        "image_id": image_id,
        "base_files": {
            key: baseline.get(key, {}).get("sha256")
            for key in sorted(set(changed) | set(deleted))
        },
        "files": changed,
        "deleted": deleted,
        "tree_checksum": checksum(hashes),
    }
    data = canonical(bundle)
    used = sum(
        path.stat().st_size
        for path in receipt_dir.glob(job["workspace_id"] + "-*.source.json")
    )
    if len(data) > 16 * 1024**2 or used + len(data) > 64 * 1024**2:
        raise ValueError("Source artifact allowance exceeded.")
    path = receipt_dir / (job["workspace_id"] + "-" + job["id"] + ".source.json")
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(data)
    temporary.chmod(0o644)
    temporary.replace(path)
    paths = sorted(set(changed) | set(deleted))
    return {
        "source_tree_checksum": bundle["tree_checksum"],
        "source_artifact_checksum": checksum(bundle),
        "source_artifact_bytes": len(data),
        "changed_paths": paths[:200],
        "changed_paths_truncated": len(paths) > 200,
    }


def signed_write(path, result, key):
    private = load_pem_private_key(key.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        raise TypeError("An Ed25519 engineering signing key is required.")
    signature = private.sign(canonical(result))
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"result": result, "signature": base64.b64encode(signature).decode()},
            sort_keys=True,
        )
    )
    temporary.chmod(
        0o644
    )  # The API can read evidence but cannot write the receipt directory.
    temporary.replace(path)


def process(path, root, image, key):
    if path.stat().st_size > 40000:
        raise ValueError("Engineering request too large.")
    job = json.loads(path.read_text())
    job_id = str(UUID(job["id"]))
    if path.name != job_id + ".json":
        raise ValueError("Request filename binding failed.")
    values = {
        name: job[name]
        for name in ("script", "timeout_seconds", "purpose", "workspace_id", "task_id")
    }
    if (
        request_checksum(values) != job["checksum"]
        or type(job["timeout_seconds"]) is not int
        or not 1 <= job["timeout_seconds"] <= 600
        or job["purpose"] not in {"inspect", "develop", "test"}
    ):
        raise ValueError("Engineering request integrity or limits failed.")
    receipt = root / "receipts" / (job_id + ".json")
    if receipt.exists():
        return
    image_id = json.loads(docker("image", "inspect", image))[0]["Id"]
    if (root / "cancel" / (job_id + ".json")).exists():
        signed_write(receipt, cancelled_result(job, image_id), key)
        return
    target = prepare_workspace(job, image_id)
    name = "tauric-engineering-" + job_id
    old = subprocess.run(["docker", "inspect", name], capture_output=True, check=False)
    if old.returncode:
        docker(*container_args(image, job))
        state = json.loads(docker("inspect", name))[0]
    else:
        state = json.loads(old.stdout)[0]
    labels = state["Config"]["Labels"]
    if labels.get(LABEL) != "v1" or labels.get("io.tauric.request") != job["checksum"]:
        raise ValueError("Existing engineering execution has different identity.")
    if state["Image"] != image_id:
        raise ValueError(
            "Existing engineering execution uses a different release image."
        )
    started = time.monotonic()
    cancelled, timeout = False, False
    launcher = None
    if state["State"]["Status"] == "created":
        if (root / "cancel" / (job_id + ".json")).exists():
            signed_write(receipt, cancelled_result(job, image_id), key)
            docker("rm", name)
            subprocess.run(["umount", str(target)], check=True, capture_output=True)
            return
        launcher = subprocess.Popen(
            ["docker", "start", "-ai", name],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        launcher.stdin.write(canonical(job))
        launcher.stdin.close()
    # A host restart observes the original container; it never replays a running/exited command.
    while True:
        cancelled = (root / "cancel" / (job_id + ".json")).exists()
        state = json.loads(docker("inspect", name))[0]
        if state["State"]["Status"] in {"exited", "dead"}:
            break
        cancelled = (root / "cancel" / (job_id + ".json")).exists()
        # Includes host recovery time: Docker start timestamp is independent of this process.
        start_stamp = state["State"].get("StartedAt", "")
        elapsed = time.monotonic() - started
        if start_stamp and not start_stamp.startswith("0001"):
            try:
                elapsed = max(
                    elapsed,
                    (
                        datetime.now(UTC)
                        - datetime.fromisoformat(
                            re.sub(
                                r"\.([0-9]{6})[0-9]+Z$", r".\1+00:00", start_stamp
                            ).replace("Z", "+00:00")
                        )
                    ).total_seconds(),
                )
            except ValueError:
                pass
        timeout = elapsed > job["timeout_seconds"] + 15
        if cancelled or timeout:
            docker("kill", name)
        time.sleep(0.5)
    if launcher:
        launcher.wait(timeout=10)
    log = docker("logs", "--tail", "300", name).decode("utf-8", "replace")[-30000:]
    tests = {}
    markers = re.findall(r"^TAURIC_TEST_RESULT=(.+)$", log, re.MULTILINE)
    if markers:
        tests = json.loads(markers[-1])
    test_runner = (
        job["purpose"] == "test"
        and tests.get("executed", 0) > 0
        and tests.get("failed") == 0
    )
    result = {
        "id": job_id,
        "request_checksum": job["checksum"],
        "workspace_id": job["workspace_id"],
        "image_id": state["Image"],
        "exit_code": 124 if timeout else int(state["State"]["ExitCode"]),
        "cancelled": cancelled,
        "output": log,
        "test_runner": test_runner,
        "tests_executed": tests.get("executed", 0),
        "finished_at": datetime.now(UTC).isoformat(),
        "error": "Time budget exceeded."
        if timeout
        else "Owner stopped this work."
        if cancelled
        else None,
    }
    if not cancelled:
        try:
            result.update(source_export(name, target, root / "receipts", job, image_id))
        except (ValueError, OSError, subprocess.CalledProcessError):
            result["exit_code"] = result["exit_code"] or 126
            result["error"] = (
                "The executed workspace source could not be exported within its integrity and size limits."
            )
    signed_write(receipt, result, key)
    docker("rm", name)
    subprocess.run(["umount", str(target)], check=True, capture_output=True)


def main():
    global WORKSPACE_ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument("--spool", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, default=WORKSPACE_ROOT)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    WORKSPACE_ROOT = args.workspace_root.resolve()
    if not re.fullmatch(r"(?:.+@)?sha256:[a-f0-9]{64}", args.image):
        raise SystemExit("Engineering image must be digest pinned.")
    for folder in ("requests", "receipts", "cancel"):
        (args.spool / folder).mkdir(parents=True, exist_ok=True)
    while True:
        for path in sorted((args.spool / "requests").glob("*.json")):
            try:
                process(path, args.spool, args.image, args.key)
            except Exception as error:  # noqa: BLE001 - isolate malformed jobs from the queue
                print("Engineering launcher: " + type(error).__name__, flush=True)
        if args.once:
            break
        time.sleep(2)


if __name__ == "__main__":
    main()
