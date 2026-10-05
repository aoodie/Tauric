"""Check deployed sources, readiness and existing demo accounts; no broker mutations."""

import hashlib
import json
import subprocess
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
    "broker_positions.py",
    "broker_lifecycle_adapters.py",
    "broker_execution.py",
    "broker_api.py",
    "broker_contracts.py",
    "broker_models.py",
    "config.py",
]
expected = {
    name: hashlib.sha256((root / "backend/tauric" / name).read_bytes()).hexdigest()
    for name in files
}
services = [
    "api",
    "broker_execution",
    "strategy_scheduler",
    "autonomous_paper",
    "portfolio_research",
    "governance_worker",
    "chat_worker",
    "worker",
]
for service in services:
    code = (
        'import hashlib,json; from pathlib import Path; print(json.dumps({name:hashlib.sha256((Path("/app/tauric")/name).read_bytes()).hexdigest() for name in '
        + repr(files)
        + "}))"
    )
    deployed = json.loads(
        subprocess.check_output(compose + [service, "python", "-c", code], text=True)
    )
    assert deployed == expected, f"Source mismatch: {service}"
with urllib.request.urlopen(
    "http://127.0.0.1:8080/health/ready", timeout=10
) as response:
    health = json.load(response)
code = """
import json
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from tauric import broker_execution, broker_positions
from tauric.database import engine
from tauric.models import Owner
from tauric.broker_models import BrokerOrderIntent
with Session(engine()) as db:
    owner = db.scalar(select(Owner))
    accounts = {}
    for provider in ("oanda", "alpaca"):
        check = broker_execution.check_account(db, provider)
        assert check.error is None and check.account.trading_enabled and check.demo_environment
        policy = broker_execution.latest_policy(db, owner, provider)
        assert policy.payload["enabled"] and policy.payload["no_owner_limits"] and not policy.payload["automatic_entries_enabled"]
        accounts[provider] = {"environment": check.environment, "currency": check.account.currency,
            "no_owner_limits": True, "automatic_entries_enabled": False,
            "positions": [{"symbol": p.symbol, "side": p.side, "quantity": p.quantity} for p in check.positions]}
    verified = []
    for intent_id in ("cf671c61-fdfa-49f3-b235-9f06d092c876", "46907e8b-eaa6-4b92-85e5-41bdf4ac52f5"):
        result = broker_execution.reconcile(db, owner, intent_id)
        assert result.status == "cancelled" and result.latest_order.filled_quantity == "0"
        verified.append({"id": result.id, "status": result.status, "filled_quantity": "0"})
    journal = broker_positions.journal(db, owner)
    state = broker_execution.state(db, owner)
    assert state.worker_ready
    print(json.dumps({"schema": db.scalar(text("SELECT version_num FROM alembic_version")), "accounts": accounts,
        "worker_ready": state.worker_ready, "existing_verification_orders": verified,
        "journal_positions": len(journal.positions), "close_requests": len(journal.close_requests), "broker_mutations": 0}))
"""
result = json.loads(
    subprocess.check_output(compose + ["api", "python", "-"], input=code, text=True)
)
result["matching_backend_services"] = services
result["http_ready"] = health
(root / "artifacts/broker-positions-preview-verification.json").write_text(
    json.dumps(result, indent=2) + "\n"
)
print(json.dumps(result))
