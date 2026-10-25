"""Migration ambiguity and authority changes cannot become startup permission."""

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import release_install


class Installation(unittest.TestCase):
    def test_shared_ingress_drift_stops_installation_before_any_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            active, candidate = root / "active", root / "candidate"
            (active / "deploy").mkdir(parents=True)
            candidate.mkdir()
            files = {}
            for name in ("Caddyfile.https", "Caddyfile.public-ip", "compose.https.yml", "compose.public-ip.yml"):
                path = active / "deploy" / name
                path.write_text("original ingress")
                files["deploy/" + name] = hashlib.sha256(path.read_bytes()).hexdigest()
            manifest = {"images": {"IMAGE": "digest"}, "files": files}
            release_install.verify_active_ingress(active, manifest)
            (active / "deploy/Caddyfile.https").write_text("original ingress\nquantdinger route")
            (active / "release-manifest.json").write_text(json.dumps(manifest))
            (candidate / "release-manifest.json").write_text(json.dumps({"revision": "a" * 40}))
            authorize, effects = Mock(), Mock()
            server = SimpleNamespace(
                command=lambda *_: ["compose"], read_env=lambda *_: {"IMAGE": "digest"},
                validate_images=lambda *_: None, run=effects,
            )
            with (
                patch.object(release_install, "module", return_value=server),
                self.assertRaisesRegex(ValueError, "preserve shared routes"),
            ):
                release_install.install(
                    active_release=active, candidate=candidate, state=root,
                    project="fixture", revision="a" * 40, journal={},
                    journal_path=root / "journal.json", authorize=authorize, tree=[],
                )
            authorize.assert_not_called()
            effects.assert_not_called()

    def test_startup_and_busy_recovery_preserve_disabled_service_profiles(self):
        services = ["db", "api", "web", "worker", "openclaw", "chat_worker"]

        class StartupObserved(Exception):
            pass

        for phase in ("startup_requested", "freeze_requested"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                active, candidate = root / "active", root / "candidate"
                active.mkdir()
                candidate.mkdir()
                images = {"IMAGE": "digest"}
                (active / "release-manifest.json").write_text(json.dumps({"images": images}))
                (candidate / "release-manifest.json").write_text(json.dumps({"revision": "a" * 40}))
                starts = []

                def run(command, *, stage=phase, recorded=starts, **_):
                    if "up" in command:
                        # Without explicit selection Compose activates every enabled
                        # profile, including the previously disabled execution lane.
                        selected = command[command.index("600") + 1:]
                        recorded.append(selected or [*services, "broker_execution", "valuation"])
                        self.assertIn("--no-deps", command)
                        if stage == "startup_requested":
                            raise StartupObserved()
                    return SimpleNamespace(stdout=b"")

                server = SimpleNamespace(
                    command=lambda *_: ["compose"], read_env=lambda *_, value=images: value,
                    validate_images=lambda *_: None, validate=lambda *_: None,
                    checksum=lambda *_: "checksum", IMAGE_KEYS=["IMAGE"], run=run,
                )
                progress = {
                    "phase": phase, "previous_services": services.copy(),
                    "old_images": images, "startup_attempts": 0,
                    "backup": str(root / "backup.tar.gz"),
                    "backup_result": {"sha256": "checksum"},
                }
                expected = StartupObserved if phase == "startup_requested" else release_install.EngineeringBusy
                with (
                    patch.object(release_install, "module", return_value=server),
                    patch.object(release_install, "engineering_idle", return_value=False),
                    self.assertRaises(expected),
                ):
                    release_install.install(
                        active_release=active, candidate=candidate, state=root,
                        project="fixture", revision="a" * 40,
                        journal={"id": "fixture", "installation": progress},
                        journal_path=root / "journal.json", authorize=lambda: None, tree=[],
                    )
                self.assertEqual(starts, [services])

    def test_preparation_failure_recovers_only_original_services(self):
        starts = []
        server = SimpleNamespace(
            IMAGE_KEYS=["IMAGE"], read_env=lambda *_: {"IMAGE": "original"},
            command=lambda *_: ["compose"],
            run=lambda command, **_: starts.append(command),
        )
        installer = SimpleNamespace(install=lambda *_: None)
        progress = {
            "phase": "backed_up", "old_images": {"IMAGE": "original"},
            "previous_services": ["api", "web", "db"],
        }
        with patch.object(release_install, "module", side_effect=[server, installer]):
            self.assertTrue(release_install.recover_before_migration(Path("release"), Path("state"), "fixture", progress))
        self.assertEqual(starts, [["compose", "--profile", "*", "up", "-d", "--no-deps", "--wait", "--wait-timeout", "600", "api", "web", "db"]])

    def test_invalid_service_recovery_state_never_starts_all_profiles(self):
        starts = []
        server = SimpleNamespace(run=lambda *args, **_: starts.append(args))
        for services in (None, [], "db", ["api"], ["db", "--profile"], ["db", "web api"]):
            with self.subTest(services=services), self.assertRaises(ValueError):
                release_install.resume_services(server, ["compose"], services)
        self.assertEqual(starts, [])

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
