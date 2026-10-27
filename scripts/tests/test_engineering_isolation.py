"""Host launch policy and signed receipts are independent of submitted code."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "engineering_host", ROOT / "scripts/engineering_host.py"
)
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
entry_spec = importlib.util.spec_from_file_location(
    "engineering_entry", ROOT / "scripts/engineering_entry.py"
)
entry = importlib.util.module_from_spec(entry_spec)
entry_spec.loader.exec_module(entry)


class Isolation(unittest.TestCase):
    def test_short_tail_keeps_upstream_truncation_information(self):
        captured = host.captured_output(b"x\n" * 500)
        self.assertTrue(captured["output_truncated"])
        self.assertLess(len(captured["output"]), 20000)
        self.assertEqual(captured["output_observed_bytes"], 1000)
        self.assertTrue(host.captured_output(b"short\n[Output truncated to the last 30 KB.]\n")["output_truncated"])
        self.assertFalse(host.captured_output(b"complete short output")["output_truncated"])

    def test_arbitrary_script_cannot_choose_host_mounts_image_or_network(self):
        job = {
            "id": str(uuid4()),
            "workspace_id": str(uuid4()),
            "checksum": "a" * 64,
            "script": "cat /run/secrets/database-app",
        }
        args = host.container_args("example/engineer@sha256:" + "b" * 64, job)
        self.assertEqual(args[args.index("--network") + 1], "none")
        self.assertIn("--read-only", args)
        self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
        self.assertEqual(args[args.index("--user") + 1], "10001:10001")
        self.assertNotIn(job["script"], args)
        self.assertNotIn("/var/run/docker.sock", " ".join(args))
        self.assertEqual(
            args[args.index("--mount") + 1],
            "type=bind,source="
            + str(host.WORKSPACE_ROOT / job["workspace_id"] / "mount")
            + ",target=/workspace",
        )
        self.assertNotIn("--privileged", args)
        with self.assertRaises(ValueError):
            host.container_args("example/engineer:latest", job)
        with self.assertRaises(ValueError):
            host.container_args(
                "example/engineer@sha256:" + "b" * 64,
                {**job, "workspace_id": "../../production"},
            )

    def test_test_purpose_does_not_execute_shell_chaining(self):
        argv = entry.test_command("python -m pytest scripts/tests/test_example.py")
        self.assertEqual(argv[1:4], ["-I", "-m", "pytest"])
        self.assertNotIn("/bin/sh", argv)
        with self.assertRaises(ValueError):
            entry.test_command("echo tests-passed")
        for command in (
            "python -m pytest --version",
            "python -m pytest --collect-only",
            "npm test",
            "node --test --help",
        ):
            with self.assertRaises(ValueError):
                entry.test_command(command)

    def test_empty_test_collection_is_not_executed_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "result.xml"
            report.write_text(
                '<testsuites><testsuite tests="0" failures="0" errors="0" skipped="0"/></testsuites>'
            )
            self.assertEqual(
                entry.test_result(["python", "-I", "-m", "pytest"], "", report)[
                    "executed"
                ],
                0,
            )

    def test_receipt_signature_changes_when_output_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            key = folder / "key.pem"
            key.write_bytes(
                Ed25519PrivateKey.generate().private_bytes(
                    Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
                )
            )
            first, second = folder / "first.json", folder / "second.json"
            host.signed_write(first, {"id": "original", "exit_code": 0}, key)
            host.signed_write(second, {"id": "original", "exit_code": 1}, key)
            self.assertNotEqual(
                json.loads(first.read_text())["signature"],
                json.loads(second.read_text())["signature"],
            )


if __name__ == "__main__":
    unittest.main()


class SourceExport(unittest.TestCase):
    def test_source_collection_never_reads_a_symlinked_host_file(self):
        from project_tree import collect

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "backend").mkdir()
            (root / "backend" / "secret.py").symlink_to("/etc/passwd")
            with self.assertRaises(ValueError):
                collect(root, contents=True)

    def test_source_checksums_bind_the_exact_executed_contents(self):
        from project_tree import collect, digest

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "backend").mkdir()
            path = root / "backend" / "example.py"
            path.write_text("def add(a, b): return a + b\n")
            first = collect(root)
            path.write_text("def add(a, b): return a - b\n")
            self.assertNotEqual(digest(first), digest(collect(root)))
