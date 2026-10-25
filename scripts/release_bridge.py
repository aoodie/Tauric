"""Fixed host bridge: signed source, one publication, checked release and verified install.

No submitted command, repository, credential path or production setting is accepted.
The application spool contains identities only; current authority is checked in
the database before each new external effect.
"""

import argparse
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from release_identity import GitHubApp, ReleaseAccessError
from release_install import EngineeringBusy, install, module, recover_before_migration
from release_publish import publish, save, signed_source
from release_workflow import (
    PendingArtifact,
    acceptance,
    artifact,
    completed_release,
    deployment_archive,
    dispatch,
    run_identity,
    zip_files,
)

UTC = timezone.utc  # Native host tools support Python 3.10.
STOP = False
STATE = Path("/srv/tauric-private")
OPERATIONS = STATE / "secrets/operations"
PROJECT = "tauric_server"


class Paused(Exception):
    pass


def read(path, limit=100000):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("A bounded regular release record is required.")
        data = handle.read(limit + 1)
        if len(data) > limit:
            raise ValueError("Release record exceeded its allowance.")
        return json.loads(data)


def active_release():
    value = read(OPERATIONS / "active-release.json")
    revision = value["revision"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Installed source identity is invalid.")
    path = Path(value["release_root"])
    expected = Path(
        "/srv/tauric/releases/openclaw-owner-" + revision[:12] + "/tauric-release"
    )
    if path != expected or path.is_symlink():
        raise ValueError("Installed release root is outside the fixed host path.")
    manifest = read(path / "release-manifest.json")
    if (
        manifest["revision"] != revision
        or manifest["repository"].lower() != "aoodie/tauric"
    ):
        raise ValueError("Installed release manifest identity changed.")
    for name, expected_hash in manifest["files"].items():
        if hashlib.sha256((path / name).read_bytes()).hexdigest() != expected_hash:
            raise ValueError("Installed release manifest checksum failed.")
    return path, manifest


def host_authorization(job_id, checksum, active):
    server = module("release_authorization_server", active / "scripts/server.py")
    command = [
        "python",
        "-m",
        "tauric.release_jobs",
        "authorize",
        str(UUID(job_id)),
        checksum,
    ]
    result = subprocess.run(
        ["docker", "exec", PROJECT + "-api-1", *command],
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # During a journaled installation the web API is deliberately frozen.
        # This fixed read-only CLI runs without starting an application worker.
        result = server.run(
            server.command(STATE, PROJECT)
            + ["run", "--rm", "--no-deps", "-T", "api", *command],
            stdout=subprocess.PIPE,
        )
    value = json.loads(result.stdout)
    if value.get("id") != job_id or value.get("checksum") != checksum:
        raise ValueError(
            "Application request identity failed independent host authorization."
        )
    return value


def receipt(journal, *, status="running", error=None):
    result = {
        "id": journal["id"],
        "task_id": journal["task_id"],
        "request_checksum": journal["checksum"],
        "status": status,
        "phase": journal.get("phase", "queued"),
        "error": error,
        "financial_authority": False,
        **journal.get("public_result", {}),
    }
    key = serialization.load_pem_private_key(
        (OPERATIONS / "release-private.pem").read_bytes(), password=None
    )
    signature = base64.b64encode(
        key.sign(json.dumps(result, sort_keys=True).encode())
    ).decode()
    save(
        OPERATIONS / "spool/receipts" / (journal["id"] + ".json"),
        {"result": result, "signature": signature},
        mode=0o644,
    )


def finish(journal, path, *, status, phase, error=None):
    """Record the terminal outcome before delivery so a restart can repair delivery."""
    journal.update(
        {
            "terminal": True,
            "terminal_status": status,
            "terminal_error": error,
            "phase": phase,
        }
    )
    save(path, journal)
    receipt(journal, status=status, error=error)


def repair_terminal_receipt(journal):
    target = OPERATIONS / "spool/receipts" / (journal["id"] + ".json")
    try:
        delivered = read(target)["result"]
    except (OSError, ValueError, KeyError):
        delivered = {}
    if (
        delivered.get("status") != journal["terminal_status"]
        or delivered.get("request_checksum") != journal["checksum"]
    ):
        receipt(
            journal,
            status=journal["terminal_status"],
            error=journal.get("terminal_error"),
        )


def source(access, active_manifest):
    root = STATE / "secrets/engineering"
    job_id = str(UUID(access["inputs"]["test_job_id"]))
    workspace = str(UUID(access["engineering_workspace_id"]))
    request = read(root / "spool/requests" / (job_id + ".json"))
    envelope = read(root / "spool/receipts" / (job_id + ".json"))
    bundle = read(
        root / "spool/receipts" / (workspace + "-" + job_id + ".source.json"),
        16 * 1024**2,
    )
    if (
        request["checksum"] != access["engineering_request_checksum"]
        or request["workspace_id"] != workspace
    ):
        raise ValueError(
            "Executed request differs from its immutable application record."
        )
    identities = read(root / "public/identity.json")["images"]
    return signed_source(
        request,
        envelope,
        bundle,
        (root / "public/public.pem").read_bytes(),
        identities,
        active_manifest["revision"],
    )


def staged_release(client, journal, path, active, active_manifest):
    revision = journal["publication"]["commit"]
    run_id = journal["release_run"]["run_id"]
    value, artifact_id = artifact(client, run_id, "tauric-deployment-" + revision)
    files = zip_files(
        value, {"tauric-release.tar.gz", "release-smoke.json", "engineering-smoke.json"}
    )
    protected = {
        name: digest
        for name, digest in active_manifest["files"].items()
        if name.startswith(("scripts/", "deploy/", ".github/"))
        or name == "docs/OPENCLAW_PROJECT_OWNERSHIP.md"
    }
    release_files, manifest = deployment_archive(
        files["tauric-release.tar.gz"],
        revision,
        active_manifest["files"].keys() - {"images.env", "engineering-image.json"},
        protected,
    )
    acceptance(files, manifest)
    engineering = json.loads(release_files["engineering-image.json"])
    if (
        json.loads(files["engineering-smoke.json"])["image"] != engineering["image"]
        or engineering["revision"] != revision
    ):
        raise ValueError(
            "Executed engineering image differs from the candidate release."
        )
    target = Path(
        "/srv/tauric/releases/openclaw-owner-" + revision[:12] + "/tauric-release"
    )
    target.mkdir(parents=True, mode=0o700, exist_ok=True)
    for name, data in release_files.items():
        output = target / name
        output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if output.exists():
            if output.is_symlink() or output.read_bytes() != data:
                raise ValueError(
                    "An existing candidate release contains different contents."
                )
        else:
            fd = os.open(
                output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
    journal["candidate"] = {
        "revision": revision,
        "root": str(target),
        "artifact_id": artifact_id,
        "archive_sha256": hashlib.sha256(files["tauric-release.tar.gz"]).hexdigest(),
    }
    journal["phase"] = "candidate_verified"
    save(path, journal)
    return target, manifest


def images_present(manifest, candidate):
    expected = [
        *manifest["images"].values(),
        read(candidate / "engineering-image.json")["image"],
    ]
    for reference in expected:
        result = subprocess.run(
            ["docker", "image", "inspect", reference], capture_output=True, check=False
        )
        if result.returncode:
            return False
        image = json.loads(result.stdout)[0]
        if (
            image["Config"]["Labels"].get("org.opencontainers.image.revision")
            != manifest["revision"]
            or reference not in image["RepoDigests"]
            or image["Architecture"] != "amd64"
        ):
            raise ValueError(
                "A preloaded candidate image has a different immutable identity."
            )
    return True


def preload(client, journal, path, candidate, manifest, authorize):
    private_path = OPERATIONS / "recipient-keys" / (journal["id"] + ".pem")

    def cleanup():
        if journal.get("registry_artifact_id"):
            try:
                client.repo(
                    "/actions/artifacts/" + str(journal["registry_artifact_id"]),
                    method="DELETE",
                )
            except ReleaseAccessError as error:
                if error.status != 404:
                    raise
            journal["registry_artifact_deleted"] = True
        private_path.unlink(missing_ok=True)
        save(path, journal)

    if images_present(manifest, candidate):
        cleanup()
        return True
    private_path.parent.mkdir(mode=0o700, exist_ok=True)
    if not private_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        fd = os.open(
            private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(fd, "wb") as handle:
            handle.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    revision = manifest["revision"]
    public = (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    run = dispatch(
        client,
        journal,
        path,
        "registry_run",
        journal["publication"]["branch"],
        {
            "registry_revision": revision,
            "registry_run": str(journal["release_run"]["run_id"]),
            "registry_recipient": public,
        },
        authorize,
    )
    if not run:
        return False
    if run.get("status") == "completed":
        # Job credentials expire when their original workflow finishes. Renew
        # only this same verified image set, inside the shared recovery budget.
        journal["attempts"] += 1
        if journal["attempts"] >= 5:
            raise ValueError("Image-only credential recovery exhausted.")
        journal.setdefault("registry_history", []).append(journal.pop("registry_run"))
        cleanup()
        journal.pop("registry_artifact_id", None)
        save(path, journal)
        return False
    # Consume while the scoped credentials' original job keeps them alive.
    run_identity(run, revision, journal["publication"]["branch"])
    value, artifact_id = artifact(
        client, run["id"], "encrypted-registry-access-" + revision
    )
    journal["registry_artifact_id"] = artifact_id
    save(path, journal)
    envelope = json.loads(
        zip_files(value, {"encrypted-registry-access.json"})[
            "encrypted-registry-access.json"
        ]
    )
    if envelope["revision"] != revision:
        raise ValueError("Encrypted image handoff has a different source identity.")
    secret = key.decrypt(
        base64.b64decode(envelope["wrapped_key"], validate=True),
        padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None
        ),
    )
    plain = AESGCM(secret).decrypt(
        base64.b64decode(envelope["nonce"], validate=True),
        base64.b64decode(envelope["ciphertext"], validate=True)
        + base64.b64decode(envelope["tag"], validate=True),
        None,
    )
    payload = json.loads(plain)
    pull = module("fixed_release_registry_pull", candidate / "scripts/registry_pull.py")
    pull.validate(payload, revision)
    expected = set(manifest["images"].values()) | {
        read(candidate / "engineering-image.json")["image"]
    }
    if len(payload["pulls"]) != 5 or {p["image"] for p in payload["pulls"]} != expected:
        raise ValueError("Image handoff does not bind all five checked images.")
    authorize()
    # registry_pull uses private RAM-only per-image credentials and suppresses errors.
    with contextlib.redirect_stdout(io_sink()):
        try:
            pull.pull(payload, revision)
        except RuntimeError:
            raise ReleaseAccessError(
                "Original immutable-image preload needs bounded reconciliation."
            ) from None
    cleanup()
    return images_present(manifest, candidate)


def io_sink():
    import io

    return io.StringIO()


def process(request_path, client):
    request = read(request_path, 1000)
    job_id = str(UUID(request["id"]))
    checksum = request["checksum"]
    if (
        request_path.name != job_id + ".json"
        or set(request) != {"id", "checksum"}
        or not re.fullmatch(r"[a-f0-9]{64}", checksum)
    ):
        raise ValueError("Release spool identity is invalid.")
    path = OPERATIONS / "jobs" / (job_id + ".json")
    journal = read(path, 16 * 1024**2) if path.exists() else None
    if journal and journal.get("terminal"):
        repair_terminal_receipt(journal)
        return
    active, active_manifest = active_release()
    access = host_authorization(job_id, checksum, active)
    if journal is None:
        journal = {
            "id": job_id,
            "checksum": checksum,
            "task_id": access["task_id"],
            "phase": "queued",
            "attempts": 0,
            "created_at": datetime.now(UTC).isoformat(),
        }
        save(path, journal)
    if journal["checksum"] != checksum or journal["task_id"] != access["task_id"]:
        raise ValueError(
            "Release journal identity is already bound to different inputs."
        )
    if time.time() < journal.get("next_check", 0):
        return

    def authorize():
        current = host_authorization(job_id, checksum, active)
        if current["status"] == "paused":
            raise Paused()
        if current["status"] != "active":
            raise ValueError(
                "The original release goal was cancelled, stopped or expired."
            )
        if current["inputs"] != access["inputs"]:
            raise ValueError("Immutable release inputs changed.")

    try:
        # Reconcile an installation already in flight even if the originating
        # task has since paused. This never resumes the task or creates a new release.
        if not journal.get("installation"):
            if access["status"] == "cancelled":
                finish(journal, path, status="cancelled", phase="cancelled")
                return
            if (
                datetime.now(UTC) - datetime.fromisoformat(journal["created_at"])
            ).total_seconds() > 4 * 3600:
                raise ValueError("The shared release recovery deadline expired.")
            authorize()
        if "publication" not in journal:
            bundle = source(access, active_manifest)
            journal["publication"] = publish(
                client,
                request_id=job_id,
                bundle=bundle,
                revision=active_manifest["revision"],
                journal_directory=OPERATIONS / "publications",
                authorize=authorize,
                pr_base="main",
            )
            journal["phase"] = "source_published"
            journal["public_result"] = {
                "pull_request": journal["publication"]["pull_request"],
                "revision": journal["publication"]["commit"],
            }
            save(path, journal)
        revision = journal["publication"]["commit"]
        if "candidate" not in journal:
            found = client.repo("/git/ref/heads/" + journal["publication"]["branch"])
            if found["object"]["sha"] != revision:
                raise ValueError("Published release branch changed.")
            run = dispatch(
                client,
                journal,
                path,
                "release_run",
                journal["publication"]["branch"],
                {},
                authorize,
            )
            journal["phase"] = "following_release_checks"
            if not run or not completed_release(
                client, run, revision, journal["publication"]["branch"]
            ):
                journal["next_check"] = time.time() + 30
                save(path, journal)
                receipt(journal)
                return
            candidate, manifest = staged_release(
                client, journal, path, active, active_manifest
            )
        else:
            candidate = Path(journal["candidate"]["root"])
            manifest = read(candidate / "release-manifest.json")
        if not preload(client, journal, path, candidate, manifest, authorize):
            journal["next_check"] = time.time() + 10
            save(path, journal)
            receipt(journal)
            return
        if not journal.get("installation"):
            authorize()
        if "source_tree" not in journal:
            tree = client.repo("/git/trees/" + revision + "?recursive=1")
            if tree.get("truncated"):
                raise ValueError("The full release source tree cannot be verified.")
            journal["source_tree"] = tree["tree"]
        journal["phase"] = "installing_verified_release"
        save(path, journal)
        result = install(
            active_release=active,
            candidate=candidate,
            state=STATE,
            project=PROJECT,
            revision=revision,
            journal=journal,
            journal_path=path,
            authorize=authorize,
            tree=journal["source_tree"],
        )
        save(
            OPERATIONS / "active-release.json",
            {"revision": revision, "release_root": str(candidate)},
        )
        journal["public_result"].update(result)
        finish(journal, path, status="succeeded", phase="deployment_verified")
    except (PendingArtifact, EngineeringBusy):
        journal["next_check"] = time.time() + 30
        save(path, journal)
        receipt(journal)
    except Paused:
        progress = journal.get("installation", {})
        if progress:
            recovered = recover_before_migration(active, STATE, PROJECT, progress)
            journal["previous_services_recovered"] = recovered
            finish(
                journal,
                path,
                status="cancelled",
                phase="paused_installation_cancelled",
                error="Owner paused this installation before migration; previous services recovered. The project task remains paused.",
            )
        else:
            journal["next_check"] = time.time() + 30
            save(path, journal)
            receipt(journal)
    except ReleaseAccessError:
        journal["attempts"] += 1
        journal["next_check"] = time.time() + min(300, 10 * 2 ** journal["attempts"])
        if journal["attempts"] >= 5:
            finish(
                journal,
                path,
                status="failed",
                phase="recovery_exhausted",
                error="Repository access or transport recovery exhausted; reconcile original request.",
            )
        else:
            save(path, journal)
            receipt(journal)
    except Exception as error:
        if journal.get("terminal"):
            # A lost delivery never changes the already recorded outcome.
            raise
        # Preserve diagnostics privately; never return raw provider output or secrets.
        journal["last_error_type"] = type(error).__name__
        if hasattr(error, "diagnostics"):
            (OPERATIONS / "diagnostics").mkdir(mode=0o700, exist_ok=True)
            diagnostic = OPERATIONS / "diagnostics" / (job_id + ".log")
            fd = os.open(
                diagnostic, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "w") as handle:
                handle.write(error.diagnostics)
        # The original compose startup is idempotent and has a two-attempt
        # recovery budget recorded by the installer. Never replay a migration.
        progress = journal.get("installation", {})
        if (
            progress.get("phase") == "startup_requested"
            and progress.get("startup_attempts", 2) < 2
        ):
            journal["next_check"] = time.time() + 30
            save(path, journal)
            receipt(journal)
            return
        try:
            journal["previous_services_recovered"] = recover_before_migration(
                active, STATE, PROJECT, progress
            )
        except (
            RuntimeError,
            OSError,
            ValueError,
            subprocess.SubprocessError,
        ) as recovery_error:
            journal["preparation_recovery_error_type"] = type(recovery_error).__name__
        finish(
            journal,
            path,
            status="failed",
            phase="needs_operator_reconciliation",
            error="Release verification or installation stopped; inspect its original host journal and recovery backup.",
        )
        raise


def stop(*_):
    global STOP
    STOP = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("The separately installed fixed host bridge must run as root.")
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    with (OPERATIONS / "deploy.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        client = GitHubApp(OPERATIONS)
        while not STOP:
            for request in sorted((OPERATIONS / "spool/requests").glob("*.json")):
                try:
                    process(request, client)
                except Exception as error:  # noqa: BLE001 -- redact the fixed host boundary
                    # Exact private journal retains the phase. Root-only service
                    # logs contain a category rather than authenticated responses.
                    print(
                        "Release request needs reconciliation: "
                        + request.stem
                        + " ("
                        + type(error).__name__
                        + ")",
                        flush=True,
                    )
            if args.once:
                return
            time.sleep(5)


if __name__ == "__main__":
    main()
