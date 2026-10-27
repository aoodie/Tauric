"""Persist bounded health evidence, text metrics and change-only host alerts."""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import server_soak
from release_publish import save

UTC = timezone.utc
APPLICATION = """import json
from sqlalchemy import select,func,text
from sqlalchemy.orm import Session
from tauric.database import engine
from tauric.models import WorkerStatus,AnalysisJob,now
with Session(engine()) as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 workers={r.id:(now()-r.heartbeat_at).total_seconds() for r in db.scalars(select(WorkerStatus))}
 earliest=db.scalar(select(func.min(AnalysisJob.created_at)).where(AnalysisJob.status=='queued'))
 tasks=d.scalar(text("SELECT count(*) FROM agent_tasks WHERE parent_id IS NULL AND status='requires_owner'"))
 print(json.dumps({'worker_age_seconds':{n:workers.get(n) for n in ('native','strategy_discovery','lab_lean','lab_qlib','chat','governance','agent_tasks','portfolio_research','candidate_research','strategy_scheduler')},'oldest_native_queue_seconds':(now()-earliest).total_seconds() if earliest else None,'project_goals_requiring_owner':tasks}))
"""


def alerts(sample, expected_services, previous=None):
    result = []
    if sample.get("observation_error"):
        result.append({"key": "host_observation", "reason": "Host observation failed", "action": "Inspect Docker and monitoring; the previous healthy sample is not current"})
    states = sample["services"]
    for name in sorted(expected_services):
        row = states.get(name)
        if not row or not row["running"] or row["health"] != "healthy" or row["oom_killed"]:
            result.append({"key": "service:" + name, "reason": "Service unavailable, unhealthy or OOM-killed", "action": "Inspect the exact service and its retained failure evidence before recovery"})
        elif previous and name in previous["services"]:
            old = previous["services"][name]
            if row["container_id"] != old["container_id"] or row["restart_count"] != old["restart_count"]:
                result.append({"key": "restart:" + name, "reason": "Service identity or restart count changed", "action": "Reconcile the change with the recorded release or recovery"})
    unexpected = {"broker_execution", "valuation", "forward_validation", "autonomous_paper"} - set(expected_services)
    for name in sorted(unexpected):
        if states.get(name, {}).get("running"):
            result.append({"key": "financial_service:" + name, "reason": "A financial service outside the recorded scope is running", "action": "Inspect owner authority and the deployment service scope immediately"})
    memory = sample.get("host_memory")
    if memory is None:
        result.append({"key": "memory_unavailable", "reason": "Host memory measurement unavailable", "action": "Restore host telemetry before accepting workload headroom"})
    elif memory["physical_available_bytes"] < max(512 * 1024**2, memory["physical_total_bytes"] // 10):
        result.append({"key": "memory_pressure", "reason": "Available host RAM below the observed reserve", "action": "Inspect measured workloads and OOM evidence before adjusting limits"})
    if sample["available_disk_bytes"] < 5 * 1024**3:
        result.append({"key": "disk_pressure", "reason": "Available disk below 5 GiB", "action": "Verify recovery archives and retention before reclaiming space"})
    if "application" in sample:
        application = sample["application"]
        if application is None:
            result.append({"key": "application_probe", "reason": "Read-only worker and queue probe failed", "action": "Inspect database/API readiness without submitting new work"})
        else:
            for name, age in application["worker_age_seconds"].items():
                if age is None or not 0 <= age <= 120:
                    result.append({"key": "worker:" + name, "reason": "Required research worker heartbeat is missing or stale", "action": "Reconcile original jobs and the exact worker before bounded recovery"})
            if (application["oldest_native_queue_seconds"] or 0) > 600:
                result.append({"key": "native_queue", "reason": "Native research has queued work older than ten minutes", "action": "Inspect the original queued jobs and worker allowance before retrying"})
            if application.get("project_goals_requiring_owner", 0):
                result.append({"key": "project_goal_needs_owner", "reason": "Project work requires owner attention", "action": "Inspect the original goal; do not reset its budget or undo a pause"})
    if sample.get("backup_age_seconds") is None or sample["backup_age_seconds"] > 36 * 3600:
        result.append({"key": "backup_stale", "reason": "No verified local checkpoint in the last 36 hours", "action": "Inspect the original checkpoint and deferred maintenance state"})
    if sample.get("offhost_backup_age_seconds") is None or sample["offhost_backup_age_seconds"] > 36 * 3600:
        result.append({"key": "offhost_backup_stale", "reason": "No verified independent checkpoint in the last 36 hours", "action": "Restore the independent backup pull and authenticate the existing archives"})
    return result


def text_metrics(sample, issues):
    memory = sample.get("host_memory") or {}
    lines = ["# Tauric host observations; no financial authority"]
    for key, value in memory.items():
        lines.append(f"tauric_host_{key} {value}")
    lines.extend([f"tauric_disk_available_bytes {sample['available_disk_bytes']}", f"tauric_active_alerts {len(issues)}"])
    for name, row in sorted(sample["services"].items()):
        # Compose validates service names; JSON escaping also protects metric labels.
        label = json.dumps(name)
        for metric, value in (("running", int(row["running"])), ("healthy", int(row["health"] == "healthy")), ("restarts", row["restart_count"]), ("memory_limit_bytes", row["memory_limit_bytes"])):
            lines.append(f"tauric_service_{metric}{{service={label}}} {value}")
    return "\n".join(lines) + "\n"


def observe(state, project, output, expected):
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = output / "latest.json"
    try:
        old = json.loads(path.read_text()) if path.exists() else None
    except (OSError, ValueError):
        old = None
    if not isinstance(old, dict) or not isinstance(old.get("observation"), dict) or not isinstance(old.get("alerts"), list):
        old = None
    try:
        sample = server_soak.observe(state, project)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        sample = {"services": {}, "host_memory": None, "available_disk_bytes": 0,
                  "observation_error": "host_probe_failed"}
    ages, offhost = [], []
    for checkpoint in Path("/srv/private-backups/scheduled").glob("scheduled-????-??-??.json"):
        try:
            saved = json.loads(checkpoint.read_text())
            if saved.get("phase") == "verified":
                age = (datetime.now(UTC) - datetime.fromisoformat(saved["verified_at"])).total_seconds()
                if age >= 0:
                    ages.append(age)
                    if saved.get("offhost_copy_verified"):
                        offhost.append(age)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    sample.update(backup_age_seconds=min(ages) if ages else None,
                  offhost_backup_age_seconds=min(offhost) if offhost else None)
    try:
        probe = subprocess.run(["docker", "exec", project + "-api-1", "python", "-c", APPLICATION], capture_output=True, check=False, timeout=20)
        sample["application"] = json.loads(probe.stdout) if probe.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        sample["application"] = None
    issues = alerts(sample, expected, old["observation"] if old else None)
    report = {"observed_at": datetime.now(UTC).isoformat(), "expected_services": sorted(expected), "alerts": issues, "observation": sample}
    save(path, report)
    metrics = output / "tauric.prom"
    temporary = metrics.with_suffix(".pending")
    temporary.write_text(text_metrics(sample, issues))
    temporary.chmod(0o600)
    temporary.replace(metrics)
    keys = {a["key"] for a in issues}
    old_keys = {a["key"] for a in old["alerts"]} if old else set()
    if keys != old_keys:
        print(json.dumps({"event": "tauric_health_changed", "alerts": issues, "resolved": sorted(old_keys - keys)}), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--project", default="tauric_server")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--services", nargs="+", required=True)
    args = parser.parse_args()
    observe(args.state_dir.resolve(), args.project, args.output.resolve(), set(args.services))


if __name__ == "__main__":
    main()
