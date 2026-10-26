"""Publish only an independently signed, executed source artifact.

The host service supplies live task authorization and an exact installed base.
No path, repository, branch command or credential is accepted from source code.
"""

import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from release_identity import ReleaseAccessError

UTC = timezone.utc  # Native host tools support Python 3.10.
PROTECTED_PREFIXES = (".github/", "deploy/")
PROTECTED_FILES = {
    "AGENTS.md",
    "docs/OPENCLAW_PROJECT_OWNERSHIP.md",
    ".dockerignore",
    "scripts/server.py",
    "scripts/shared_ingress.py",
    "scripts/research_release.py",
    "scripts/quant_release.py",
    "scripts/quant_host.py",
    "scripts/trusted_research_install.py",
    "scripts/backup_vault.py",
    "scripts/operations_watch.py",
    "scripts/maintenance_backup.py",
    "scripts/project_tree.py",
    "scripts/engineering_host.py",
    "scripts/engineering_install.py",
    "scripts/engineering_entry.py",
    "scripts/registry_pull.py",
    "scripts/registry_handoff.mjs",
    "scripts/release_identity.py",
    "scripts/release_publish.py",
    "scripts/release_bridge.py",
    "scripts/release_install.py",
    "scripts/release_bridge_install.py",
    "scripts/release_workflow.py",
    "scripts/server_soak.py",
    "scripts/acceptance_research.py",
    "scripts/release_bundle.py",
    "scripts/release_smoke.py",
    "scripts/engineering_smoke.py",
    "backend/tauric/auth.py",
    "backend/tauric/agent_api.py",
    "backend/tauric/agent_tasks.py",
    "backend/tauric/agent_contracts.py",
    "backend/tauric/engineering.py",
    "backend/tauric/release_jobs.py",
    "backend/tauric/release_contracts.py",
}
SOURCE_PREFIXES = ("backend/", "frontend/", "openclaw/", "docs/", "scripts/")


def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def checked_path(value):
    if (
        not isinstance(value, str)
        or len(value) > 240
        or str(PurePosixPath(value)) != value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or ".." in PurePosixPath(value).parts
        or any(part.startswith(".") for part in PurePosixPath(value).parts)
        or not (value.startswith(SOURCE_PREFIXES) or value == "README.md")
        or value.startswith(PROTECTED_PREFIXES)
        or value in PROTECTED_FILES
        or "node_modules" in PurePosixPath(value).parts
        or "artifacts" in PurePosixPath(value).parts
    ):
        raise ValueError(
            "The signed change requires the separate trusted installation path."
        )
    return value


def signed_source(
    request, envelope, bundle, public_pem, identities, installed_revision
):
    """Verify both the execution and its exact source, independently of the API."""
    public = load_pem_public_key(public_pem)
    if not isinstance(public, Ed25519PublicKey):
        raise TypeError("The trusted execution signing identity is required.")
    result = envelope["result"]
    public.verify(
        base64.b64decode(envelope["signature"], validate=True),
        json.dumps(result, sort_keys=True).encode(),
    )
    original = {k: v for k, v in request.items() if k not in {"id", "checksum"}}
    request_digest = hashlib.sha256(
        json.dumps(original, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    if (
        request.get("checksum") != request_digest
        or request.get("purpose") != "test"
        or result.get("request_checksum") != request_digest
        or result.get("id") != request.get("id")
        or result.get("workspace_id") != request.get("workspace_id")
        or result.get("cancelled")
        or result.get("exit_code") != 0
        or type(result.get("tests_executed")) is not int
        or result["tests_executed"] < 1
        or not result.get("test_runner")
        or identities.get(result.get("image_id")) != installed_revision
        or bundle.get("job_id") != request["id"]
        or bundle.get("workspace_id") != request["workspace_id"]
        or bundle.get("image_id") != result.get("image_id")
        or checksum(bundle) != result.get("source_artifact_checksum")
        or bundle.get("tree_checksum") != result.get("source_tree_checksum")
        or not re.fullmatch(r"[a-f0-9]{40}", installed_revision)
    ):
        raise ValueError(
            "The signed source does not prove successful tests on the installed base."
        )
    paths = set(bundle["files"]) | set(bundle["deleted"])
    if (
        not paths
        or len(paths) > 200
        or set(bundle["base_files"]) != paths
        or set(bundle["files"]) & set(bundle["deleted"])
        or len(bundle["deleted"]) != len(set(bundle["deleted"]))
        or len(json.dumps(bundle).encode()) > 16 * 1024**2
    ):
        raise ValueError("The source patch exceeds the fixed publication allowance.")
    for path in paths:
        checked_path(path)
        baseline = bundle["base_files"][path]
        if baseline is not None and not re.fullmatch(r"[a-f0-9]{64}", baseline):
            raise ValueError("Invalid baseline checksum.")
    if any(
        not isinstance(text, str) or len(text.encode()) > 2 * 1024**2
        for text in bundle["files"].values()
    ):
        raise ValueError("Only bounded UTF-8 source files can be published.")
    return bundle


def save(path, value, *, mode=0o600):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("The release journal cannot be a symlink.")
    temporary = path.with_name(path.name + ".pending")
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    os.fchmod(fd, mode)
    with os.fdopen(fd, "w") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def publish(
    client, *, request_id, bundle, revision, journal_directory, authorize, pr_base
):
    """Stable commit/ref/PR identities reconcile a lost response without replaying source."""
    request_id = str(UUID(request_id))
    if not re.fullmatch(r"[a-f0-9]{40}", revision) or not re.fullmatch(
        r"[a-zA-Z0-9_./-]{1,180}", pr_base
    ):
        raise ValueError("An exact release base and repository branch are required.")
    path = journal_directory / (request_id + ".json")
    inputs = {
        "base_revision": revision,
        "artifact_checksum": checksum(bundle),
        "pr_base": pr_base,
    }
    if path.exists():
        journal = json.loads(path.read_text())
        if journal["inputs"] != inputs:
            raise ValueError(
                "Publication identity is already bound to different source."
            )
    else:
        journal = {
            "inputs": inputs,
            "committed_at": datetime.now(UTC).isoformat(),
            "branch": "codex/openclaw/" + request_id,
        }
        save(path, journal)
    if journal.get("pull_request"):
        # Re-read the branch so an external change cannot become deployment authority.
        found = client.repo("/git/ref/heads/" + journal["branch"])
        if found["object"]["sha"] != journal["commit"]:
            raise ValueError("Published source branch changed after its receipt.")
        return journal
    parent = client.repo("/git/commits/" + revision)
    tree = client.repo("/git/trees/" + parent["tree"]["sha"] + "?recursive=1")
    if tree.get("truncated"):
        raise ValueError("The repository tree cannot be fully verified.")
    existing = {item["path"]: item for item in tree["tree"]}
    changes = []
    for name in sorted(set(bundle["files"]) | set(bundle["deleted"])):
        checked_path(name)
        previous = existing.get(name)
        baseline = bundle["base_files"][name]
        if previous:
            if previous["type"] != "blob" or previous["mode"] not in {
                "100644",
                "100755",
            }:
                raise ValueError("Only regular source files can be changed.")
            blob = client.repo("/git/blobs/" + previous["sha"])
            data = base64.b64decode(blob["content"], validate=False)
            if hashlib.sha256(data).hexdigest() != baseline:
                raise ValueError(
                    "The signed source baseline differs from the repository."
                )
        elif baseline is not None or name in bundle["deleted"]:
            raise ValueError("The signed source baseline path is missing.")
        mode = previous["mode"] if previous else "100644"
        if name in bundle["deleted"]:
            changes.append({"path": name, "mode": mode, "type": "blob", "sha": None})
        else:
            authorize()
            blob = client.repo(
                "/git/blobs",
                method="POST",
                body={
                    "content": base64.b64encode(
                        bundle["files"][name].encode()
                    ).decode(),
                    "encoding": "base64",
                },
            )
            changes.append(
                {"path": name, "mode": mode, "type": "blob", "sha": blob["sha"]}
            )
    authorize()
    candidate = client.repo(
        "/git/trees",
        method="POST",
        body={"base_tree": parent["tree"]["sha"], "tree": changes},
    )
    actor = {
        "name": "Tauric OpenClaw Release Bridge[bot]",
        "email": "tauric-openclaw-release-bridge[bot]@users.noreply.github.com",
        "date": journal["committed_at"],
    }
    authorize()
    commit = client.repo(
        "/git/commits",
        method="POST",
        body={
            "message": "Publish verified OpenClaw source\n\nSource artifact: "
            + inputs["artifact_checksum"],
            "tree": candidate["sha"],
            "parents": [revision],
            "author": actor,
            "committer": actor,
        },
    )
    if journal.get("commit") and journal["commit"] != commit["sha"]:
        raise ValueError("Stable publication commit identity changed.")
    journal["commit"] = commit["sha"]
    save(path, journal)
    try:
        found = client.repo("/git/ref/heads/" + journal["branch"])
    except ReleaseAccessError as error:
        if error.status != 404:
            raise
        authorize()
        try:
            found = client.repo(
                "/git/refs",
                method="POST",
                body={
                    "ref": "refs/heads/" + journal["branch"],
                    "sha": journal["commit"],
                },
            )
        except ReleaseAccessError:
            # An unknown create outcome must be reconciled against the original ref.
            found = client.repo("/git/ref/heads/" + journal["branch"])
    if found["object"]["sha"] != journal["commit"]:
        raise ValueError("The stable source branch contains a different commit.")
    pulls = client.repo(
        "/pulls?state=all&head=aoodie:" + journal["branch"] + "&base=" + pr_base
    )
    if len(pulls) > 1:
        raise ValueError("Publication has conflicting pull request identities.")
    if pulls:
        pull = pulls[0]
    else:
        authorize()
        try:
            pull = client.repo(
                "/pulls",
                method="POST",
                body={
                    "title": "OpenClaw: verified project source",
                    "head": journal["branch"],
                    "base": pr_base,
                    "draft": True,
                    "body": "This change publishes the exact source from a successful signed engineering test execution.\n\nSource artifact: `"
                    + inputs["artifact_checksum"]
                    + "`.\n\nDeployment still requires the fixed release checks, backup restoration, installation and independent readiness verification. Financial settings and owner pauses remain mandatory.",
                },
            )
        except ReleaseAccessError:
            pulls = client.repo(
                "/pulls?state=all&head=aoodie:" + journal["branch"] + "&base=" + pr_base
            )
            if len(pulls) != 1:
                raise ReleaseAccessError(
                    "Pull request outcome requires reconciliation."
                ) from None
            pull = pulls[0]
    if pull["head"]["sha"] != journal["commit"] or pull["base"]["ref"] != pr_base:
        raise ValueError("The pull request does not bind the exact verified commit.")
    journal["pull_request"] = pull["html_url"]
    journal["published"] = True
    save(path, journal)
    return journal
