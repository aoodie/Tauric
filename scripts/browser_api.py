import json
import os
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
fixture = json.loads((root / "artifacts/browser-test.json").read_text())
env = os.environ.copy()
env["TAURIC_DATABASE_URL"] = fixture["database_url"]
env["TAURIC_MODEL_KEY_FILE"] = str(root / "artifacts/browser-model-key")
env["TAURIC_OPENCLAW_PRIVATE_FILE"] = str(root / ".env.openclaw-private.json")
env["TAURIC_SECURE_COOKIES"] = "false"
env["TAURIC_ALLOWED_ORIGINS"] = '["http://127.0.0.1:5173","http://localhost:5173"]'
raise SystemExit(
    subprocess.call(
        [
            str(root / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "tauric.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--no-proxy-headers",
        ],
        cwd=root / "backend",
        env=env,
    )
)
