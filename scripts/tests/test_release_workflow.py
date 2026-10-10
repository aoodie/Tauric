"""Actual release job identities, lost dispatches and archive boundaries."""

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_identity import REPOSITORY_ID, ReleaseAccessError
from release_workflow import (
    REQUIRED_JOBS,
    completed_release,
    deployment_archive,
    dispatch,
    zip_files,
)


class WorkflowRepository:
    def __init__(self):
        self.runs = []
        self.effects = 0
        self.run = {
            "id": 71,
            "head_sha": "a" * 40,
            "head_branch": "codex/openclaw/fixture",
            "path": ".github/workflows/release.yml",
            "event": "workflow_dispatch",
            "repository": {"id": REPOSITORY_ID},
            "status": "completed",
            "conclusion": "success",
        }
        self.jobs = [{"name": n, "conclusion": "success"} for n in REQUIRED_JOBS]

    def repo(self, path, method="GET", body=None):
        if method == "POST":
            self.effects += 1
            self.runs = [self.run]
            raise ReleaseAccessError("Lost dispatch response")
        if "/jobs?" in path:
            return {"total_count": len(self.jobs), "jobs": self.jobs}
        if "/workflows/" in path:
            return {"workflow_runs": self.runs}
        return self.run


class Workflows(unittest.TestCase):
    def test_lost_dispatch_reconciles_once_and_never_resubmits(self):
        client = WorkflowRepository()
        journal = {}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.json"
            args = (
                client,
                journal,
                path,
                "release_run",
                client.run["head_branch"],
                {},
                lambda: None,
            )
            self.assertIsNone(dispatch(*args))
            run = dispatch(*args)
            self.assertEqual(run["id"], 71)
            self.assertEqual(dispatch(*args)["id"], 71)
            self.assertEqual(client.effects, 1)
            self.assertEqual(json.loads(path.read_text())["release_run"]["run_id"], 71)

    def test_owner_pause_prevents_dispatch_before_any_external_effect(self):
        client = WorkflowRepository()

        def paused():
            raise PermissionError("Owner paused")

        with (
            tempfile.TemporaryDirectory() as folder,
            self.assertRaises(PermissionError),
        ):
            dispatch(
                client,
                {},
                Path(folder) / "journal.json",
                "run",
                "codex/openclaw/fixture",
                {},
                paused,
            )
        self.assertEqual(client.effects, 0)

    def test_checks_must_match_source_and_all_actual_matrix_job_names(self):
        client = WorkflowRepository()
        self.assertTrue(
            completed_release(client, client.run, "a" * 40, "codex/openclaw/fixture")
        )
        for change in (
            {"head_sha": "b" * 40},
            {"event": "pull_request"},
            {"repository": {"id": 1}},
            {"conclusion": "failure"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                completed_release(
                    client, {**client.run, **change}, "a" * 40, "codex/openclaw/fixture"
                )
        client.jobs[0]["conclusion"] = "skipped"
        with self.assertRaises(ValueError):
            completed_release(client, client.run, "a" * 40, "codex/openclaw/fixture")


def tar(files, *, extra=None):
    value = io.BytesIO()
    with tarfile.open(fileobj=value, mode="w:gz") as archive:
        for name, data in files.items():
            item = tarfile.TarInfo("tauric-release/" + name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
        if extra:
            archive.addfile(extra)
    return value.getvalue()


class Archives(unittest.TestCase):
    def setUp(self):
        self.files = {
            "scripts/fixed.py": b"fixed runtime\n",
            "images.env": b"images\n",
            "engineering-image.json": b"{}",
        }
        self.manifest = {
            "revision": "a" * 40,
            "repository": "aoodie/Tauric",
            "files": {n: hashlib.sha256(v).hexdigest() for n, v in self.files.items()},
        }
        self.files["release-manifest.json"] = json.dumps(self.manifest).encode()
        self.protected = {
            "scripts/fixed.py": self.manifest["files"]["scripts/fixed.py"]
        }

    def verify(self, value):
        return deployment_archive(value, "a" * 40, {"scripts/fixed.py"}, self.protected)

    def test_complete_manifest_and_fixed_host_components_are_bound(self):
        self.assertEqual(self.verify(tar(self.files))[0], self.files)
        self.protected["scripts/fixed.py"] = "b" * 64
        with self.assertRaises(ValueError):
            self.verify(tar(self.files))

    def test_traversal_symlinks_extra_files_and_changed_contents_are_rejected(self):
        for name, kind in (
            ("tauric-release/../private.pem", tarfile.REGTYPE),
            ("tauric-release/link", tarfile.SYMTYPE),
        ):
            extra = tarfile.TarInfo(name)
            extra.type = kind
            extra.linkname = "/srv/private-key"
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.verify(tar(self.files, extra=extra))
        for files in (
            {**self.files, "scripts/unexpected.py": b"extra"},
            {**self.files, "scripts/fixed.py": b"altered"},
        ):
            with self.assertRaises(ValueError):
                self.verify(tar(files))

    def test_zip_reads_only_the_expected_bounded_names(self):
        value = io.BytesIO()
        with zipfile.ZipFile(value, "w") as archive:
            archive.writestr("release.json", "{}")
        self.assertEqual(
            zip_files(value.getvalue(), {"release.json"}), {"release.json": b"{}"}
        )
        with self.assertRaises(ValueError):
            zip_files(value.getvalue(), {"other.json"})


if __name__ == "__main__":
    unittest.main()
