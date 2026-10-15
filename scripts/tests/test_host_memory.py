"""Host recovery must preserve existing files and fail closed on unsafe state."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import host_memory


class HostMemory(unittest.TestCase):
    def test_kernel_swap_records_report_bytes_and_empty_configuration(self):
        header = "Filename Type Size Used Priority\n"
        self.assertEqual(host_memory.parse_swaps(header), [])
        self.assertEqual(
            host_memory.parse_swaps(header + "/private/swapfile file 2097148 16 10\n"),
            [{"name": "/private/swapfile", "size": 2097148 * 1024, "used": 16384}],
        )

    def test_malformed_kernel_swap_record_is_rejected(self):
        with self.assertRaises(ValueError):
            host_memory.parse_swaps(
                "Filename Type Size Used Priority\nunknown record\n"
            )

    def test_existing_unit_is_preserved_and_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.swap"
            host_memory.ensure_unit(path, "expected\n")
            before = path.stat().st_ino
            host_memory.ensure_unit(path, "expected\n")
            self.assertEqual(path.stat().st_ino, before)
            with self.assertRaises(ValueError):
                host_memory.ensure_unit(path, "different\n")
            self.assertEqual(path.read_text(), "expected\n")

    def test_symlink_never_writes_an_unrelated_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "unrelated"
            path = Path(directory) / "memory.swap"
            path.symlink_to(target)
            with self.assertRaises(ValueError):
                host_memory.ensure_unit(path, "must not write")
            self.assertFalse(target.exists())

    def test_existing_file_requires_capacity_permissions_and_signature(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "swapfile"
            path.write_bytes(b"x" * 4096)
            path.chmod(0o600)
            with patch.object(host_memory, "command", return_value="swap"):
                host_memory.validate_file(path, 4096, os.getuid())
                path.chmod(0o644)
                with self.assertRaises(ValueError):
                    host_memory.validate_file(path, 4096, os.getuid())
                path.chmod(0o600)
                with self.assertRaises(ValueError):
                    host_memory.validate_file(path, 8192, os.getuid())
            with (
                patch.object(host_memory, "command", return_value="ext4"),
                self.assertRaises(ValueError),
            ):
                host_memory.validate_file(path, 4096, os.getuid())
            self.assertEqual(path.read_bytes(), b"x" * 4096)

    def test_existing_foreign_swap_is_not_replaced(self):
        with (
            patch.object(host_memory.os, "geteuid", return_value=0),
            patch.object(host_memory, "swaps", return_value=[{"name": "/existing"}]),
            patch.object(host_memory, "command") as command,
        ):
            with self.assertRaises(ValueError):
                host_memory.apply()
            command.assert_not_called()

    def test_already_active_buffer_is_checked_without_format_or_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root, units = Path(directory) / "private", Path(directory) / "units"
            root.mkdir(mode=0o700)
            units.mkdir()
            path = root / "swapfile"
            path.write_text("existing signed swap")
            path.chmod(0o600)
            active = {"name": str(path), "size": host_memory.SIZE - 4096, "used": 0}
            calls = []

            def command(args):
                calls.append(args)
                if args[0] == "findmnt":
                    return "ext4"
                if args[0] == "systemd-escape":
                    return "memory.swap"
                return ""

            with (
                patch.object(host_memory.os, "geteuid", return_value=0),
                patch.object(host_memory, "swaps", return_value=[active]),
                patch.object(host_memory, "validate_file") as validate,
                patch.object(host_memory, "command", side_effect=command),
                patch.object(
                    host_memory.shutil,
                    "disk_usage",
                    return_value=SimpleNamespace(free=20 * 1024**3),
                ),
                patch.object(
                    host_memory,
                    "check",
                    return_value={
                        "active": active,
                        "is-active": True,
                        "is-enabled": True,
                    },
                ),
                patch.object(
                    host_memory.Path,
                    "lstat",
                    return_value=type("Stat", (), {"st_uid": 0, "st_mode": 0o40700})(),
                ),
                patch.object(
                    host_memory.os, "posix_fallocate", create=True
                ) as allocate,
            ):
                host_memory.apply(root, units)
                validate.assert_called_once_with(path)
                allocate.assert_not_called()
            self.assertFalse(any(args[0] == "mkswap" for args in calls))
            self.assertEqual(path.read_text(), "existing signed swap")

    def test_low_disk_reserve_rejects_before_creation(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(host_memory.os, "geteuid", return_value=0),
            patch.object(host_memory, "swaps", return_value=[]),
            patch.object(host_memory, "command", return_value="ext4"),
            patch.object(
                host_memory.shutil,
                "disk_usage",
                return_value=SimpleNamespace(free=host_memory.DISK_RESERVE),
            ),
        ):
            root = Path(directory) / "not-created"
            with self.assertRaises(ValueError):
                host_memory.apply(root)
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
