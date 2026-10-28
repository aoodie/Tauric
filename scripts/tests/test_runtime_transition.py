"""A dependency exception needs exact installed inventories, source and root custody."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import quant_release
import research_runtime_transition as transition


class RuntimeTransition(unittest.TestCase):
    def test_only_the_reviewed_certificate_patch_is_permitted(self):
        before = {'certifi': '2026.7.22', 'sqlalchemy': '2.0.50'}
        self.assertTrue(transition.only_certificate_change(before, {**before, 'certifi': transition.VERSION}))
        for after in ({**before, 'certifi': transition.VERSION, 'sqlalchemy': '2.0.51'}, {'certifi': transition.VERSION}, before):
            self.assertFalse(transition.only_certificate_change(before, after))
        old = b'certifi==2026.7.22\nother==1\n'
        self.assertTrue(transition.reviewed_lock(old, old.replace(b'2026.7.22', b'2026.10.18')))
        self.assertFalse(transition.reviewed_lock(old, b'certifi==2026.10.18\nother==2\n'))

    def test_pip_is_bootstrapped_in_a_private_venv(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            log = work / 'install.log'
            with log.open('w') as output, patch.object(transition.subprocess, 'run') as run:
                python = transition.bootstrap_pip(Path('/runtime/bin/python'), work, output)
            self.assertEqual(python, work / 'installer/bin/python')
            self.assertEqual(run.call_args_list, [
                call(['/runtime/bin/python', '-I', '-m', 'venv', '--clear', str(work / 'installer')],
                     check=True, stdout=output, stderr=output, timeout=180),
                call([str(python), '-I', '-m', 'pip', '--isolated', '--version'],
                     check=True, stdout=output, stderr=output, timeout=30),
            ])

    def test_certificate_wheel_is_staged_and_replaced_with_a_recovery_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            site = root / 'runtime/lib/python/site-packages'
            old_package = site / 'certifi'
            old_metadata = site / 'certifi-2026.7.22.dist-info'
            old_package.mkdir(parents=True)
            old_metadata.mkdir()
            (old_package / '__init__.py').write_text('old')
            (old_metadata / 'METADATA').write_text('Version: 2026.7.22\n')
            before = {'certifi': '2026.7.22', 'sqlalchemy': '2.0.50'}
            after = {**before, 'certifi': transition.VERSION}
            log = root / 'install.log'

            def install_target(command, **_):
                target = Path(command[command.index('--target') + 1])
                target.mkdir()
                (target / 'certifi').mkdir()
                (target / f'certifi-{transition.VERSION}.dist-info').mkdir()
                (target / 'certifi' / '__init__.py').write_text('new')
                (target / f'certifi-{transition.VERSION}.dist-info' / 'METADATA').write_text(
                    f'Version: {transition.VERSION}\n'
                )

            with (
                log.open('w') as output,
                patch.object(transition, 'runtime_site_packages', return_value=site),
                patch.object(transition, 'inventory', side_effect=[before, after]),
                patch.object(transition.subprocess, 'run', side_effect=install_target) as run,
            ):
                actual, backup = transition.install_certificate(
                    root / 'installer/bin/python', root / 'runtime/bin/python', 'app',
                    root, 'f' * 36, before, output,
                )
                transition.clear_certificate_backup(backup)

            self.assertEqual(actual, after)
            self.assertEqual((site / 'certifi' / '__init__.py').read_text(), 'new')
            self.assertTrue((site / f'certifi-{transition.VERSION}.dist-info').is_dir())
            self.assertFalse(old_metadata.exists())
            self.assertFalse(backup.exists())
            self.assertIn('--require-hashes', run.call_args.args[0])

    def test_uncertain_package_replacement_rolls_back_both_original_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            site = root / 'runtime/lib/python/site-packages'
            (site / 'certifi').mkdir(parents=True)
            (site / 'certifi-2026.7.22.dist-info').mkdir()
            (site / 'certifi' / '__init__.py').write_text('old')
            (site / 'certifi-2026.7.22.dist-info' / 'METADATA').write_text('old metadata')
            before = {'certifi': '2026.7.22', 'sqlalchemy': '2.0.50'}

            def install_target(command, **_):
                target = Path(command[command.index('--target') + 1])
                target.mkdir()
                (target / 'certifi').mkdir()
                (target / f'certifi-{transition.VERSION}.dist-info').mkdir()

            with (
                (root / 'install.log').open('w') as output,
                patch.object(transition, 'runtime_site_packages', return_value=site),
                patch.object(transition, 'inventory', side_effect=[
                    before, {**before, 'certifi': transition.VERSION, 'unexpected': '1'}
                ]),
                patch.object(transition.subprocess, 'run', side_effect=install_target),
                self.assertRaisesRegex(ValueError, 'other than the reviewed certificate'),
            ):
                transition.install_certificate(
                    root / 'installer/bin/python', root / 'runtime/bin/python', 'app',
                    root, 'f' * 36, before, output,
                )

            self.assertEqual((site / 'certifi' / '__init__.py').read_text(), 'old')
            self.assertEqual(
                (site / 'certifi-2026.7.22.dist-info' / 'METADATA').read_text(), 'old metadata'
            )
            self.assertFalse((site / f'certifi-{transition.VERSION}.dist-info').exists())

    def test_interrupted_replacement_restores_saved_original_package(self):
        with tempfile.TemporaryDirectory() as folder:
            site = Path(folder) / 'site-packages'
            backup = site.parent / 'backup'
            (site / 'certifi').mkdir(parents=True)
            (site / f'certifi-{transition.VERSION}.dist-info').mkdir()
            (backup / 'certifi').mkdir(parents=True)
            (backup / 'metadata').mkdir()
            (site / 'certifi' / '__init__.py').write_text('new')
            (backup / 'certifi' / '__init__.py').write_text('old')
            (site / f'certifi-{transition.VERSION}.dist-info' / 'METADATA').write_text('new')
            (backup / 'metadata' / 'METADATA').write_text('old')
            transition.recover_certificate_install(site, backup, '2026.7.22')
            self.assertEqual((site / 'certifi' / '__init__.py').read_text(), 'old')
            self.assertEqual(
                (site / 'certifi-2026.7.22.dist-info' / 'METADATA').read_text(), 'old'
            )
            self.assertFalse(backup.exists())

    def test_receipt_cannot_hide_runtime_drift_or_claim_an_uncertain_install(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = {'certifi': '2026.7.22', 'sqlalchemy': '2.0.50'}
            after = {**before, 'certifi': transition.VERSION}
            record = {'phase': 'installed_verified', 'revision': 'b' * 40, 'base_revision': 'a' * 40,
                      'original': {'source_revision': 'a' * 40}, 'locks': {n: 'blob' for n in transition.LOCKS},
                      'wheel_sha256': transition.WHEEL_SHA256, 'units': [],
                      'before': {k: before for k in ('app', 'qlib')}, 'after': {k: after for k in ('app', 'qlib')}}
            real_stat = Path.stat

            def root_stat(path, **kwargs):
                value = list(real_stat(path, **kwargs))
                value[4] = 0
                return os.stat_result(value)

            with patch.object(transition, 'ROOT', root), patch.object(transition, 'inventory', return_value=after), patch.object(Path, 'stat', root_stat):
                receipt = transition.receipt_path('b' * 40)
                receipt.parent.mkdir(parents=True)
                receipt.write_text(json.dumps(record))
                receipt.chmod(0o600)
                args = ('b' * 40, 'a' * 40, record['locks'], record['original'])
                self.assertEqual(transition.validate(*args)['units'], [])
                with patch.object(transition, 'inventory', return_value={**after, 'unexpected-package': '1'}), self.assertRaisesRegex(ValueError, 'runtime differs'):
                    transition.validate(*args)
                record['phase'] = 'prepared'
                receipt.write_text(json.dumps(record))
                with self.assertRaisesRegex(ValueError, 'not bound'):
                    transition.validate(*args)
                record['phase'] = 'installed_verified'
                receipt.write_text(json.dumps(record))
                receipt.chmod(0o644)
                with self.assertRaisesRegex(ValueError, 'root-owned'):
                    transition.validate(*args)

    def test_old_source_is_not_resumed_after_dependencies_changed(self):
        with patch.object(quant_release, 'resume_units') as resume:
            quant_release.resume_original({'units': list(transition.UNITS), 'dependency_transition': 'b' * 40})
        resume.assert_not_called()
