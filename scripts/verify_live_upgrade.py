"""Read-only release verification. Report hashes/status, never credentials."""

import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / ".env"),
    "-f",
    str(ROOT / "deploy/compose.dev.yml"),
    "-f",
    str(ROOT / "deploy/compose.memory-app.yml"),
]


def command(args, source=None):
    result = subprocess.run(
        COMPOSE + args,
        input=source,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("A release verification command failed.")
    return result.stdout


def main():
    files = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT / "backend/tauric").glob("*.py"))
    }
    probe = "import hashlib,json; from pathlib import Path; print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/tauric').glob('*.py')}))"
    matches = {}
    for service in (
        "api",
        "worker",
        "broker_execution",
        "governance_worker",
        "valuation",
        "forward_validation",
    ):
        hashes = json.loads(command(["exec", "-T", service, "python", "-c", probe]))
        matches[service] = hashes == files
    if not all(matches.values()):
        raise RuntimeError("Running services differ from the verified source files.")
    statuses = [
        json.loads(line)
        for line in command(["ps", "--format", "json"]).splitlines()
        if line.strip()
    ]
    service_states = {
        r["Service"]: {k: r[k] for k in ("State", "Health")} for r in statuses
    }
    if any(
        r["State"] != "running" or r["Health"] not in ("", "healthy")
        for r in service_states.values()
    ):
        raise RuntimeError("A running release service is not healthy.")
    source = """import json
from sqlalchemy import select,text
from sqlalchemy.orm import Session
from tauric.database import engine
from tauric.models import Owner
from tauric.app import app
from tauric import live_trading,analysis
with Session(engine()) as db:
    owner=db.scalar(select(Owner))
    state=live_trading.state(db,owner)
    print(json.dumps({'version':app.version,'schema':db.scalar(text('select version_num from alembic_version')),'brokers':[b.model_dump(mode='json') for b in state.brokers],'valuation_worker_ready':state.valuation_worker_ready,'forward_worker_ready':state.forward_worker_ready,'native_worker_ready':analysis.status(db).worker_ready,'forward_protocols':len(state.forward_protocols),'passed_forward_reports':sum(r.payload['status']=='passed' for r in state.forward_reports),'accounts':[{k:a.get(k) for k in ('name','mode','currency','equity','gross_exposure','complete','errors')} for a in state.valuations[0].payload['accounts']] if state.valuations else []}))
"""
    runtime = json.loads(command(["exec", "-T", "api", "python", "-"], source))
    result = {
        "source_hashes_match": matches,
        "services": service_states,
        "runtime": runtime,
        "orders_submitted_by_verification": 0,
    }
    (ROOT / "artifacts/live-upgrade-verification.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
