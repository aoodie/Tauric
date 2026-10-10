"""Deployment controls that can be checked without Docker or application data."""

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import github_source
import release_bundle
import release_smoke
import server


class ServerControls(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.images = self.root / "images.env"
        self.values = {
            key: f"ghcr.io/owner/tauric-{key.lower()}@sha256:{'a' * 64}"
            for key in server.IMAGE_KEYS
        }
        self.images.write_text(
            "".join(f"{key}={value}\n" for key, value in self.values.items())
        )

    def tearDown(self):
        self.folder.cleanup()

    def test_operation_errors_keep_private_diagnostics_out_of_the_exception_message(
        self,
    ):
        failed = subprocess.CompletedProcess(
            [], 1, stdout=b"private-output", stderr=b"private-password"
        )
        with (
            patch("server.subprocess.run", return_value=failed),
            self.assertRaises(server.ServerOperationError) as error,
        ):
            server.run(["docker", "compose", "up"])
        self.assertNotIn("private-password", str(error.exception))
        self.assertIn("private-password", error.exception.diagnostics)

    def test_failure_diagnostics_remove_nested_tokens_and_encoded_passwords(self):
        private = self.root / "secrets"
        private.mkdir()
        (private / "database-app").write_text("db/password?\n")
        (private / "tokens").write_text(json.dumps({"tokens": ["gateway-secret"]}))
        (private / "empty").write_text("\n")
        text = "db/password? db%2Fpassword%3F gateway-secret owner-password service unhealthy"
        redacted = release_smoke.redact_diagnostics(text, private, "owner-password")
        for secret in (
            "db/password?",
            "db%2Fpassword%3F",
            "gateway-secret",
            "owner-password",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("service unhealthy", redacted)

    def test_setup_is_private_and_cannot_overwrite_existing_keys(self):
        state = self.root / "state"
        server.setup(state, self.images)
        before = {
            path.name: path.read_bytes() for path in (state / "secrets").iterdir()
        }
        with self.assertRaises(ValueError):
            server.setup(state, self.images)
        self.assertEqual(
            before,
            {path.name: path.read_bytes() for path in (state / "secrets").iterdir()},
        )
        for path in (state, state / "server.env", state / "secrets"):
            self.assertEqual(path.stat().st_mode & 0o077, 0)
        for path in (state / "secrets").iterdir():
            self.assertEqual(path.stat().st_mode & 0o022, 0)
        values = server.read_env(state / "server.env")
        self.assertNotIn("POSTGRES_PASSWORD", values)
        self.assertEqual(
            json.loads(values["TAURIC_ALLOWED_ORIGINS"]), ["http://localhost:8080"]
        )
        self.assertEqual(set(before), set(server.PRIVATE_FILES))

    def test_public_origin_and_cookie_settings(self):
        state = self.root / "state"
        server.setup(state, self.images, "trade.example.com")
        values = server.read_env(state / "server.env")
        self.assertEqual(values["TAURIC_SECURE_COOKIES"], "true")
        self.assertEqual(
            json.loads(values["TAURIC_ALLOWED_ORIGINS"]), ["https://trade.example.com"]
        )
        self.assertIn(
            "compose.https.yml", " ".join(server.command(state, "tauric_test"))
        )

    def test_unpinned_images_and_injected_hostnames_are_rejected_before_writes(self):
        for domain in (
            "https://trade.example.com",
            "example.com/path",
            "localhost",
            "example.com\nSECRET=value",
            "127.0.0.1",
            "10.1.2.3",
            "192.168.1.4",
            "999.999.999.999",
            "217.160.9.196:8081",
            "::1",
        ):
            with self.assertRaises(ValueError):
                server.setup(self.root / "state", self.images, domain)
            self.assertFalse((self.root / "state").exists())
        for value in (
            "image:latest",
            "ghcr.io/owner/image:version",
            "sha256:" + "a" * 64,
        ):
            with self.assertRaises(ValueError):
                server.validate_images(dict.fromkeys(server.IMAGE_KEYS, value))
        server.validate_images(
            dict.fromkeys(server.IMAGE_KEYS, "sha256:" + "a" * 64), local=True
        )

    def test_public_ip_uses_trusted_https_origin_and_dedicated_override(self):
        state = self.root / "state"
        server.setup(state, self.images, "217.160.9.196")
        values = server.read_env(state / "server.env")
        self.assertEqual(values["TAURIC_SECURE_COOKIES"], "true")
        self.assertEqual(
            json.loads(values["TAURIC_ALLOWED_ORIGINS"]), ["https://217.160.9.196"]
        )
        command = server.command(state, "tauric_test")
        self.assertIn(str(server.ROOT / "deploy/compose.public-ip.yml"), command)
        self.assertNotIn(str(server.ROOT / "deploy/compose.https.yml"), command)

    def test_config_rejects_public_internal_ports(self):
        state = self.root / "state"
        server.setup(state, self.images)
        config = {"services": {"api": {"ports": [{"published": "8000"}]}}}
        with (
            patch.object(
                server,
                "run",
                return_value=type(
                    "Result", (), {"stdout": json.dumps(config).encode()}
                )(),
            ),
            self.assertRaisesRegex(ValueError, "Only the web"),
        ):
            server.validate(state, "tauric_test")

    def test_archive_rejects_traversal_and_links(self):
        for name, kind in (
            ("../escaped", tarfile.REGTYPE),
            ("secret-link", tarfile.SYMTYPE),
            ("/absolute", tarfile.REGTYPE),
        ):
            archive = self.root / "backup.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                entry = tarfile.TarInfo(name)
                entry.type = kind
                entry.linkname = "../outside"
                tar.addfile(entry, io.BytesIO(b""))
            with self.assertRaises(ValueError):
                server.extract_backup(archive, self.root / "extract")

    def test_restore_never_touches_an_existing_state_directory(self):
        state = self.root / "state"
        state.mkdir()
        with patch.object(server, "run") as run:
            with self.assertRaises(ValueError):
                server.restore_check(state, "tauric_test", self.root / "missing")
            run.assert_not_called()

    def test_release_bundle_has_only_allowlisted_files_and_source_bound_images(self):
        images = self.root / "digests"
        images.mkdir()
        revision = "a" * 40
        for kind, key in zip(
            ("api", "worker", "web", "openclaw"), server.IMAGE_KEYS, strict=True
        ):
            (images / f"{kind}.json").write_text(
                json.dumps(
                    {
                        "image": self.values[key],
                        "revision": revision,
                        "repository": "owner/tauric",
                    }
                )
            )
        (images / "engineering.json").write_text(
            json.dumps(
                {
                    "image": "ghcr.io/owner/tauric-engineering@sha256:" + "c" * 64,
                    "revision": revision,
                    "repository": "owner/tauric",
                }
            )
        )
        archive = release_bundle.build(
            images, self.root / "release", "owner/tauric", revision
        )
        with tarfile.open(archive) as tar:
            names = {
                str(Path(member.name).relative_to("tauric-release"))
                for member in tar
                if member.isfile()
            }
        self.assertEqual(
            names,
            set(release_bundle.FILES)
            | {"images.env", "release-manifest.json", "engineering-image.json"},
        )
        self.assertFalse(
            any(".env." in name or "database.dump" in name for name in names)
        )
        entry = json.loads((images / "api.json").read_text())
        entry["revision"] = "b" * 40
        (images / "api.json").write_text(json.dumps(entry))
        with self.assertRaises(ValueError):
            release_bundle.build(
                images, self.root / "wrong-release", "owner/tauric", revision
            )

    def test_github_upload_excludes_private_state_and_blocks_embedded_credentials(self):
        root = self.root / "upload"
        root.mkdir()
        for name in github_source.ROOT_FILES:
            (root / name).write_text("safe source\n")
        for name in github_source.DIRECTORIES:
            (root / name).mkdir()
        (root / ".env").write_text("POSTGRES_PASSWORD=private-fixture-secret-012345\n")
        (root / "backend" / ".env.hidden").write_text("private fixture")
        (root / "backend" / "node_modules").mkdir()
        (root / "backend" / "node_modules" / "ignored.js").write_text("private fixture")
        source = root / "backend" / "app.py"
        source.write_text("print('safe')\n")
        with patch.object(github_source, "ROOT", root):
            result = github_source.bundle(self.root / "source.tar.gz")
            self.assertTrue(result["known_private_credentials_checked"])
            with tarfile.open(self.root / "source.tar.gz") as tar:
                names = {m.name for m in tar}
            self.assertNotIn("tauric/.env", names)
            self.assertNotIn("tauric/backend/.env.hidden", names)
            self.assertNotIn("tauric/backend/node_modules/ignored.js", names)
            source.write_text("private-fixture-secret-012345")
            with self.assertRaisesRegex(ValueError, "Private credential detected"):
                github_source.bundle(self.root / "unsafe.tar.gz")
            self.assertFalse((self.root / "unsafe.tar.gz").exists())


if __name__ == "__main__":
    os.umask(0o077)
    unittest.main()
