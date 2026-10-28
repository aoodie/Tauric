"""Backport inventory and bootstrap wheel describe code that is actually installed."""

import csv
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import patch_python_tooling as tooling


class PythonTooling(unittest.TestCase):
    def fixture(self, root):
        package = root / 'pip'
        metadata = root / 'pip-26.2.2.dist-info'
        metadata.mkdir()
        (metadata / 'RECORD').write_text('pip-26.2.2.dist-info/RECORD,,\n')
        for name in ('msgpack', 'urllib3', 'pkg_resources'):
            folder = package / '_vendor' / name
            folder.mkdir(parents=True)
            (folder / '__init__.py').write_text('old code')
        (package / '_vendor/vendor.txt').write_text('msgpack==1.1.2\n    urllib3==2.7.0\nsetuptools==70.3.0\n')
        (package / '_vendor/bom.cdx.json').write_text(json.dumps({'version': 1, 'metadata': {},
            'components': [{'name': name, 'version': version, 'bom-ref': 'pkg:pypi/' + name + '@' + version}
                           for name, version in [('msgpack', '1.1.2'), ('urllib3', '2.7.0'), ('setuptools', '70.3.0')]],
            'dependencies': [{'ref': 'bom-ref:pip', 'dependsOn': ['pkg:pypi/setuptools@70.3.0', 'pkg:pypi/msgpack@1.1.2']}]}))
        backend = package / '_internal/metadata'
        backend.mkdir(parents=True)
        (backend / '__init__.py').write_text('if sys.version_info >= (3, 14):\n    return True\n')
        inputs = root / 'inputs'
        inputs.mkdir()
        with tarfile.open(inputs / 'msgpack-1.2.3.tar.gz', 'w:gz') as archive:
            for name in ('__init__.py', 'fallback.py'):
                data = b'new published implementation\n'
                info = tarfile.TarInfo('msgpack-1.2.3/msgpack/' + name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        with zipfile.ZipFile(inputs / 'urllib3-2.8.0-py3-none-any.whl', 'w') as archive:
            archive.writestr('urllib3/__init__.py', 'new published implementation')
        bootstrap = root / 'pip-26.2.2-py3-none-any.whl'
        with zipfile.ZipFile(bootstrap, 'w') as archive:
            archive.writestr('pip-26.2.2.dist-info/METADATA', 'Version: 26.2.2\n')
            archive.writestr('pip/_vendor/pkg_resources/__init__.py', 'old legacy backend')
        hashes = {name: hashlib.sha256((inputs / name).read_bytes()).hexdigest() for name in tooling.INPUTS}
        return package, metadata, bootstrap, inputs, root / 'assembly.json', hashes

    def test_bootstrap_wheel_contains_only_the_new_actual_package_tree(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.fixture(Path(folder))
            with patch.object(tooling, 'INPUTS', args[-1]):
                result = tooling.patch(*args[:-1])
            self.assertFalse((args[0] / '_vendor/pkg_resources').exists())
            self.assertIn('urllib3==2.8.0', (args[0] / '_vendor/vendor.txt').read_text())
            self.assertEqual(json.loads(args[4].read_text()), result)
            with zipfile.ZipFile(args[2]) as archive:
                self.assertFalse(any('pkg_resources' in x for x in archive.namelist()))
                bom = archive.read('pip/_vendor/bom.cdx.json').decode()
                self.assertNotIn('setuptools', bom)
                self.assertIn('pkg:pypi/msgpack@1.2.3', bom)
                records = list(csv.reader(archive.read('pip-26.2.2.dist-info/RECORD').decode().splitlines()))
                self.assertEqual({r[0] for r in records}, set(archive.namelist()))
                for name, checksum, size in records:
                    if checksum:
                        self.assertEqual(int(size), len(archive.read(name)))

    def test_mismatched_inputs_fail_before_removing_a_tool(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.fixture(Path(folder))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                tooling.patch(*args[:-1])
            self.assertTrue((args[0] / '_vendor/pkg_resources').is_dir())
