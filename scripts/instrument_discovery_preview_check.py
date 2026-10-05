"""Read-only discovery deployment checks; credentials and account details remain private."""

import hashlib
import json
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1]
compose = [
    "docker",
    "compose",
    "--env-file",
    str(root / ".env"),
    "-f",
    str(root / "deploy/compose.dev.yml"),
    "exec",
    "-T",
]
files = [
    "instrument_discovery.py",
    "market_api.py",
    "market_contracts.py",
    "asset_capabilities.py",
    "native_scope.py",
    "config.py",
]
expected = {
    name: hashlib.sha256((root / "backend/tauric" / name).read_bytes()).hexdigest()
    for name in files
}
services = [
    "api",
    "worker",
    "broker_execution",
    "strategy_scheduler",
    "autonomous_paper",
    "portfolio_research",
    "governance_worker",
    "chat_worker",
]
for service in services:
    code = (
        'import hashlib,json; from pathlib import Path; print(json.dumps({name:hashlib.sha256((Path("/app/tauric")/name).read_bytes()).hexdigest() for name in '
        + repr(files)
        + "}))"
    )
    result = subprocess.run(
        compose + [service, "python", "-c", code],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, (
        "Deployment hash read failed; private details withheld"
    )
    assert json.loads(result.stdout) == expected, (
        f"Discovery source mismatch: {service}"
    )
with urllib.request.urlopen(
    "http://127.0.0.1:8080/health/ready", timeout=10
) as response:
    assert json.load(response)["status"] == "ok"
with urllib.request.urlopen("http://127.0.0.1:8080/", timeout=10) as response:
    html = response.read().decode()
scripts = re.findall(r'src="(/assets/[^" ]+\.js)"', html)
assert scripts
served_ui = ""
for path in scripts:
    with urllib.request.urlopen("http://127.0.0.1:8080" + path, timeout=10) as response:
        served_ui += response.read().decode()
assert "Find more symbols" in served_ui and "Search symbols" in served_ui
try:
    urllib.request.urlopen(
        "http://127.0.0.1:8080/api/v1/instrument-discovery/alpaca", timeout=10
    )
    raise AssertionError("Discovery must require owner authentication")
except urllib.error.HTTPError as error:
    assert error.code == 401
code = """
import json
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from tauric.database import engine
from tauric.instrument_discovery import discover
with Session(engine()) as db:
    revision=db.scalar(text("SELECT version_num FROM alembic_version"))
    assert revision=="0027_instrument_discovery"
    assert db.scalar(text("SELECT has_table_privilege('tauric_app','instruments','INSERT')"))
    providers={}
    for provider in ('oanda','alpaca'):
        found=discover(db,provider,limit=1)
        providers[provider]={'total':found.total,'checked_at':found.checked_at}
    print(json.dumps({'schema':revision,'providers':providers,'read_only':True}))
    db.rollback()
"""
result = subprocess.run(
    compose + ["api", "python", "-c", code],
    capture_output=True,
    text=True,
    timeout=60,
    check=False,
)
assert result.returncode == 0, (
    "Deployed catalog verification failed; private details withheld"
)
evidence = {
    **json.loads(result.stdout),
    "source_modules": len(files),
    "backend_services": len(services),
    "readiness": True,
    "unauthenticated_discovery_rejected": True,
    "discovery_picker_served": True,
}
(root / "artifacts/instrument-discovery-preview-verification.json").write_text(
    json.dumps(evidence, indent=2) + "\n"
)
print(json.dumps(evidence))
