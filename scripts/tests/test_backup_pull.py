"""An off-host receipt requires both unchanged ciphertext and authenticated recovery."""

import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backup_vault
import pull_recovery_backups as pull


class BackupPull(unittest.TestCase):
    def test_complete_pair_is_authenticated_before_remote_acknowledgment(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            remote = root / "remote"
            remote.mkdir()
            key = root / "remote-key"
            key.write_bytes(base64.urlsafe_b64encode(os.urandom(32)))
            key.chmod(0o600)
            items = []
            for suffix in (".tar.gz.vault", "-research.tar.gz.vault"):
                source = root / ("source" + suffix)
                source.write_bytes(b"private recovery contents" * 20)
                destination = remote / ("scheduled-2026-10-27" + suffix)
                result = backup_vault.seal(source, destination, key)
                items.append({"name": destination.name, "bytes": destination.stat().st_size,
                              "sha256": pull.checksum(destination),
                              "recovery": {k: result[k] for k in ("plaintext_bytes", "plaintext_sha256")}})
            row = {"day": "2026-10-27", "files": items}
            record = remote / "scheduled-2026-10-27.json"
            record.write_text('{"phase":"verified"}')
            config = {"destination": str(root / "offhost"), "key_file": str(root / "keys/key")}

            def ssh(config, code, output=None):
                if code == pull.INVENTORY:
                    return json.dumps([row]).encode()
                if "secrets/backup-key" in code:
                    output.write(key.read_bytes())
                elif "shutil.copyfileobj" in code:
                    name = next(item["name"] for item in items if item["name"] in code)
                    output.write((remote / name).read_bytes())
                else:
                    # Run the actual receipt update against the disposable remote directory.
                    exec(compile(code.replace("/srv/private-backups/scheduled", str(remote)), "receipt", "exec"), {})  # noqa: S102 - trusted generated script, disposable fixture paths

            with patch.object(pull, "ssh", side_effect=ssh):
                result = pull.pull(config)
                self.assertEqual(len(result["verified_checkpoints"]), 1)
                self.assertTrue(json.loads(record.read_text())["offhost_copy_verified"])
                self.assertEqual(len(list((root / "offhost").glob("*.vault"))), 2)
                # Existing ciphertext is never silently replaced on mismatch.
                damaged = root / "offhost" / items[0]["name"]
                damaged.write_bytes(b"damaged ciphertext")
                with self.assertRaises(ValueError):
                    pull.pull(config)
                self.assertEqual(damaged.read_bytes(), b"damaged ciphertext")
            self.assertEqual((root / "offhost").stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / "keys/key").stat().st_mode & 0o777, 0o600)
            self.assertFalse(any("private recovery contents" in p.read_text(errors="ignore")
                                 for p in (root / "offhost").iterdir()))

    def test_incomplete_or_escaping_inventory_never_starts_transfer(self):
        for row in ({"day": "../../secrets", "files": []}, {"day": "2026-10-27", "files": []}):
            with self.assertRaises(ValueError):
                pull.validate_inventory(row)

    def test_unpinned_option_like_host_is_rejected(self):
        with self.assertRaises(ValueError):
            pull.ssh({"host": "-o ProxyCommand=bad", "identity": "unused"}, "pass")
