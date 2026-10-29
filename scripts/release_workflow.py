"""Follow original GitHub run identities and verify bounded release artifacts."""

import hashlib
import io
import json
import re
import tarfile
import zipfile
from datetime import datetime, timezone
from pathlib import PurePosixPath
from urllib.parse import quote

from release_identity import REPOSITORY_ID, ReleaseAccessError
from release_publish import save

UTC = timezone.utc  # Native host tools support Python 3.10.
WORKFLOW = ".github/workflows/release.yml"
REQUIRED_JOBS = {"checks / checks", "checks / quant-runtimes", "checks / host-tools-310", "image-security", "bundle"} | {
    "images (" + kind + ", " + dockerfile + ")"
    for kind, dockerfile in {
        "api": "deploy/Dockerfile.release-api",
        "worker": "deploy/Dockerfile.release-worker",
        "web": "deploy/Dockerfile.web",
        "openclaw": "deploy/Dockerfile.openclaw",
        "engineering": "deploy/Dockerfile.engineering",
    }.items()
}


class PendingArtifact(Exception):
    """The original run has not uploaded its expected artifact yet."""


def dispatch(client, journal, path, key, branch, inputs, authorize):
    """An uncertain dispatch is observed, never reissued with a fresh identity."""
    old = journal.get(key)
    endpoint = (
        "/actions/workflows/release.yml/runs?event=workflow_dispatch&branch="
        + quote(branch, safe="")
        + "&per_page=100"
    )
    runs = client.repo(endpoint)["workflow_runs"]
    if old is None:
        old = {
            "before": [r["id"] for r in runs],
            "requested_at": datetime.now(UTC).isoformat(),
        }
        journal[key] = old
        authorize()
        save(path, journal)  # Intent precedes the external effect.
        try:
            result = client.repo(
                "/actions/workflows/release.yml/dispatches",
                method="POST",
                body={
                    "ref": branch,
                    "inputs": inputs,
                    "return_run_details": True,
                },
            )
        except ReleaseAccessError:
            return None
        if result and type(result.get("workflow_run_id")) is int:
            old["run_id"] = result["workflow_run_id"]
            save(path, journal)
    if old.get("run_id"):
        return client.repo("/actions/runs/" + str(old["run_id"]))
    candidates = [
        r for r in runs if r["id"] not in old["before"] and r.get("path") == WORKFLOW
    ]
    if len(candidates) > 1:
        raise ValueError(
            "Workflow dispatch has conflicting run identities; reconcile the original request."
        )
    if candidates:
        old["run_id"] = candidates[0]["id"]
        save(path, journal)
        return candidates[0]
    if (
        datetime.now(UTC) - datetime.fromisoformat(old["requested_at"])
    ).total_seconds() > 300:
        raise ValueError(
            "Workflow dispatch outcome remains unknown; it was not resubmitted."
        )
    return None


def run_identity(run, revision, branch):
    if (
        run.get("head_sha") != revision
        or run.get("head_branch") != branch
        or run.get("path") != WORKFLOW
        or run.get("event") != "workflow_dispatch"
        or run.get("repository", {}).get("id") != REPOSITORY_ID
    ):
        raise ValueError("Workflow run is not bound to the exact published source.")
    if run.get("status") != "completed":
        return False
    if run.get("conclusion") != "success":
        raise ValueError(
            "The original workflow failed; no release installation is authorized."
        )
    return True


def completed_release(client, run, revision, branch):
    if not run_identity(run, revision, branch):
        return False
    response = client.repo("/actions/runs/" + str(run["id"]) + "/jobs?per_page=100")
    if response["total_count"] > 100:
        raise ValueError("The complete release job set could not be verified.")
    successful = {
        j["name"] for j in response["jobs"] if j.get("conclusion") == "success"
    }
    if not REQUIRED_JOBS <= successful:
        raise ValueError(
            "Required checks, image security scans, five builds or installation rehearsal did not pass."
        )
    return True


def artifact(client, run_id, name):
    response = client.repo("/actions/runs/" + str(run_id) + "/artifacts?per_page=100")
    if response["total_count"] > 100:
        raise ValueError("Artifact list exceeded its allowance.")
    found = [
        a for a in response["artifacts"] if a["name"] == name and not a.get("expired")
    ]
    if not found:
        raise PendingArtifact()
    if len(found) != 1:
        raise ValueError("The original workflow has conflicting artifact identities.")
    entry = found[0]
    if entry.get("workflow_run", {}).get("id") not in {None, run_id}:
        raise ValueError("Artifact belongs to a different workflow run.")
    value = client.artifact(entry["id"])
    expected = entry.get("digest")
    if (
        not isinstance(expected, str)
        or expected != "sha256:" + hashlib.sha256(value).hexdigest()
    ):
        raise ValueError("Downloaded artifact failed its GitHub digest binding.")
    return value, entry["id"]


def zip_files(value, expected):
    with zipfile.ZipFile(io.BytesIO(value)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or set(names) != set(expected):
            raise ValueError("Release artifact contains unexpected or duplicate files.")
        if sum(i.file_size for i in archive.infolist()) > 64 * 1024**2:
            raise ValueError("Release artifact exceeds its expanded allowance.")
        return {name: archive.read(name) for name in names}


def deployment_archive(value, revision, allowed_files, protected_hashes):
    """Validate the whole archive before writing or loading any candidate code."""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(value), mode="r:gz") as archive:
        size = 0
        for member in archive:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.name
                or not path.parts
                or path.parts[0] != "tauric-release"
                or str(path) != member.name.rstrip("/")
                or not (member.isdir() or member.isfile())
            ):
                raise ValueError("Unsafe release archive entry.")
            if member.isdir():
                continue
            name = path.relative_to("tauric-release").as_posix()
            size += member.size
            if name in files or size > 64 * 1024**2 or member.size > 16 * 1024**2:
                raise ValueError("Duplicate or oversized release archive entry.")
            files[name] = archive.extractfile(member).read()
    required = set(allowed_files) | {
        "images.env",
        "engineering-image.json",
        "release-manifest.json",
    }
    if set(files) != required:
        raise ValueError(
            "Release archive does not contain the complete fixed file set."
        )
    manifest = json.loads(files["release-manifest.json"])
    if (
        manifest.get("revision") != revision
        or manifest.get("repository", "").lower() != "aoodie/tauric"
    ):
        raise ValueError("Release archive belongs to another repository or source.")
    if set(manifest["files"]) != required - {"release-manifest.json"}:
        raise ValueError("Release manifest omitted or added files.")
    for name, expected in manifest["files"].items():
        if hashlib.sha256(files[name]).hexdigest() != expected:
            raise ValueError("Release file checksum failed.")
    for name, expected in protected_hashes.items():
        if name not in files or hashlib.sha256(files[name]).hexdigest() != expected:
            raise ValueError(
                "A fixed host or workflow component changed; use the trusted installation path."
            )
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("An exact source revision is required.")
    return files, manifest


def acceptance(files, manifest):
    smoke = json.loads(files["release-smoke.json"])
    engineering = json.loads(files["engineering-smoke.json"])
    attestation = engineering.get("independent_test_attestation")
    if "independent_test_attestation" not in engineering:
        # Legacy artifacts use the previous execution-count field.
        executed_tests = engineering.get("tests_executed")
        independent_tests_verified = type(executed_tests) is int and executed_tests >= 1
    elif attestation is True:
        executed_tests = engineering.get("tests_executed")
        independent_tests_verified = type(executed_tests) is int and executed_tests >= 1
    elif attestation is False:
        executed_tests = engineering.get("independent_api_smoke_cases")
        independent_tests_verified = (
            type(executed_tests) is int
            and executed_tests >= 1
            and type(engineering.get("tests_reported")) is int
            and engineering["tests_reported"] >= 1
            and engineering.get(
                "forged_workspace_report_rejected_as_independent_proof"
            ) is True
        )
    else:
        executed_tests = None
        independent_tests_verified = False
    required = (
        "fresh_install_ready",
        "test_tools_excluded",
        "supervisor_saved_chat_configuration",
        "owner_login_and_saved_configuration",
        "api_restart_persistence",
        "whole_stack_restart_persistence",
    )
    if (
        not all(smoke.get(k) is True for k in required)
        or smoke.get("tested_images") != manifest["images"]
        or not smoke.get("restore", {}).get("table_hashes_match")
        or smoke.get("restore", {}).get("background_services_started") is not False
        or smoke.get("model_calls") != 0
        or smoke.get("broker_calls") != 0
        or not independent_tests_verified
        or engineering.get("model_calls") != 0
        or engineering.get("broker_calls") != 0
    ):
        raise ValueError(
            "The release lacks its required installation and isolated execution evidence."
        )
    return {
        "release_checks_verified": True,
        "executed_engineering_tests": executed_tests,
    }
