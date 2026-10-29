"""Verified source mounts are readable without exposing private deployment files."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from release_bridge import public_source_permissions


class PublicSourcePermissions(unittest.TestCase):
    def fixture(self, root):
        release = root / 'release'
        source = release / 'backend/tauric/chat_instructions.txt'
        source.parent.mkdir(parents=True, mode=0o700)
        source.write_text('Verified project instructions')
        source.chmod(0o600)
        private = root / 'private-key'
        private.write_text('private fixture')
        private.chmod(0o600)
        return release, source, private

    def test_public_source_is_readable_but_never_writable_by_another_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            release, source, private = self.fixture(Path(folder))
            public_source_permissions(release, ['backend/tauric/chat_instructions.txt'])
            self.assertEqual(source.stat().st_mode & 0o777, 0o644)
            for path in [release, source.parent, source.parent.parent]:
                self.assertEqual(path.stat().st_mode & 0o777, 0o755)
            self.assertEqual(private.stat().st_mode & 0o777, 0o600)

    def test_unknown_files_and_links_fail_before_exposing_any_source(self):
        for linked in (False, True):
            with self.subTest(linked=linked), tempfile.TemporaryDirectory() as folder:
                release, source, private = self.fixture(Path(folder))
                if linked:
                    (release / 'unknown').symlink_to(private)
                else:
                    (release / 'unknown').write_text('unexpected credential fixture')
                with self.assertRaisesRegex(ValueError, 'Unexpected entries'):
                    public_source_permissions(release, ['backend/tauric/chat_instructions.txt'])
                self.assertEqual(source.stat().st_mode & 0o777, 0o600)
                self.assertEqual(private.stat().st_mode & 0o777, 0o600)
