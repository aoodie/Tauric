"""Stage a private retained-key rotation without changing active credentials."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from tauric.credential_keys import stage_rotation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = stage_rotation(args.key_file, args.output)
    except (OSError, ValueError):
        raise SystemExit("Rotation was not activated. Check the input key, permissions and unused output path.") from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
