"""Signature, source provenance and uncertain publication outcomes."""

import base64
import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_identity import ReleaseAccessError
from release_publish import checked_path, checksum, publish, signed_source


class SourceProof(unittest.TestCase):
    def setUp(self):
        self.secret = Ed25519PrivateKey.generate()
        self.public = self.secret.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.revision = "a" * 40
        self.identity = {"sha256:" + "b" * 64: self.revision}
        self.request = {
            "id": str(uuid4()), "task_id": str(uuid4()), "workspace_id": str(uuid4()),
            "purpose": "test", "script": "node --test scripts/tests/example.mjs", "timeout_seconds": 120,
        }
        self.request["checksum"] = hashlib.sha256(json.dumps(
            {k: v for k, v in self.request.items() if k != "id"}, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        self.bundle = {
            "job_id": self.request["id"], "workspace_id": self.request["workspace_id"],
            "image_id": next(iter(self.identity)), "tree_checksum": "c" * 64,
            "files": {"docs/implementation.md": "Verified implementation\n"},
            "deleted": [], "base_files": {"docs/implementation.md": None},
        }
        self.result = {
            "id": self.request["id"], "workspace_id": self.request["workspace_id"],
            "image_id": next(iter(self.identity)), "request_checksum": self.request["checksum"],
            "exit_code": 0, "tests_executed": 2, "test_runner": "node",
            "source_artifact_checksum": checksum(self.bundle), "source_tree_checksum": self.bundle["tree_checksum"],
        }

    def envelope(self):
        return {"result": self.result, "signature": base64.b64encode(self.secret.sign(
            json.dumps(self.result, sort_keys=True).encode()
        )).decode()}

    def verify(self, envelope=None):
        return signed_source(self.request, envelope or self.envelope(), self.bundle,
                             self.public, self.identity, self.revision)

    def test_exact_source_and_test_execution_are_required(self):
        self.assertEqual(self.verify(), self.bundle)
        envelope = self.envelope()
        envelope["result"] = {**self.result, "tests_executed": 200}
        with self.assertRaises(InvalidSignature):
            self.verify(envelope)
        self.bundle["files"]["docs/implementation.md"] = "Different untested source"
        with self.assertRaises(ValueError):
            self.verify()

    def test_old_image_cancelled_or_non_test_receipt_cannot_publish(self):
        original = copy.deepcopy(self.result)
        for changes in ({"tests_executed": 0}, {"exit_code": 1}, {"cancelled": True}, {"image_id": "sha256:unknown"}):
            with self.subTest(changes=changes):
                self.result = {**original, **changes}
                with self.assertRaises(ValueError):
                    self.verify()
        self.result = original
        self.request["purpose"] = "develop"
        with self.assertRaises(ValueError):
            self.verify()

    def test_protected_runtime_and_escaping_paths_require_trusted_installation(self):
        for path in ("../private.pem", "/srv/credentials", "docs/../AGENTS.md", ".github/workflows/checks.yml",
                     "scripts/release_bridge.py", "scripts/server.py", "deploy/compose.server.yml",
                     "backend/tauric/agent_tasks.py", "frontend/node_modules/example.js"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                checked_path(path)
        self.assertEqual(checked_path("backend/tauric/new_feature.py"), "backend/tauric/new_feature.py")
        self.assertEqual(checked_path("backend/migrations/0037_feature.sql"), "backend/migrations/0037_feature.sql")


class Repository:
    def __init__(self):
        self.ref = None
        self.pulls = []
        self.ref_creates = self.pull_creates = 0
        self.mutations = []

    def repo(self, path, *, method="GET", body=None):
        if method == "POST":
            self.mutations.append(path)
        if path.startswith("/git/commits/"):
            return {"tree": {"sha": "baseline-tree"}}
        if path.startswith("/git/trees/"):
            return {"truncated": False, "tree": []}
        if path == "/git/blobs":
            return {"sha": "new-blob"}
        if path == "/git/trees":
            return {"sha": "new-tree"}
        if path == "/git/commits":
            return {"sha": "b" * 40}
        if path.startswith("/git/ref/heads/"):
            if not self.ref:
                raise ReleaseAccessError("Not found", status=404)
            return self.ref
        if path == "/git/refs":
            self.ref_creates += 1
            self.ref = {"object": {"sha": body["sha"]}}
            raise ReleaseAccessError("Synthetic lost reference response")
        if path.startswith("/pulls?"):
            return self.pulls
        if path == "/pulls":
            self.pull_creates += 1
            self.pulls = [{"head": {"sha": "b" * 40}, "base": {"ref": body["base"]},
                           "html_url": "https://github.com/aoodie/Tauric/pull/100"}]
            raise ReleaseAccessError("Synthetic lost pull request response")
        raise AssertionError("Unexpected repository call")


class Publication(unittest.TestCase):
    def test_lost_responses_reconcile_original_branch_and_pull_request(self):
        repository = Repository()
        bundle = {"files": {"docs/new.md": "New verified source"}, "deleted": [], "base_files": {"docs/new.md": None}}
        with tempfile.TemporaryDirectory() as directory:
            args = {"request_id": str(uuid4()), "bundle": bundle, "revision": "a" * 40,
                    "journal_directory": Path(directory), "authorize": lambda: None, "pr_base": "codex/trader-dashboard"}
            first = publish(repository, **args)
            mutations = len(repository.mutations)
            second = publish(repository, **args)
            self.assertEqual(first, second)
            self.assertEqual(len(repository.mutations), mutations)
            self.assertEqual(repository.ref_creates, 1)
            self.assertEqual(repository.pull_creates, 1)
            repository.ref["object"]["sha"] = "c" * 40
            with self.assertRaises(ValueError):
                publish(repository, **args)

    def test_live_authorization_stops_publication_before_a_repository_mutation(self):
        repository = Repository()
        def paused():
            raise ValueError("Owner paused the task")
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            publish(repository, request_id=str(uuid4()), bundle={"files": {"docs/new.md": "Source"}, "deleted": [], "base_files": {"docs/new.md": None}},
                    revision="a" * 40, journal_directory=Path(directory), authorize=paused,
                    pr_base="codex/trader-dashboard")
        self.assertFalse(repository.mutations)


if __name__ == "__main__":
    unittest.main()
