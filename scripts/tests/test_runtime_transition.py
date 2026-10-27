"""A dependency exception needs exact installed inventories, source and root custody."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
