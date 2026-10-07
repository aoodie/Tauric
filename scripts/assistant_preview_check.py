"""Read-only local rollout evidence; never submits research, chat or broker actions.

Baseline stores configuration hashes and activity counts, never saved credentials.
Verification compares deployed code, source excerpts, assets and agent permissions.
"""

import argparse
import hashlib
import json
import re
import subprocess
import urllib.request
from datetime import UTC, datetime
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
CONFIGURATION_TABLES = [
    "model_connection_versions",
    "market_connection_versions",
    "chat_config_versions",
    "strategy_versions",
    "scanner_profile_versions",
    "setting_versions",
    "shared_admission_budget_versions",
    "portfolio_research_policy_versions",
    "autonomy_policy_versions",
    "strategy_schedule_versions",
    "deployment_control_versions",
    "capital_scope_versions",
    "portfolio_policy_versions",
    "broker_policy_versions",
    "broker_route_versions",
    "paper_deployments",
]
TERMINAL_STATES = {
    "analysis_jobs": {"succeeded", "failed", "interrupted", "cancelled"},
    "chat_turns": {"succeeded", "failed", "interrupted", "cancelled"},
    "governance_jobs": {"succeeded", "failed", "interrupted", "cancelled"},
    "broker_review_jobs": {"succeeded", "failed", "interrupted", "cancelled"},
    "portfolio_research_runs": {"succeeded", "failed", "partial", "cancelled"},
    "broker_order_intents": {"filled", "rejected", "cancelled", "expired"},
    "broker_close_requests": {"filled", "rejected", "cancelled", "expired"},
    "broker_route_workflows": {"blocked", "closed", "cancelled", "failed"},
}


def snapshot():
    # This branch runs inside the API container, including before migration.
    from sqlalchemy import text
    from tauric.database import engine

    with engine().connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        configuration = {}
        for table in CONFIGURATION_TABLES:
            rows = [
                dict(row)
                for row in connection.execute(
                    text(f"SELECT * FROM {table} ORDER BY id")
                ).mappings()
            ]
            configuration[table] = {
                "count": len(rows),
                "sha256": hashlib.sha256(
                    json.dumps(rows, sort_keys=True, default=str).encode()
                ).hexdigest(),
            }
        activity = {}
        for table in TERMINAL_STATES:
            activity[table] = dict(
                connection.execute(
                    text(f"SELECT status, count(*) FROM {table} GROUP BY status")
                ).all()
            )
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
    active = {
        table: {
            status: count
            for status, count in counts.items()
            if status not in TERMINAL_STATES[table]
        }
        for table, counts in activity.items()
    }
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "schema_revision": revision,
        "configuration": configuration,
        "activity": activity,
        "active_tasks": {table: counts for table, counts in active.items() if counts},
    }


def container_json(service, code):
    return json.loads(
        subprocess.check_output(
            COMPOSE + ["exec", "-T", "--user", "10001", service, "python", "-c", code],
            text=True,
        )
    )


def deployed_snapshot():
    return json.loads(
        subprocess.check_output(
            COMPOSE
            + ["exec", "-T", "--user", "10001", "api", "python", "-", "snapshot"],
            input=Path(__file__).read_text(),
            text=True,
        )
    )


def verify(
    baseline_path=None,
    report_path=None,
    expected_revision="0030_assistant_management",
    initialized_tables=(),
):
    from tauric.assistant import file_hash, source_index, source_path

    baseline_path = (
        baseline_path or ROOT / "artifacts/assistant-upgrade-preview-baseline.json"
    )
    report_path = (
        report_path or ROOT / "artifacts/assistant-upgrade-preview-verification.json"
    )
    baseline = json.loads(baseline_path.read_text())
    current = deployed_snapshot()
    changed = [
        name
        for name, value in current["configuration"].items()
        if baseline["configuration"][name] != value
    ]
    for name in changed:
        assert (
            name in initialized_tables
            and baseline["configuration"][name]["count"] == 0
            and current["configuration"][name]["count"] == 1
        ), "Saved owner configuration changed during rollout"
    assert current["schema_revision"] == expected_revision
    expected = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (ROOT / "backend/tauric").glob("*.py")
    }
    for service in SERVICES:
        actual = container_json(
            service,
            "import hashlib,json;from pathlib import Path;print(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/app/tauric').glob('*.py')}))",
        )
        assert actual == expected, f"Backend code differs in {service}"
    expected_source = {path: file_hash(source_path(path)) for path in source_index()}
    actual_source = container_json(
        "api",
        "import json,os;from tauric.assistant import source_index,source_path,file_hash;print(json.dumps({'files':{p:file_hash(source_path(p)) for p in source_index()},'uid':os.getuid(),'writable':os.access('/app/project-source',os.W_OK)}))",
    )
    assert actual_source["files"] == expected_source, (
        "Assistant source snapshot is stale"
    )
    assert actual_source["uid"] == 10001
    assert actual_source["writable"] is False, (
        "Assistant source snapshot must be read-only to API user"
    )
    schema = container_json(
        "api", "import json;from tauric.app import app;print(json.dumps(app.openapi()))"
    )
    assert schema == json.loads((ROOT / "backend/openapi.json").read_text()), (
        "Public API schema differs"
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
            content = response.read()
        assert (
            hashlib.sha256(content).digest()
            == hashlib.sha256(
                (ROOT / "frontend/dist" / asset.lstrip("/")).read_bytes()
            ).digest()
        ), f"Served asset differs: {asset}"
        assets.append(asset)
    assert assets
    gateway_code = "import fs from 'node:fs';const c=JSON.parse(fs.readFileSync('/state/openclaw.json','utf8'));const p=JSON.parse(fs.readFileSync('/app/plugin/openclaw.plugin.json','utf8'));console.log(JSON.stringify({agents:Object.entries(c.agents.entries??{}).map(([id,a])=>({id,tools:a.tools.allow})),codeMode:c.tools.codeMode,commands:Object.fromEntries(Object.entries(c.commands).filter(([k,v])=>typeof v==='boolean')),tools:p.contracts.tools,instructions:fs.readFileSync('/app/chat_instructions.txt','utf8')}));"
    gateway = json.loads(
        subprocess.check_output(
            COMPOSE
            + [
                "exec",
                "-T",
                "openclaw",
                "node",
                "--input-type=module",
                "-e",
                gateway_code,
            ],
            text=True,
        )
    )
    assert (
        gateway.pop("instructions")
        == (ROOT / "backend/tauric/chat_instructions.txt").read_text()
    )
    assert gateway["codeMode"] is False
    assert gateway["commands"] and all(
        value is False for value in gateway["commands"].values()
    )
    gateway_files = [
        "config.mjs",
        "plugin/src/index.ts",
        "plugin/dist/index.js",
        "plugin/openclaw.plugin.json",
    ]
    gateway_hashes_code = (
        "import fs from 'node:fs';import {createHash} from 'node:crypto';const files="
        + json.dumps(gateway_files)
        + ";console.log(JSON.stringify(Object.fromEntries(files.map(p=>[p,createHash('sha256').update(fs.readFileSync('/app/'+p)).digest('hex')]))));"
    )
    gateway_hashes = json.loads(
        subprocess.check_output(
            COMPOSE
            + [
                "exec",
                "-T",
                "openclaw",
                "node",
                "--input-type=module",
                "-e",
                gateway_hashes_code,
            ],
            text=True,
        )
    )
    assert gateway_hashes == {
        path: hashlib.sha256((ROOT / "openclaw" / path).read_bytes()).hexdigest()
        for path in gateway_files
    }, "OpenClaw code differs from verified local build"
    assert (
        gateway["tools"]
        == json.loads((ROOT / "openclaw/plugin/openclaw.plugin.json").read_text())[
            "contracts"
        ]["tools"]
    )
    managers = [a for a in gateway["agents"] if a["id"] == "tauric-chat"]
    assert managers and all(
        {"tauric_project_read", "tauric_project_action"}.issubset(a["tools"])
        for a in managers
    )
    for agent in gateway["agents"]:
        if agent["id"] != "tauric-chat":
            assert len(agent["tools"]) == 1 and agent["tools"][0] in {
                "tauric_review_context",
                "tauric_broker_review_context",
            }, "Governor tools were broadened"
    raw = subprocess.check_output(COMPOSE + ["ps", "--format", "json"], text=True)
    services = [
        {key: row[key] for key in ("Service", "State", "Health")}
        for row in map(json.loads, raw.splitlines())
    ]
    assert all(
        s["State"] == "running" and s["Health"] in ("", "healthy") for s in services
    ), "A preview service is not healthy"
    report = {
        "checked_at": current["checked_at"],
        "schema_revision": current["schema_revision"],
        "owner_configuration_unchanged": not changed,
        "existing_owner_configuration_unchanged": True,
        "new_configuration_tables": changed,
        "configuration": current["configuration"],
        "matching_backend_services": SERVICES,
        "matching_backend_source_files": len(expected),
        "matching_read_only_source_files": len(expected_source),
        "matching_frontend_assets": assets,
        "public_api_schema_matches": True,
        "http_ready": health,
        "services": services,
        "gateway_permissions": gateway,
        "matching_gateway_source_files": gateway_files,
        "activity": current["activity"],
        "verification_model_requests": 0,
        "verification_broker_order_requests": 0,
        "scope": "Local preview only; no live model-quality or production release certification",
    }
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "schema_revision",
                    "owner_configuration_unchanged",
                    "matching_backend_source_files",
                    "matching_read_only_source_files",
                    "matching_frontend_assets",
                    "services",
                )
            },
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["snapshot", "baseline", "verify"])
    args = parser.parse_args()
    if args.phase == "snapshot":
        print(json.dumps(snapshot()))
    elif args.phase == "baseline":
        value = deployed_snapshot()
        assert not value["active_tasks"], (
            f"Preview has active tasks: {value['active_tasks']}"
        )
        (ROOT / "artifacts/assistant-upgrade-preview-baseline.json").write_text(
            json.dumps(value, indent=2) + "\n"
        )
        print(
            json.dumps(
                {
                    "active_tasks": value["active_tasks"],
                    "schema_revision": value["schema_revision"],
                    "configuration_tables_checked": len(value["configuration"]),
                }
            )
        )
    else:
        verify()


if __name__ == "__main__":
    main()
