"""Scanner access must match private runner files while retaining all isolation."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import scan_release_images as scanner


class ScannerIdentity(unittest.TestCase):
    def test_private_scratch_and_credentials_use_the_runner_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            images, output = root / 'images', root / 'output'
            images.mkdir()
            (images / 'api.json').write_text(json.dumps({'revision': 'a' * 40, 'repository': 'aoodie/tauric',
                'image': 'ghcr.io/aoodie/tauric-api@sha256:' + 'b' * 64}))

            def execute(command, **kwargs):
                self.assertEqual(command[command.index('--user') + 1], '1001:1002')
                self.assertIn('--read-only', command)
                self.assertEqual(command[command.index('--cap-drop') + 1], 'ALL')
                self.assertIn('DOCKER_CONFIG=/registry', command)
                self.assertIn(str(output.resolve()) + ':/reports', command)
                scratch = Path(next(x[:-5] for x in command if x.endswith(':/tmp')))
                self.assertEqual(scratch.stat().st_mode & 0o777, 0o700)
                self.assertNotIn('/var/run/docker.sock', ' '.join(command))
                report = output / 'api-amd64.json'
                report.write_text(json.dumps({'SchemaVersion': 2, 'Metadata': {'ImageConfig': {'architecture': 'amd64'}},
                                              'Results': [{'Target': 'Python'}]}))
                return SimpleNamespace(returncode=0)

            with (patch.object(scanner, 'KINDS', ('api',)), patch.object(scanner, 'PLATFORMS', ('linux/amd64',)),
                  patch.object(scanner.os, 'getuid', return_value=1001), patch.object(scanner.os, 'getgid', return_value=1002),
                  patch.object(scanner.subprocess, 'run', side_effect=execute)):
                self.assertFalse(scanner.scan(images, output, 'a' * 40, 'aoodie/Tauric'))
            self.assertTrue(json.loads((output / 'summary.json').read_text())['passed'])
