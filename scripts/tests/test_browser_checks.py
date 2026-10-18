"""A skipped browser workflow must never satisfy the release checks."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_browser_checks import verify_report


class BrowserReport(unittest.TestCase):
    def check(self, cases):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "results.xml"
            report.write_text(f"<testsuites><testsuite>{cases}</testsuite></testsuites>")
            verify_report(report)

    def test_every_workflow_must_pass(self):
        self.check("<testcase/>" * 4)

    def test_skipped_failed_or_missing_workflow_rejects_the_report(self):
        for cases in (
            "<testcase/>" * 3,
            "<testcase/>" * 3 + "<testcase><skipped/></testcase>",
            "<testcase/>" * 3 + "<testcase><failure/></testcase>",
            "<testcase/>" * 3 + "<testcase><error/></testcase>",
        ):
            with self.subTest(cases=cases), self.assertRaises(ValueError):
                self.check(cases)
