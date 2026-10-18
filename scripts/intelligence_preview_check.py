"""Read-only preview checks; keep owner configuration and worker selection intact."""

import hashlib
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / ".env"),
    "-f",
    str(ROOT / "deploy/compose.dev.yml"),
]
INSPECT = """
import hashlib,json
from sqlalchemy import text
from tauric.database import engine
with engine().connect() as db:
    tables=db.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename LIKE '%versions' ORDER BY tablename")).all()
    result={}
    for table in tables:
        rows=db.scalars(text(f'SELECT to_jsonb(t)::text FROM "{table}" t')).all()
        rows=sorted(json.dumps(json.loads(row),sort_keys=True,separators=(',',':')) for row in rows)
        result[table]={'count':len(rows),'checksum':hashlib.sha256('\\n'.join(rows).encode()).hexdigest()}
    print(json.dumps({'configuration':result,'schema':db.scalar(text('SELECT version_num FROM alembic_version')),'research_jobs':dict(db.execute(text('SELECT status,count(*) FROM analysis_jobs GROUP BY status')).all())}))
"""


def run(service, code):
    return json.loads(
        subprocess.check_output(
            COMPOSE + ["exec", "-T", service, "python", "-c", code], text=True
        )
    )


def services():
    rows = map(
        json.loads,
        subprocess.check_output(
            COMPOSE + ["ps", "--format", "json"], text=True
        ).splitlines(),
    )
    return [{k: row[k] for k in ("Service", "State", "Health")} for row in rows]


def main():
    baseline_path = ROOT / "artifacts/intelligence-preview-baseline.json"
    current = run("api", INSPECT)
    if sys.argv[1:] == ["baseline"]:
        current["services"] = services()
        assert not current["research_jobs"].get("running") and not current[
            "research_jobs"
        ].get("queued")
        baseline_path.write_text(json.dumps(current, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "schema": current["schema"],
                    "protected_tables": len(current["configuration"]),
                    "active_research_jobs": 0,
                }
            )
        )
        return
    baseline = json.loads(baseline_path.read_text())
    coordinated = sys.argv[1:] == ["--coordinated-scanner-rollout"]
    changed_configuration = sorted(
        name
        for name in current["configuration"]
        if current["configuration"][name] != baseline["configuration"].get(name)
    )
    if coordinated:
        assert set(changed_configuration) <= {"scanner_profile_versions"}, (
            "Unrelated owner configuration changed."
        )
        assert (
            current["configuration"]["scanner_profile_versions"]["count"]
            >= baseline["configuration"]["scanner_profile_versions"]["count"]
        )
    else:
        assert not changed_configuration, "Owner configuration changed."
    assert current["schema"] == "0029_market_intelligence"
    live = services()
    previous_services = {s["Service"] for s in baseline["services"]}
    live_services = {s["Service"] for s in live}
    if coordinated:
        assert (
            previous_services
            <= live_services
            <= previous_services | {"candidate_research"}
        ), "Unrelated running service selection changed."
    else:
        assert live_services == previous_services, "Running service selection changed."
    assert all(s["State"] == "running" and s["Health"] in ("", "healthy") for s in live)
    expected = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "backend/tauric").glob("*.py")
    }
    code = "import hashlib,json;from pathlib import Path;print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/tauric').glob('*.py')}))"
    backend_services = [
        s["Service"] for s in live if s["Service"] not in {"db", "web", "openclaw"}
    ]
    intelligence_files = {
        "app.py",
        "forex_macro.py",
        "intelligence.py",
        "intelligence_api.py",
        "intelligence_contracts.py",
        "intelligence_models.py",
        "intelligence_sources.py",
        "intelligence_catalog.py",
        "intelligence_prices.py",
    }
    other_changes = {}
    for service in backend_services:
        deployed = run(service, code)
        assert all(
            deployed.get(name) == expected[name] for name in intelligence_files
        ), f"Deployed intelligence source differs: {service}"
        differences = sorted(
            name for name in expected if deployed.get(name) != expected[name]
        )
        if differences:
            other_changes[service] = differences
    with urllib.request.urlopen(
        "http://127.0.0.1:8080/health/ready", timeout=10
    ) as response:
        health = json.load(response)
    with urllib.request.urlopen("http://127.0.0.1:8080", timeout=10) as response:
        html = response.read().decode()
    assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html)
    assert assets
    for asset in assets:
        with urllib.request.urlopen(
            "http://127.0.0.1:8080" + asset, timeout=10
        ) as response:
            assert (
                hashlib.sha256(response.read()).digest()
                == hashlib.sha256(
                    (ROOT / "frontend/dist" / asset.lstrip("/")).read_bytes()
                ).digest()
            )
    code = "import json;from tauric.app import app;print(json.dumps(all(p in app.openapi()['paths'] for p in ['/api/v1/intelligence','/api/v1/intelligence/catalogs/refresh','/api/v1/intelligence/register','/api/v1/intelligence/refresh','/api/v1/intelligence/watchlist','/api/v1/intelligence/receipts/{receipt_id}','/api/v1/intelligence/reports/{instrument_id}'])))"
    assert run("api", code), "Intelligence routes missing."
    report = {
        "health": health,
        "schema": current["schema"],
        "protected_configuration_unchanged": not changed_configuration,
        "running_service_selection_unchanged": live_services == previous_services,
        "coordinated_scanner_rollout": coordinated,
        "coordinated_configuration_changes": changed_configuration,
        "unrelated_configuration_unchanged": True,
        "matching_backend_services": backend_services,
        "matching_intelligence_source_files": len(intelligence_files),
        "other_current_workspace_changes": other_changes,
        "frontend_assets": assets,
        "research_jobs": current["research_jobs"],
        "services": live,
    }
    target = ROOT / "artifacts/intelligence-preview-verification.json"
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
