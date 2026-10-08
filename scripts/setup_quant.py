"""Install isolated optional research engines; app and broker environments are unchanged."""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from tauric.quant_runtime import LEAN_IMAGE

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--python", default="python3.12", help="Python 3.12 executable for Qlib"
)
parser.add_argument("--engine", choices=("all", "qlib", "lean"), default="all")
args = parser.parse_args()
if args.engine in ("all", "qlib"):
    version = subprocess.check_output(
        [
            args.python,
            "-c",
            "import sys; print('.'.join(map(str,sys.version_info[:2])))",
        ],
        text=True,
    ).strip()
    if version != "3.12":
        raise SystemExit("Use Python 3.12 for the validated Qlib environment")
    destination = ROOT / ".qlib-venv"
    subprocess.run([args.python, "-m", "venv", str(destination)], check=True)
    subprocess.run(
        [
            str(destination / "bin/python"),
            "-m",
            "pip",
            "install",
            "-r",
            str(ROOT / "backend/requirements.qlib.lock"),
        ],
        check=True,
    )
    subprocess.run(
        [
            str(destination / "bin/python"),
            "-c",
            "import qlib, lightgbm; print('Qlib research runtime ready:',qlib.__version__)",
        ],
        check=True,
    )
if args.engine in ("all", "lean"):
    subprocess.run(["docker", "pull", LEAN_IMAGE], check=True)
print(
    "Research runtimes ready. Start the separate workers with scripts/local.py quant-qlib or quant-lean."
)
