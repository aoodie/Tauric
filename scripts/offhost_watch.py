"""Independent workstation deadman, changed-state notifications and encrypted backup pull."""

import argparse
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from pull_recovery_backups import private_directory, private_save, pull, ssh

UTC = timezone.utc
PROBE = '''import pathlib,json,datetime,subprocess
p=pathlib.Path("/srv/tauric-private/telemetry/latest.json")
d=json.loads(p.read_text())
age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(d["observed_at"])).total_seconds()
keys=[x["key"] for x in d["alerts"]]
code="from sqlalchemy import text;from tauric.database import engine;\\nwith engine().connect() as d:\\n d.execute(text('SET TRANSACTION READ ONLY'));print(d.scalar(text(\\"SELECT count(*) FROM agent_tasks WHERE parent_id IS NULL AND status='requires_owner'\\")))"
r=subprocess.run(["docker","exec","tauric_server-api-1","python","-c",code],capture_output=True,text=True,timeout=15)
if r.returncode or not r.stdout.strip().isdigit():keys.append("project_task_probe_unavailable")
elif int(r.stdout.strip()):keys.append("project_goal_needs_owner")
print(json.dumps({"age_seconds":age,"keys":keys}))
'''


def transition(previous, current):
    return set(previous) != set(current)


def notify(keys):
    body = ("Tauric needs attention: " + ", ".join(keys[:8])) if keys else "Tauric monitoring recovered."
    subprocess.run(["osascript", "-e", "display notification " + json.dumps(body) + ' with title "Tauric operations"'],
                   check=True, capture_output=True, timeout=10)


def observe(config, output):
    private_directory(output)
    path = output / "offhost-status.json"
    try:
        previous = json.loads(path.read_text()) if path.exists() else {"keys": []}
    except (OSError, ValueError):
        previous = {"keys": []}
    keys = []
    try:
        probe = json.loads(ssh(config, PROBE))
        keys = [k for k in probe["keys"] if isinstance(k, str) and re.fullmatch(r"[a-zA-Z0-9_:.-]{1,100}", k)]
        if not 0 <= probe["age_seconds"] <= 180:
            keys.append("host_monitor_stale")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        keys.append("host_unreachable_or_monitor_unavailable")
    try:
        checkpoint = pull(config)
        if not checkpoint["verified_checkpoints"]:
            keys.append("no_verified_backups")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError):
        keys.append("offhost_backup_failed")
    keys = sorted(set(keys))
    changed = transition(previous.get("keys", []), keys)
    report = {"observed_at": datetime.now(UTC).isoformat(), "keys": keys,
              "delivery": previous.get("delivery", "not_required"),
              "requires_workstation_online": True}
    if changed or previous.get("delivery") == "failed":
        try:
            notify(keys)
            report["delivery"] = "submitted_to_macos_notifications"
        except (OSError, subprocess.SubprocessError):
            report["delivery"] = "failed"
    private_save(path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.config.is_symlink() or args.config.stat().st_mode & 0o077:
        raise ValueError("Use a private regular observation configuration.")
    print(json.dumps(observe(json.loads(args.config.read_text()), args.output)))


if __name__ == "__main__":
    main()
