"""Check the approved local memory rollout without model or broker requests."""

import argparse
import json
import subprocess
from pathlib import Path

import assistant_preview_check as preview

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "artifacts/memory-upgrade-preview-baseline.json"
REPORT = ROOT / "artifacts/memory-upgrade-preview-verification.json"
preview.COMPOSE += ["-f", str(ROOT / "deploy/compose.memory-app.yml")]
MEMORY_COMPOSE = [
    "docker",
    "compose",
    "--env-file",
    str(ROOT / ".env.honcho"),
    "-f",
    str(ROOT / "deploy/compose.honcho.yml"),
]

RUNTIME_CHECK = """
import json
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from tauric.database import engine
from tauric.models import Owner,now
from tauric import knowledge
from tauric.knowledge_models import HonchoDelivery,KnowledgeDocument,MemorySyncState
with Session(engine()) as db:
    own=db.scalar(select(Owner))
    value=knowledge.state(db,own)
    sync=db.get(MemorySyncState,own.id)
    config=value.settings
    assert config.enabled and config.honcho_enabled
    assert not config.allow_external_context
    assert not value.chat_recall_allowed
    assert knowledge.context(db,own,'strategy research') == {}
    assert value.honcho_configured and value.vault_configured and value.worker_ready
    assert value.last_collected_at
    assert sync and not sync.last_error
    counts=dict(db.execute(select(HonchoDelivery.status,func.count()).join(KnowledgeDocument,KnowledgeDocument.id==HonchoDelivery.document_id).where(KnowledgeDocument.owner_id==own.id).group_by(HonchoDelivery.status)).all())
    folder=knowledge.vault_root(own.id)
    files=list(folder.rglob('*.md'))
    print(json.dumps({'settings_revision':value.revision,'settings':config.model_dump(),'counts':value.counts,'worker_ready':value.worker_ready,'last_collected_at':value.last_collected_at.isoformat(),'last_honcho_at':value.last_honcho_at.isoformat() if value.last_honcho_at else None,'honcho_configured':value.honcho_configured,'honcho_delivery_counts':counts,'cached_profile_available':bool(value.honcho_representation),'chat_recall_allowed':value.chat_recall_allowed,'chat_recall_detail':value.chat_recall_detail,'remote_auto_context_empty':True,'vault_location':value.vault_location,'vault_files':len(files),'vault_conflicts':value.vault_conflicts,'retired_workspaces_pending':len(sync.honcho_retired_workspaces)}))
"""


def memory_network_check():
    raw = subprocess.check_output(
        MEMORY_COMPOSE + ["ps", "--format", "json"], text=True
    )
    rows = [json.loads(line) for line in raw.splitlines()]
    expected = {"honcho", "memory-model", "memory-db", "memory-cache", "memory-deriver"}
    assert {row["Service"] for row in rows} == expected
    inspected = json.loads(
        subprocess.check_output(
            ["docker", "inspect", *[r["ID"] for r in rows]], text=True
        )
    )
    network = "tauric_memory_memory_private"
    assert json.loads(
        subprocess.check_output(["docker", "network", "inspect", network], text=True)
    )[0]["Internal"]
    for value in inspected:
        assert set(value["NetworkSettings"]["Networks"]) == {network}
        assert not value["HostConfig"]["PortBindings"]
        assert value["State"]["Running"]
        assert value["State"].get("Health", {}).get("Status", "healthy") == "healthy"
    return {
        "runtime_internal_network_only": True,
        "no_published_memory_ports": True,
        "services": [
            {key: row[key] for key in ("Service", "State", "Health")} for row in rows
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["baseline", "verify"])
    args = parser.parse_args()
    if args.phase == "baseline":
        value = preview.deployed_snapshot()
        assert not value["active_tasks"], "Wait for current preview tasks to finish"
        BASELINE.write_text(json.dumps(value, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "schema_revision": value["schema_revision"],
                    "configuration_tables_checked": len(value["configuration"]),
                    "active_tasks": value["active_tasks"],
                }
            )
        )
        return
    raw = subprocess.check_output(
        preview.COMPOSE + ["ps", "--format", "json"], text=True
    )
    running = [json.loads(line)["Service"] for line in raw.splitlines()]
    preview.SERVICES += [
        s
        for s in ("knowledge_worker", "valuation", "forward_validation")
        if s in running
    ]
    assert "knowledge_worker" in preview.SERVICES
    from tauric.config import SCHEMA_REVISION

    # The owner's concurrent live-trading chat created one portfolio and its
    # policy where the baseline had no rows. Preserve and report those additions.
    preview.verify(
        BASELINE,
        REPORT,
        SCHEMA_REVISION,
        initialized_tables=("capital_scope_versions", "portfolio_policy_versions"),
    )
    report = json.loads(REPORT.read_text())
    report["memory"] = preview.container_json("api", RUNTIME_CHECK)
    report["local_memory_network"] = memory_network_check()
    report["scope"] = (
        "Local preview only. Owner memory stays local; no broker order, new chat reply or quality certification was requested by this verifier."
    )
    report["concurrent_work"] = {
        "title": "Complete live auto trading",
        "note": "A separate owner-authorized chat upgraded trading and added a portfolio/policy during memory verification. The two additions are reported explicitly; the 14 other configuration tables match the original baseline.",
    }
    REPORT.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "memory": report["memory"],
                "local_memory_network": report["local_memory_network"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
