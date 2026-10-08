"""Run developer commands with private .env credentials, without echoing them."""

import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
values = {}
for line in (root / ".env").read_text().splitlines():
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        values[key] = value
command = sys.argv[1] if len(sys.argv) > 1 else ""
env = os.environ.copy()
role = "tauric_admin" if command == "migrate" else "tauric_app"
password = values["POSTGRES_PASSWORD" if command == "migrate" else "TAURIC_DB_PASSWORD"]
env["TAURIC_DATABASE_URL"] = (
    f"postgresql+psycopg://{role}:{password}@127.0.0.1:{values.get('DB_PORT', '54330')}/tauric"
)
env["TAURIC_SECURE_COOKIES"] = "false"
env["TAURIC_MODEL_PROFILES_FILE"] = str(root / ".env.models.json")
env["TAURIC_MODEL_KEY_FILE"] = str(root / ".env.model-key")
env["TAURIC_OPENCLAW_PRIVATE_FILE"] = str(root / ".env.openclaw-private.json")
env["TAURIC_MARKET_CONNECTIONS_FILE"] = str(root / ".env.market-data.json")
env["TAURIC_RUNTIME_DIRECTORY"] = str(root / "artifacts/runtime")
env["TAURIC_ALLOWED_ORIGINS"] = (
    '["http://localhost:5173","http://127.0.0.1:5173","http://localhost:8080","http://127.0.0.1:8080"]'
)
python = str(root / ".venv/bin/python")
commands = {
    "worker": [str(root / ".runtime-venv/bin/python"), "-m", "tauric.worker"],
    "scanner": [python, "-m", "tauric.scanner_worker"],
    "candidate-research": [python, "-m", "tauric.candidate_research_worker"],
    "valuation": [python, "-m", "tauric.valuation_worker"],
    "forward-validation": [python, "-m", "tauric.forward_worker"],
    "broker": [python, "-m", "tauric.broker_worker"],
    "migrate": [python, "-m", "alembic", "upgrade", "head"],
    "api": [
        python,
        "-m",
        "uvicorn",
        "tauric.app:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8000",
        "--no-proxy-headers",
    ],
    "create-owner": [python, "-m", "tauric.cli", "create-owner", *sys.argv[2:]],
    "test": [python, "-m", "pytest", *sys.argv[2:]],
}
if command not in commands:
    raise SystemExit(
        "Use: local.py migrate | api | worker | scanner | candidate-research | broker | valuation | forward-validation | create-owner --username NAME | test"
    )
raise SystemExit(subprocess.call(commands[command], cwd=root / "backend", env=env))
