"""An exception cannot hide a new image, platform, version or expired finding."""

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_policy import evaluate


class SecurityPolicy(unittest.TestCase):
    def setUp(self):
        self.report = {'SchemaVersion': 2, 'Results': [{'Target': 'exact/path', 'Vulnerabilities': [
            {'VulnerabilityID': 'CVE-one', 'PkgName': 'library', 'InstalledVersion': '1', 'Severity': 'HIGH'}]}]}
        self.exception = {'kind': 'openclaw', 'platform': 'linux/amd64', 'target': 'exact/path',
                          'id': 'CVE-one', 'package': 'library', 'version': '1', 'severity': 'HIGH',
                          'package_path': '',
                          'expires': '2026-11-03', 'reason': 'Detailed reachability assessment',
                          'evidence': 'docs/specific-assessment.md', 'reviewer': 'engineering security review'}

    def assess(self, exceptions):
        return evaluate(self.report, exceptions, kind='openclaw', platform='linux/amd64', today=date(2026, 10, 27))

    def test_default_blocks_every_high_and_unfixed_finding(self):
        self.assertEqual(len(self.assess([])['blocked']), 1)

    def test_only_exact_current_short_lived_exceptions_apply(self):
        self.assertFalse(self.assess([self.exception])['blocked'])
        for field, value in {'version': '2', 'target': 'other/path', 'platform': 'linux/arm64', 'kind': 'engineering',
                             'expires': '2026-10-26', 'reason': '', 'evidence': '', 'reviewer': '', 'package_path': 'another/package.json'}.items():
            with self.subTest(field=field):
                self.assertTrue(self.assess([{**self.exception, field: value}])['blocked'])

    def test_an_available_fix_cannot_be_hidden_by_a_reachability_exception(self):
        self.report['Results'][0]['Vulnerabilities'][0]['FixedVersion'] = '2'
        self.assertTrue(self.assess([self.exception])['blocked'])

    def test_empty_and_unrecognized_reports_fail(self):
        for report in ({}, {'SchemaVersion': 2, 'Results': []}, {'SchemaVersion': 1, 'Results': [{}]}):
            with self.assertRaises(ValueError):
                evaluate(report, [], kind='api', platform='linux/amd64')
