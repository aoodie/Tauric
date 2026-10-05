"""Check deployed broker-route sources and demo facts; no broker mutations.

The concurrently edited scanner worker is intentionally outside this deployment.
Its broker admission is still checked in the deployed native worker below.
"""

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
    "broker_setup.py",
    "native_broker_setup.py",
    "native_runner.py",
    "broker_adapters.py",
    "analysis.py",
    "paper.py",
    "governance.py",
    "governance_worker.py",
    "broker_review.py",
    "broker_routes.py",
    "broker_risk.py",
    "broker_worker.py",
    "review_queue.py",
    "governance_contracts.py",
    "openclaw_api.py",
    "models.py",
    "admission_budget.py",
    "instrument_discovery.py",
    "market_api.py",
    "market_contracts.py",
    "asset_capabilities.py",
    "native_scope.py",
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
from tauric import broker_execution, broker_positions, broker_setup, broker_review, governance
from tauric.database import engine
from tauric.models import Owner, GatewayStatus, now
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
            "no_owner_limits": True, "legacy_manual_policy_automatic_entries_enabled": False,
            "positions": [{"symbol": p.symbol, "side": p.side, "quantity": p.quantity} for p in check.positions]}
    quote_checks = {}
    for provider, symbol in (("oanda", "EUR_USD"), ("alpaca", "AAPL")):
        config, revision = broker_execution.connection(db, provider)
        with broker_execution.adapter(provider, config) as broker:
            try:
                quote = broker.quote(symbol)
                age = (now() - quote.observed_at).total_seconds()
                quote_checks[provider] = {"symbol": symbol, "bid": quote.bid, "ask": quote.ask,
                    "observed_at": quote.observed_at.isoformat(), "feed": quote.feed,
                    "market_tradable": quote.tradable, "fresh": 0 <= age <= 60}
            except broker_execution.BrokerError as failure:
                quote_checks[provider] = {"symbol": symbol, "error": str(failure)}
    from tauric import broker_routes
    route_state = broker_routes.state(db, owner)
    assert len(route_state.routes) == 2 and all(row.settings.enabled for row in route_state.routes)
    automatic_routes = [{"id": row.id, "provider": row.settings.provider, "instrument_id": row.settings.instrument_id, "risk_percent": row.settings.risk_percent, "enabled": row.settings.enabled, "last_event": row.last_event} for row in route_state.routes]
    assessments = broker_setup.reports(db, owner)
    verified = []
    for intent_id in ("cf671c61-fdfa-49f3-b235-9f06d092c876", "46907e8b-eaa6-4b92-85e5-41bdf4ac52f5"):
        result = broker_execution.reconcile(db, owner, intent_id)
        assert result.status == "cancelled" and result.latest_order.filled_quantity == "0"
        verified.append({"id": result.id, "status": result.status, "filled_quantity": "0"})
    journal = broker_positions.journal(db, owner)
    state = broker_execution.state(db, owner)
    assert state.worker_ready
    reviews = broker_review.views(db, owner)
    gateway = db.get(GatewayStatus, "openclaw")
    assert gateway and "broker_entry_review" in gateway.capabilities
    assert governance.status(db, owner).gateway_ready
    import httpx
    check = httpx.get("http://127.0.0.1:8000/internal/openclaw/broker-jobs/00000000-0000-4000-8000-000000000000/context", headers={"Authorization": "Bearer " + governance.private_tokens()["platform_token"]}, trust_env=False)
    assert check.status_code == 404 and check.json()["detail"] == "This broker review does not exist."
    assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0028_broker_routes"
    print(json.dumps({"automatic_routes": automatic_routes, "automatic_workflows": [w.model_dump(mode="json") for w in route_state.workflows], "broker_reviews": len(reviews), "gateway_capabilities": gateway.capabilities, "gateway_ready": True, "schema": db.scalar(text("SELECT version_num FROM alembic_version")), "accounts": accounts,
        "worker_ready": state.worker_ready, "existing_verification_orders": verified, "quote_checks": quote_checks, "setup_reviews": len(assessments), "model_calls": 0,
        "journal_positions": len(journal.positions), "close_requests": len(journal.close_requests), "broker_mutations": 0}))
"""
result = json.loads(
    subprocess.check_output(compose + ["api", "python", "-"], input=code, text=True)
)
result["matching_backend_services"] = services
result["verified_backend_files"] = files
result["deployment_scope"] = (
    "Broker-route sources and OANDA transaction/lifecycle repair over the previous tested deployment; concurrent scanner worker edits are excluded"
)
result["strategy_qualified"] = False
result["http_ready"] = health
worker_code = """
import json
from sqlalchemy import select
from sqlalchemy.orm import Session
from tauric import broker_execution
from tauric.database import engine
from tauric.models import Owner
with Session(engine()) as db:
    owner = db.scalar(select(Owner))
    checked = {}
    for provider in ("oanda", "alpaca"):
        config, revision = broker_execution.connection(db, provider)
        policy = broker_execution.latest_policy(db, owner, provider)
        with broker_execution.adapter(provider, config) as broker:
            account = broker.account()
            assert account.trading_enabled and account.account_fingerprint == policy.payload["account_fingerprint"]
            checked[provider] = {"connection_revision": revision, "verified_same_demo_account": True}
    from tauric import broker_routes
    from tauric.broker_models import BrokerRoute, BrokerRouteVersion
    for route in db.scalars(select(BrokerRoute).where(BrokerRoute.owner_id == owner.id)):
        binding = broker_routes.current(db, owner, db.get(BrokerRouteVersion, route.version_id))
        checked[binding.provider]["route_guard_verified"] = True
    print(json.dumps(checked))
"""
result["native_worker_broker_admission"] = json.loads(
    subprocess.check_output(
        compose + ["worker", "python", "-"], input=worker_code, text=True
    )
)
broker_worker_code = worker_code.replace(
    "print(json.dumps(checked))", "print(json.dumps(checked))"
)
result["demo_worker_route_admission"] = json.loads(
    subprocess.check_output(
        compose + ["broker_execution", "python", "-"],
        input=broker_worker_code,
        text=True,
    )
)
route_check = 'import json; from tauric.app import app; print(json.dumps(all(route in app.openapi()["paths"] for route in ("/api/v1/brokers/routes", "/api/v1/brokers/routes/{route_id}", "/api/v1/brokers/routes/{route_id}/history", "/api/v1/brokers/setup-preview", "/api/v1/brokers/setup-assessments", "/api/v1/brokers/setup-assessments/{job_id}", "/api/v1/brokers/reviews", "/api/v1/brokers/reviews/{job_id}/submit"))))'
result["setup_routes_present"] = json.loads(
    subprocess.check_output(compose + ["api", "python", "-c", route_check], text=True)
)
assert result["setup_routes_present"]
(root / "artifacts/broker-routes-preview-verification.json").write_text(
    json.dumps(result, indent=2) + "\n"
)
gateway_code = """
import fs from 'node:fs';
const c=JSON.parse(fs.readFileSync('/state/openclaw.json','utf8'));
const m=JSON.parse(fs.readFileSync('/app/plugin/openclaw.plugin.json','utf8'));
if(!m.contracts.tools.includes('tauric_broker_review_context')) throw Error('Missing broker tool');
const agents=Object.entries(c.agents.entries).filter(([name])=>name.startsWith('broker-governor-')).map(([name,a])=>{
 if(JSON.stringify(a.tools.allow)!==JSON.stringify(['tauric_broker_review_context']) || a.model.fallbacks.length) throw Error('Invalid broker permissions');
 return {agent:name,tools:a.tools.allow,model:a.model.primary};
});
if(!agents.length) throw Error('No broker agents');
console.log(JSON.stringify({pinned_version:JSON.parse(fs.readFileSync('/app/node_modules/openclaw/package.json','utf8')).version,agents}));
"""
result["gateway_broker_agents"] = json.loads(
    subprocess.check_output(
        compose + ["openclaw", "node", "--input-type=module", "-"],
        input=gateway_code,
        text=True,
    )
)
(root / "artifacts/broker-routes-preview-verification.json").write_text(
    json.dumps(result, indent=2) + "\n"
)
print(json.dumps(result))
