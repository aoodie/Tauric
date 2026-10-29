"""Compare runtime, saved OpenAPI and TypeScript without changing the checkout."""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main():
    from tauric.app import app

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Explicitly refresh both tracked contracts")
    args = parser.parse_args()
    saved = ROOT / "backend/openapi.json"
    types = ROOT / "frontend/src/api/schema.d.ts"
    generated = app.openapi()
    with TemporaryDirectory(prefix="tauric-contract-") as folder:
        schema = Path(folder) / "openapi.json"
        declarations = Path(folder) / "schema.d.ts"
        schema.write_text(json.dumps(generated, indent=2) + "\n")
        subprocess.run([str(ROOT / "frontend/node_modules/.bin/openapi-typescript"), str(schema), "-o", str(declarations)], check=True)
        subprocess.run([str(ROOT / "frontend/node_modules/.bin/prettier"), "--write", str(declarations)], check=True, stdout=subprocess.DEVNULL)
        if args.write:
            saved.write_bytes(schema.read_bytes())
            types.write_bytes(declarations.read_bytes())
        elif generated != json.loads(saved.read_text()) or declarations.read_bytes() != types.read_bytes():
            raise SystemExit("API contracts are stale. Refresh both artifacts with scripts/check_api_contract.py --write.")
    print("Runtime OpenAPI and TypeScript contracts match.")


if __name__ == "__main__":
    main()
