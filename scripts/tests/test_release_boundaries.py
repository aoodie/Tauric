"""Independent routing, encrypted recovery and research-only release boundaries."""

import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.exceptions import InvalidTag

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_vault
import maintenance_backup
import operations_watch
import research_release
import shared_ingress


class Routing(unittest.TestCase):
    def test_external_routes_are_hash_bound_and_cannot_escape_private_state(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            root = state / "secrets/shared-ingress"
            root.mkdir(parents=True, mode=0o700)
            files = {"Caddyfile": "tauric and neighbour routes", "compose.yml": "host network"}
            for name, value in files.items():
                (root / name).write_text(value)
            (root / "manifest.json").write_text(json.dumps({"format": 1, "files": {n: hashlib.sha256(v.encode()).hexdigest() for n, v in files.items()}}))
            values = {"TAURIC_SHARED_INGRESS": "true", "TAURIC_DOMAIN": "example.test", "TAURIC_SECRETS_DIR": str(state / "secrets")}
            command = shared_ingress.command(["compose"], state, values)
            self.assertEqual(command, ["compose", "-f", str(root / "compose.yml")])
            self.assertEqual(shared_ingress.command(command, state, values), command)
            (root / "Caddyfile").write_text("lost neighbour route")
            with self.assertRaisesRegex(ValueError, "changed"):
                shared_ingress.command(["compose"], state, values)
            with self.assertRaises(ValueError):
                shared_ingress.override(state, {**values, "TAURIC_SECRETS_DIR": "/another/state"})
            (root / "Caddyfile").unlink()
            (root / "Caddyfile").symlink_to(root / "compose.yml")
            with self.assertRaises(ValueError):
                shared_ingress.override(state, values)


class ResearchOnly(unittest.TestCase):
    def setUp(self):
        self.proof = {"kind": "trusted_research_only", "revision": "a" * 40, "before_signature": {"source": "old"}, "after_signature": {"source": "new"}, "release_run_id": 123, "checked_release_verified": True}
        self.snapshot = {"tables": {n: {"count": 0, "sha256": "empty"} for n in research_release.EMPTY_TABLES}}

    def verify(self, **changes):
        args = {"revision": "a" * 40, "before_signature": {"source": "old"}, "after_signature": {"source": "new"}, "snapshot": self.snapshot, "services": ["db", "api", "web"]}
        args.update(changes)
        return research_release.verify(self.proof, **args)

    def test_source_transition_is_not_available_for_existing_financial_state(self):
        self.assertEqual(self.verify(), {"source": "new"})
        for name in research_release.EMPTY_TABLES:
            self.snapshot["tables"][name]["count"] = 1
            with self.subTest(table=name), self.assertRaises(ValueError):
                self.verify()
            self.snapshot["tables"][name]["count"] = 0
        self.snapshot["tables"].pop("broker_routes")
        with self.assertRaises(ValueError):
            self.verify()

    def test_financial_workers_unchecked_releases_and_changed_identities_are_refused(self):
        for name in research_release.FINANCIAL_SERVICES:
            with self.subTest(service=name), self.assertRaises(ValueError):
                self.verify(services=["db", name])
        for changes in ({"revision": "b" * 40}, {"before_signature": {}}, {"after_signature": {}}, {"services": ["api"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.verify(**changes)
        self.proof["checked_release_verified"] = False
        with self.assertRaises(ValueError):
            self.verify()


class EncryptedRecovery(unittest.TestCase):
    def test_large_backup_authentication_roundtrip_and_no_plaintext_on_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, archive, key = root / "database.dump", root / "database.vault", root / "key"
            data = os.urandom(backup_vault.BLOCK * 2 + 43)
            source.write_bytes(data)
            key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
            key.chmod(0o600)
            result = backup_vault.seal(source, archive, key)
            self.assertEqual(result["plaintext_sha256"], hashlib.sha256(data).hexdigest())
            self.assertTrue(source.exists())
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)
            restored = root / "restored"
            backup_vault.restore(archive, restored, key)
            self.assertEqual(restored.read_bytes(), data)
            with self.assertRaises(ValueError):
                backup_vault.seal(source, archive, key)
            with archive.open("r+b") as output:
                output.seek(len(backup_vault.HEADER) + 100)
                output.write(b"tamper")
            rejected = root / "rejected"
            with self.assertRaises(InvalidTag):
                backup_vault.restore(archive, rejected, key)
            self.assertFalse(rejected.exists())

    def test_permissive_or_wrong_key_and_interrupted_archive_never_replace_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, archive, key = root / "source", root / "archive", root / "key"
            source.write_bytes(b"important original")
            key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
            key.chmod(0o644)
            with self.assertRaises(ValueError):
                backup_vault.seal(source, archive, key)
            key.chmod(0o600)
            archive.with_name("archive.pending").write_bytes(b"uncertain old result")
            with self.assertRaises(FileExistsError):
                backup_vault.seal(source, archive, key)
            self.assertEqual(source.read_bytes(), b"important original")
            with self.assertRaises(ValueError):
                backup_vault.recover(source, os.urandom(32), io.BytesIO())


class HealthAlerts(unittest.TestCase):
    def test_disabled_financial_profiles_are_not_reported_as_failed_required_services(self):
        service = {"running": True, "health": "healthy", "oom_killed": False, "restart_count": 0, "container_id": "same", "memory_limit_bytes": 100}
        sample = {"services": {"api": service, "broker_execution": {**service, "running": False}}, "host_memory": {"physical_total_bytes": 16 * 1024**3, "physical_available_bytes": 6 * 1024**3}, "available_disk_bytes": 20 * 1024**3, "backup_age_seconds": 10, "offhost_backup_age_seconds": 20}
        self.assertEqual(operations_watch.alerts(sample, {"api"}), [])
        sample["services"]["broker_execution"]["running"] = True
        self.assertEqual(operations_watch.alerts(sample, {"api"})[0]["key"], "financial_service:broker_execution")
        sample["services"]["api"]["health"] = "unhealthy"
        self.assertIn("service:api", {a["key"] for a in operations_watch.alerts(sample, {"api"})})
        self.assertIn('tauric_service_running{service="api"} 1', operations_watch.text_metrics(sample, []))


class ResearchRecovery(unittest.TestCase):
    def test_actual_artifact_archive_restores_bytes_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            quant = root / "quant"
            (quant / "artifacts/job").mkdir(parents=True)
            (quant / "control").mkdir()
            (quant / "artifacts/job/result.json").write_text('{"observed":"real bytes"}')
            (quant / "control/config.json").write_text('{"source":"pinned"}')
            archive = root / "research.tar.gz"
            self.assertEqual(maintenance_backup.research_archive(archive, quant)["files"], 2)
            restored = maintenance_backup.restore_research(archive, root)
            self.assertEqual(restored["files_restored"], 2)
            self.assertFalse(restored["background_services_started"])
            (quant / "control/link").symlink_to(root / "outside")
            with self.assertRaises(ValueError):
                maintenance_backup.research_archive(root / "bad.tar.gz", quant)

    def test_retention_protects_unverified_offhost_copies_and_manual_backups(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for day in ("2026-10-24", "2026-10-25", "2026-10-26"):
                (root / ("scheduled-" + day + ".json")).write_text(json.dumps({"day": day, "phase": "verified", "offhost_copy_verified": day != "2026-10-25"}))
                for suffix in (".tar.gz.vault", "-research.tar.gz.vault"):
                    (root / ("scheduled-" + day + suffix)).write_text("encrypted archive")
            manual = root / "manual-recovery.vault"
            manual.write_text("preserve")
            self.assertEqual(maintenance_backup.retain_verified(root, keep=1), ["2026-10-24"])
            self.assertTrue((root / "scheduled-2026-10-25.tar.gz.vault").exists())
            self.assertTrue(manual.exists())
