"""Require real Qlib training, LEAN handoff and financing checks in isolated CI."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from run_browser_checks import verify_report

ROOT = Path(__file__).resolve().parents[1]


def main():
    with TemporaryDirectory(prefix="tauric-quant-checks-") as directory:
        report = Path(directory) / "results.xml"
        result = subprocess.run(
            [
                sys.executable, str(ROOT / "scripts/local.py"), "test",
                "tests/test_quant_runtimes.py", "-q", f"--junitxml={report}",
            ],
            cwd=ROOT,
            env={**os.environ, "TAURIC_TEST_QUANT_RUNTIMES": "1"},
            check=False,
        )
        if result.returncode:
            return result.returncode
        verify_report(report, expected_count=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
