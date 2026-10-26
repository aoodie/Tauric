"""Owner-operated installation of a fully checked release without financial authority.

The autonomous bridge never calls this entry point. It requires root, a private
immutable operator intent, a complete independently fetched GitHub release run,
empty financial-authority/evidence tables and the exact recorded service set.
No qualification record, financial policy, route or pause is created or changed.
"""

import argparse
import fcntl
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from uuid import UUID

import release_bridge_install
import release_install
import release_workflow
from release_identity import GitHubApp
from release_publish import save

STATE = Path("/srv/tauric-private")
PROJECT = "tauric_server"
QUANT_UNITS = ("tauric-quant-lean.service", "tauric-quant-qlib.service")


def active(unit):
    return subprocess.run(["systemctl", "is-active", "--quiet", unit], capture_output=True, check=False).returncode == 0


def install(intent_path):
    if os.geteuid() != 0 or intent_path.is_symlink() or intent_path.stat().st_uid != 0 or intent_path.stat().st_mode & 0o077 or not intent_path.is_relative_to(STATE):
        raise ValueError("A root-operated private release intent is required.")
    intent = json.loads(intent_path.read_text())
    if intent.get("kind") != "owner_requested_research_release":
        raise ValueError("This installer requires an explicit research-only operator intent.")
    if any(not re.fullmatch(r"[a-f0-9]{40}", intent[key]) for key in ("revision", "base_revision")) or str(UUID(intent["id"])) != intent["id"]:
        raise ValueError("Exact source revisions and a stable release identity are required.")
    candidate = Path("/srv/tauric/releases/openclaw-owner-" + intent["revision"][:12] + "/tauric-release")
    base = Path("/srv/tauric/releases/openclaw-owner-" + intent["base_revision"][:12] + "/tauric-release")
    for root, revision in ((candidate, intent["revision"]), (base, intent["base_revision"])):
        manifest = json.loads((root / "release-manifest.json").read_text())
        if manifest["revision"] != revision or any(hashlib.sha256((root / n).read_bytes()).hexdigest() != d for n, d in manifest["files"].items()):
            raise ValueError("Release source/manifest verification failed.")
    client = GitHubApp(STATE / "secrets/operations")
    run = client.repo("/actions/runs/" + str(intent["release_run_id"]))
    if not release_workflow.completed_release(client, run, intent["revision"], intent["branch"]):
        raise ValueError("The complete checked release has not finished.")
    tree = client.repo("/git/trees/" + intent["revision"] + "?recursive=1")
    if tree.get("truncated"):
        raise ValueError("The complete source tree is required.")
    journal_path = STATE / ("research-release-" + intent["id"] + ".json")
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {"id": intent["id"], "intent": intent}
    if journal["intent"] != intent:
        raise ValueError("The original release identity belongs to different inputs.")

    def authorize():
        if json.loads(intent_path.read_text()) != intent:
            raise ValueError("Owner-operated release intent changed.")

    if not journal.get("installation"):
        old = release_install.module("trusted_base_server", base / "scripts/server.py")
        compose = release_install.compose_command(old, STATE, PROJECT) + ["--profile", "*"]
        services = old.run(compose + ["ps", "--status", "running", "--services"], stdout=subprocess.PIPE).stdout.decode().split()
        if set(services) != set(intent["services"]):
            raise ValueError("The recorded running service scope changed.")
        jobs = subprocess.run(["docker", "exec", PROJECT + "-api-1", "python", "-c", "from sqlalchemy import text;from tauric.database import engine;\nwith engine().connect() as d:\n d.execute(text('SET TRANSACTION READ ONLY'));print(d.scalar(text(\"SELECT count(*) FROM quant_records WHERE kind='quant_job' AND payload->>'status' IN ('queued','running')\")))"], check=True, capture_output=True, text=True)
        if jobs.stdout.strip() != "0" or not release_install.engineering_idle(STATE):
            raise release_install.EngineeringBusy()
        journal["quant_units"] = [name for name in QUANT_UNITS if active(name)]
        save(journal_path, journal)
    if journal.get('installation', {}).get('phase') != 'verified':
        for unit in journal["quant_units"]:
            subprocess.run(["systemctl", "stop", unit], check=True, capture_output=True)
    subprocess.run(["systemctl", "stop", "tauric-release-bridge.service"], check=True, capture_output=True)
    proof = {"kind": "trusted_research_only", "revision": intent["revision"], "before_signature": intent["before_signature"], "after_signature": intent["after_signature"], "release_run_id": intent["release_run_id"], "checked_release_verified": True}
    try:
        result = release_install.install(active_release=base, candidate=candidate, state=STATE, project=PROJECT, revision=intent["revision"], journal=journal, journal_path=journal_path, authorize=authorize, tree=tree["tree"], trusted_research_transition=proof)
    except Exception:
        phase = journal.get("installation", {}).get("phase")
        if phase in {"freeze_requested", "frozen", "backed_up", "rehearsed"}:
            journal["previous_services_recovered"] = release_install.recover_before_migration(base, STATE, PROJECT, journal["installation"])
            save(journal_path, journal)
        if phase is None or journal.get("previous_services_recovered"):
            for unit in journal["quant_units"]:
                subprocess.run(["systemctl", "start", unit], check=True, capture_output=True)
            subprocess.run(["systemctl", "start", "tauric-release-bridge.service"], check=True, capture_output=True)
        raise
    bridge = release_bridge_install.install(candidate)
    pointer = Path("/srv/tauric/current")
    if pointer.resolve() != candidate:
        if not pointer.is_symlink() or pointer.resolve() != base:
            raise ValueError("The original active release pointer changed.")
        temporary = pointer.with_name("current.research.pending")
        temporary.symlink_to(candidate)
        temporary.replace(pointer)
    for unit in journal["quant_units"]:
        subprocess.run(["systemctl", "start", unit], check=True, capture_output=True)
    result.update(research_only=True, strategy_qualification_granted=False, financial_services_activated=False, release_bridge_revision=bridge["source_revision"])
    journal["final_result"] = result
    save(journal_path, journal)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, required=True)
    args = parser.parse_args()
    with (STATE / "maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(install(args.intent.absolute()), sort_keys=True))


if __name__ == "__main__":
    main()
