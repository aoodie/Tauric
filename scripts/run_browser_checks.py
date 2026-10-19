"""Require actual passes for the critical built-UI workflows in hosted CI."""

import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
TESTS = (
    "tests/test_broker_routes_browser.py",
    "tests/test_project_tasks_browser.py",
    "tests/test_quant_browser.py",
    "tests/test_protected_replay_browser.py",
)


def verify_report(path):
    cases = ElementTree.parse(path).getroot().findall(".//testcase")
    if len(cases) != len(TESTS) or any(
        case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")
    ):
        raise ValueError("Every critical browser workflow must execute and pass; skips are failures.")


def main():
    with TemporaryDirectory(prefix="tauric-browser-checks-") as directory:
        report = Path(directory) / "results.xml"
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/local.py"), "test", *TESTS, "-q", f"--junitxml={report}"],
            cwd=ROOT,
            check=False,
        )
        if result.returncode:
            return result.returncode
        verify_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
