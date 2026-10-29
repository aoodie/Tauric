"""Install fixed observation and resumable backup timers; never start financial services."""

import argparse
import os
import subprocess
from pathlib import Path

SERVICES = "db api web openclaw worker chat_worker governance_worker agent_tasks portfolio_research candidate_research scanner strategy_scheduler"


def units():
    common = "[Unit]\nAfter=docker.service\nRequires=docker.service\n[Service]\nType=oneshot\nUser=root\nUMask=0077\nNoNewPrivileges=true\n"
    return {
        "tauric-operations-watch.service": common + "ExecStart=/usr/bin/python3 /srv/tauric/current/scripts/operations_watch.py --state-dir /srv/tauric-private --output /srv/tauric-private/telemetry --services " + SERVICES + "\nTimeoutStartSec=45\n",
        "tauric-operations-watch.timer": "[Unit]\nDescription=Tauric health observations\n[Timer]\nOnBootSec=60\nOnUnitActiveSec=60\nUnit=tauric-operations-watch.service\n[Install]\nWantedBy=timers.target\n",
        "tauric-maintenance-backup.service": common + "ExecStart=/usr/bin/python3 /srv/tauric/current/scripts/maintenance_backup.py --state-dir /srv/tauric-private --output /srv/private-backups/scheduled\nTimeoutStartSec=3600\nNice=10\n",
        "tauric-maintenance-backup.timer": "[Unit]\nDescription=Tauric daily checkpoint with bounded safe deferral\n[Timer]\nOnCalendar=*-*-* 03..08:10,40:00 UTC\nPersistent=true\nUnit=tauric-maintenance-backup.service\n[Install]\nWantedBy=timers.target\n",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if not args.install:
        print("\n".join(units()))
        return
    if os.geteuid() != 0:
        raise SystemExit("Root is required to install fixed operational units.")
    for name, content in units().items():
        path = Path("/etc/systemd/system") / name
        path.write_text(content)
        path.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "tauric-operations-watch.timer", "tauric-maintenance-backup.timer"], check=True)


if __name__ == "__main__":
    main()
