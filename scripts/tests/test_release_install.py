"""Migration ambiguity and authority changes cannot become startup permission."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_install


class Installation(unittest.TestCase):
    def test_candidate_configuration_replaces_existing_private_file_and_interrupted_temporary(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            target = root / "server.env"
            target.write_text("IMAGE=old\nSETTING=preserved\n")
            pending = root / "server.env.release.pending"
            pending.write_text("interrupted")
            release_install.write_configuration(
                target, {"IMAGE": "checked", "SETTING": "preserved"}
            )
            self.assertEqual(target.read_text(), "IMAGE=checked\nSETTING=preserved\n")
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertFalse(pending.exists())
            target.unlink()
            target.symlink_to(root / "unrelated")
            with self.assertRaises(ValueError):
                release_install.write_configuration(target, {"IMAGE": "checked"})
            self.assertFalse((root / "unrelated").exists())

    def test_unknown_migration_or_changed_authority_never_replays_or_starts_workers(
        self,
    ):
        for schema, changed in (("old_schema", False), ("new_schema", True)):
            with (
                self.subTest(schema=schema, changed=changed),
                tempfile.TemporaryDirectory() as folder,
            ):
                root = Path(folder)
                active, candidate = root / "active", root / "candidate"
                active.mkdir()
                candidate.mkdir()
                images = {"IMAGE": "digest"}
                (active / "release-manifest.json").write_text(
                    json.dumps({"images": images})
                )
                (candidate / "release-manifest.json").write_text(
                    json.dumps({"revision": "a" * 40})
                )
                calls = []
                server = SimpleNamespace(
                    command=lambda *_: [],
                    read_env=lambda *_, value=images: value,
                    validate_images=lambda *_: None,
                    checksum=lambda *_: "checksum",
                    IMAGE_KEYS=["IMAGE"],
                    snapshot=lambda *_, version=schema, altered=changed: {
                        "schema": version,
                        "tables": {
                            "settings": {"sha256": "changed" if altered else "original"}
                        },
                    },
                    run=lambda *a, sink=calls, **kw: sink.append(a),
                )
                progress = {
                    "phase": "migration_requested",
                    "backup": str(root / "backup.tar.gz"),
                    "backup_result": {"sha256": "checksum"},
                    "expected_schema": "new_schema",
                    "before": {"tables": {"settings": {"sha256": "original"}}},
                    "protected": ["settings"],
                }
                with (
                    patch.object(release_install, "module", return_value=server),
                    self.assertRaises(ValueError),
                ):
                    release_install.install(
                        active_release=active,
                        candidate=candidate,
                        state=root,
                        project="fixture",
                        revision="a" * 40,
                        journal={"id": "fixture", "installation": progress},
                        journal_path=root / "journal.json",
                        authorize=lambda: None,
                        tree=[],
                    )
                self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
