"""Privileged research startup and cleanup must stay within their fixed scope."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location(
    "quant_host", Path(__file__).parents[1] / "quant_host.py"
)
host = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(host)


class ResearchHostTests(unittest.TestCase):
    def test_source_mismatch_and_changed_release_fail_before_worker_startup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = "a" * 40
            code = root / "source" / revision / "backend/tauric/quant_worker.py"
            code.parent.mkdir(parents=True)
            code.write_bytes(b"reviewed source")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"revision": revision}))
            config = {
                "source_revision": revision,
                "source_files": {
                    "backend/tauric/quant_worker.py": hashlib.sha256(
                        code.read_bytes()
                    ).hexdigest()
                },
            }
            with (
                patch.object(host, "ROOT", root),
                patch.object(host, "Path", return_value=manifest),
            ):
                self.assertEqual(host.verify_source(config), root / "source" / revision)
                code.write_bytes(b"unreviewed change")
                with self.assertRaisesRegex(ValueError, "integrity"):
                    host.verify_source(config)
                manifest.write_text(json.dumps({"revision": "b" * 40}))
                with self.assertRaisesRegex(ValueError, "Revalidate"):
                    host.verify_source(config)

    def test_source_inventory_cannot_escape_the_extracted_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "control").mkdir()
            config = {
                "source_revision": "a" * 40,
                "lean_image": host.LEAN_IMAGE,
                "source_files": {
                    "backend/tauric/quant_worker.py": "a" * 64,
                    "backend/runners/qlib_runner.py": "b" * 64,
                    "backend/../private": "c" * 64,
                },
            }
            (root / "control/config.json").write_text(json.dumps(config))
            with (
                patch.object(host, "ROOT", root),
                self.assertRaisesRegex(ValueError, "inventory"),
            ):
                host.read_config()

    def database(self):
        return [
            {
                "Config": {
                    "Labels": {
                        "com.docker.compose.project": "tauric_server",
                        "com.docker.compose.service": "db",
                    }
                },
                "State": {"Running": True, "Health": {"Status": "healthy"}},
                "NetworkSettings": {
                    "Networks": {
                        "tauric_server_default": {
                            "IPAddress": "172.22.0.2",
                            "Aliases": ["db"],
                        }
                    }
                },
            }
        ]

    def test_database_discovery_rejects_other_projects_and_public_or_unhealthy_targets(
        self,
    ):
        self.assertEqual(host.database_ip(self.database()), "172.22.0.2")
        for mutate in (
            lambda row: row[0]["Config"]["Labels"].update(
                {"com.docker.compose.project": "unrelated"}
            ),
            lambda row: row[0]["State"]["Health"].update(Status="unhealthy"),
            lambda row: row[0]["NetworkSettings"]["Networks"][
                "tauric_server_default"
            ].update(IPAddress="8.8.8.8"),
        ):
            row = self.database()
            mutate(row)
            with self.assertRaises(ValueError):
                host.database_ip(row)

    def test_stop_cleanup_leaves_other_images_job_mounts_and_names_alone(self):
        identity = "b3db2cc2-2d8e-4fa2-9f6c-931711e9c1f7"
        container = {
            "Name": "/tauric-lab-" + identity,
            "Config": {"Image": host.LEAN_IMAGE},
            "Mounts": [
                {
                    "Type": "bind",
                    "Source": str(host.ROOT / "artifacts/lean" / identity),
                    "Destination": "/job",
                }
            ],
        }
        self.assertTrue(host.owned_lean_container(container))
        container["Mounts"][0]["Source"] = "/srv/other-project/" + identity
        self.assertFalse(host.owned_lean_container(container))
        container["Mounts"][0]["Source"] = str(host.ROOT / "artifacts/lean" / identity)
        container["Config"]["Image"] = "unrelated:latest"
        self.assertFalse(host.owned_lean_container(container))

    def test_units_keep_service_identity_write_scope_and_docker_access_separate(self):
        qlib, lean = host.unit("qlib"), host.unit("lean")
        self.assertNotIn("SupplementaryGroups=docker", qlib)
        self.assertIn("SupplementaryGroups=docker", lean)
        self.assertIn("ProtectSystem=strict", qlib)
        self.assertIn("ProtectHome=yes", lean)
        self.assertIn("PartOf=tauric-engineering.service", qlib)
        self.assertIn("WantedBy=multi-user.target tauric-engineering.service", lean)
        self.assertIn(
            "ReadWritePaths=/run/tauric-quant /srv/tauric-quant/artifacts/qlib", qlib
        )
        self.assertNotIn("broker_execution", qlib + lean)
        self.assertNotIn("/secrets/", qlib + lean)
        with self.assertRaises(ValueError):
            host.unit("broker_execution")

    def test_private_environment_preserves_literals_and_rejects_new_line_injection(
        self,
    ):
        self.assertEqual(
            host.environment_text({"VALUE": 'a$word`literal"\\'}),
            'VALUE="a$word`literal\\"\\\\"\n',
        )
        for value in ("secret\nOVERRIDE=yes", "secret\r", "secret\0"):
            with self.assertRaises(ValueError):
                host.environment_text({"VALUE": value})


if __name__ == "__main__":
    unittest.main()
