"""Check deployed scanner source, service health and real cycle evidence.

Uses only read-only container operations. Reports checksums and public data;
never credentials, model traces or broker account identifiers.
"""

import hashlib
import json
import re
import subprocess
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
SERVICES = [
    "api",
    "scanner",
    "candidate_research",
    "worker",
    "strategy_scheduler",
    "broker_execution",
    "autonomous_paper",
    "portfolio_research",
    "chat_worker",
    "governance_worker",
]


def main():
    expected = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "backend/tauric").glob("*.py")
    }
    code = "import hashlib,json;from pathlib import Path;print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/tauric').glob('*.py')}))"
    for service in SERVICES:
        deployed = json.loads(
            subprocess.check_output(
                COMPOSE + ["exec", "-T", service, "python", "-c", code], text=True
            )
        )
        assert deployed == expected, f"Deployed backend source mismatch: {service}"
    raw = subprocess.check_output(COMPOSE + ["ps", "--format", "json"], text=True)
    services = [
        {k: row[k] for k in ("Service", "State", "Health")}
        for row in map(json.loads, raw.splitlines())
    ]
    assert all(
        s["State"] == "running" and s["Health"] in ("", "healthy") for s in services
    )
    with urllib.request.urlopen(
        "http://127.0.0.1:8080/health/ready", timeout=10
    ) as response:
        health = json.load(response)
    with urllib.request.urlopen("http://127.0.0.1:8080", timeout=10) as response:
        html = response.read().decode()
    assets = []
    for asset in re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html):
        with urllib.request.urlopen(
            "http://127.0.0.1:8080" + asset, timeout=10
        ) as response:
            remote = response.read()
        local = ROOT / "frontend/dist" / asset.lstrip("/")
        matches = (
            local.exists()
            and hashlib.sha256(remote).digest()
            == hashlib.sha256(local.read_bytes()).digest()
        )
        assert matches, f"Served frontend asset mismatch: {asset}"
        assets.append({"asset": asset, "matches_local_build": matches})
    assert assets, "Preview does not expose production frontend assets."
    cycles = json.loads(
        subprocess.check_output(
            COMPOSE + ["exec", "-T", "api", "python", "-", "inspect"],
            input=(ROOT / "scripts/scanner_rollout_acceptance.py").read_text(),
            text=True,
        )
    )
    assert cycles["scheduled_runs_total"] > 0, (
        "Scheduled scanner has not completed a cycle."
    )
    baseline = json.loads(
        (ROOT / "artifacts/scanner-rollout-baseline.json").read_text()
    )
    assert cycles["protected_configuration"] == baseline["protected_configuration"], (
        "Owner configuration changed during rollout; inspect before continuing."
    )
    report = {
        "checked_at": cycles["checked_at"],
        "http_ready": health,
        "services": services,
        "matching_backend_services": SERVICES,
        "matching_source_files": len(expected),
        "backend_source_checksums": expected,
        "cycles": cycles,
        "protected_configuration_unchanged": True,
        "frontend_assets": assets,
    }
    automatic = cycles["parameters"].get("automatic_research_enabled", False)
    if automatic:
        assert cycles["automatic_research_ready"], (
            "Automatic research coordinator is unavailable."
        )
        assert len(cycles["parameters"]["detector_ids"]) == 3
        assert cycles["charged_today"] <= cycles["parameters"]["max_analyses_per_day"]
    target = (
        ROOT
        / "artifacts"
        / (
            "automatic-scanner-preview-verification.json"
            if automatic
            else "scanner-preview-verification.json"
        )
    )
    target.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "http_ready": health,
                "matching_backend_services": SERVICES,
                "matching_source_files": len(expected),
                "scheduled_runs": cycles["scheduled_runs_total"],
                "automatic_research_ready": cycles["automatic_research_ready"],
                "charged_today": cycles["charged_today"],
                "automatic_receipts": cycles["automatic_receipts"],
                "reason_counts": cycles["recent_reason_counts"],
                "research": cycles["research"],
                "feeds": cycles["feeds"],
                "protected_configuration_unchanged": True,
                "report": str(target),
            }
        )
    )


if __name__ == "__main__":
    main()
