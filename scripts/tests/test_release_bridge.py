"""The host follows one durable publication while owner controls remain current."""

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_bridge
from test_release_publish import Repository


class FollowingRepository(Repository):
    def __init__(self):
        super().__init__()
        self.dispatches = 0

    def repo(self, path, *, method="GET", body=None):
        if path.startswith("/actions/workflows/") and method == "GET":
            return {"workflow_runs": []}
        if path.endswith("/dispatches"):
            self.dispatches += 1
            return {"workflow_run_id": 17}
        if path.startswith("/actions/runs/"):
            return {
                "id": 17,
                "head_sha": "b" * 40,
                "head_branch": self.branch,
                "path": ".github/workflows/release.yml",
                "event": "workflow_dispatch",
                "repository": {"id": 1405263842},
                "status": "in_progress",
            }
        if path == "/git/refs":
            self.branch = body["ref"].removeprefix("refs/heads/")
        return super().repo(path, method=method, body=body)


class Bridge(unittest.TestCase):
    def test_pause_resume_follow_and_cancel_never_duplicate_repository_effects(self):
        key = Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory() as folder:
            operations = Path(folder)
            requests = operations / "spool/requests"
            requests.mkdir(parents=True)
            job_id, task_id = str(uuid4()), str(uuid4())
            checksum = "c" * 64
            request = requests / (job_id + ".json")
            request.write_text(json.dumps({"id": job_id, "checksum": checksum}))
            secret = operations / "release-private.pem"
            secret.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            secret.chmod(0o600)
            access = {
                "id": job_id,
                "checksum": checksum,
                "task_id": task_id,
                "inputs": {},
                "status": "paused",
            }
            bundle = {
                "files": {"docs/verified.md": "Verified source"},
                "deleted": [],
                "base_files": {"docs/verified.md": None},
            }
            clock = [1000]
            client = FollowingRepository()
            with (
                patch.object(release_bridge, "OPERATIONS", operations),
                patch.object(
                    release_bridge,
                    "active_release",
                    return_value=(operations, {"revision": "a" * 40}),
                ),
                patch.object(
                    release_bridge,
                    "host_authorization",
                    side_effect=lambda *_: access.copy(),
                ),
                patch.object(release_bridge, "source", return_value=bundle),
                patch.object(release_bridge.time, "time", side_effect=lambda: clock[0]),
            ):
                release_bridge.process(request, client)
                self.assertEqual(client.mutations, [])
                access["status"] = "active"
                clock[0] += 31
                release_bridge.process(request, client)
                self.assertEqual(client.ref_creates, 1)
                self.assertEqual(client.pull_creates, 1)
                self.assertEqual(client.pulls[0]["base"]["ref"], "main")
                self.assertEqual(client.dispatches, 1)
                count = len(client.mutations)
                clock[0] += 31
                release_bridge.process(request, client)
                self.assertEqual(len(client.mutations), count)
                self.assertEqual(client.dispatches, 1)
                access["status"] = "cancelled"
                clock[0] += 31
                release_bridge.process(request, client)
                access["status"] = "active"
                clock[0] += 31
                release_bridge.process(request, client)
                self.assertEqual(len(client.mutations), count)
            receipt = operations / "spool/receipts" / (job_id + ".json")
            envelope = json.loads(receipt.read_text())
            key.public_key().verify(
                base64.b64decode(envelope["signature"]),
                json.dumps(envelope["result"], sort_keys=True).encode(),
            )
            self.assertEqual(envelope["result"]["status"], "cancelled")
            self.assertEqual(receipt.stat().st_mode & 0o777, 0o644)
            self.assertEqual(
                (operations / "jobs" / (job_id + ".json")).stat().st_mode & 0o777, 0o600
            )
            self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_identity_spool_never_follows_a_symlink_or_accepts_an_oversized_record(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            private = root / "private.json"
            private.write_text('{"private":"not a spool identity"}')
            link = root / "link.json"
            link.symlink_to(private)
            with self.assertRaises(OSError):
                release_bridge.read(link)
            with self.assertRaises(ValueError):
                release_bridge.read(private, limit=3)
            fifo = root / "pipe.json"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                release_bridge.read(fifo)

    def test_restart_delivers_recorded_terminal_outcome_without_authorizing_or_replaying(
        self,
    ):
        key = Ed25519PrivateKey.generate()
        with tempfile.TemporaryDirectory() as folder:
            operations = Path(folder)
            job_id = str(uuid4())
            secret = operations / "release-private.pem"
            secret.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
            path = operations / "jobs" / (job_id + ".json")
            request = operations / (job_id + ".json")
            request.write_text(json.dumps({"id": job_id, "checksum": "c" * 64}))
            journal = {"id": job_id, "checksum": "c" * 64, "task_id": str(uuid4())}
            with patch.object(release_bridge, "OPERATIONS", operations):
                release_bridge.receipt(journal)
                with (
                    patch.object(
                        release_bridge, "receipt", side_effect=OSError("lost delivery")
                    ),
                    self.assertRaises(OSError),
                ):
                    release_bridge.finish(
                        journal,
                        path,
                        status="succeeded",
                        phase="deployment_verified",
                    )
                with (
                    patch.object(
                        release_bridge, "active_release", return_value=(operations, {})
                    ),
                    patch.object(
                        release_bridge,
                        "host_authorization",
                        side_effect=AssertionError("no new effects"),
                    ),
                ):
                    release_bridge.process(request, None)
            envelope = json.loads(
                (operations / "spool/receipts" / (job_id + ".json")).read_text()
            )
            self.assertEqual(envelope["result"]["status"], "succeeded")
            key.public_key().verify(
                base64.b64decode(envelope["signature"]),
                json.dumps(envelope["result"], sort_keys=True).encode(),
            )


if __name__ == "__main__":
    unittest.main()
