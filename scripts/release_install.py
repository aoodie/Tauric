"""Fixed, journaled installation of a completely checked source-bound release."""

import hashlib
import importlib.util
import json
import os
import re
import subprocess
from pathlib import Path

from release_publish import save

PROTECTED_TABLES = {
    "owners",
    "setting_definitions",
    "setting_heads",
    "paper_accounts",
    "paper_deployments",
    "trading_records",
    "broker_order_intents",
    "broker_order_events",
    "broker_position_cursors",
    "broker_close_requests",
    "broker_review_jobs",
    "broker_review_decisions",
    "broker_routes",
    "broker_route_workflows",
    "broker_route_events",
    "qualification_protocols",
    "qualification_evaluations",
    "qualification_admissions",
    "qualification_reports",
    "strategy_schedules",
    "autonomy_workflows",
    "autonomy_events",
    "portfolio_research_schedules",
    "chat_portfolio_bindings",
    "intelligence_watchlists",
}
HISTORY = """import hashlib,json
from sqlalchemy import text
from tauric.database import engine
with engine().connect() as db:
 db.execute(text("SET TRANSACTION READ ONLY"))
 print(json.dumps({i:hashlib.sha256(json.dumps(json.loads(v),sort_keys=True,separators=(",",":")).encode()).hexdigest() for i,v in db.execute(text("SELECT id,to_jsonb(t)::text FROM trading_readiness_records t"))},sort_keys=True))
"""
SIGNATURE = "import json; from tauric.live_trading import signature; print(json.dumps(signature(),sort_keys=True))"


class EngineeringBusy(Exception):
    """An existing bounded execution must finish before its executor is upgraded."""


def write_configuration(path, values):
    """Replace an existing private configuration atomically, including after interruption."""
    if path.is_symlink():
        raise ValueError("Release configuration must not be a symlink.")
    temporary = path.with_name(path.name + ".release.pending")
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as output:
        output.write("".join(f"{k}={v}\n" for k, v in values.items()))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def engineering_idle(state):
    root = state / "secrets/engineering/spool"
    return not any(
        not (root / "receipts" / p.name).exists()
        for p in (root / "requests").glob("*.json")
    )


def verify_active_ingress(active_release, manifest):
    """Local ingress edits may carry another project's routes; never overwrite them."""
    files = manifest.get("files", {})
    for name in (
        "deploy/Caddyfile.https", "deploy/Caddyfile.public-ip",
        "deploy/compose.https.yml", "deploy/compose.public-ip.yml",
    ):
        path = active_release / name
        expected = files.get(name)
        if (
            not isinstance(expected, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected)
            or path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
        ):
            raise ValueError(
                "Active ingress differs from its release manifest. Reconcile and preserve shared routes before installation."
            )


def resume_services(server, compose, services):
    """Restore the recorded service set without activating optional profiles."""
    if not isinstance(services, list) or not services or "db" not in services or any(
        not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", name)
        for name in services
    ):
        raise ValueError("A recorded service set including the database is required.")
    server.run(
        compose + ["up", "-d", "--no-deps", "--wait", "--wait-timeout", "600", *services],
        stdout=subprocess.PIPE,
    )


def recover_before_migration(active_release, state, project, progress):
    """Resume the unchanged services if preparation stops before any migration."""
    if progress.get("phase") not in {
        "freeze_requested",
        "frozen",
        "backed_up",
        "rehearsed",
    }:
        return False
    server = module(
        "release_preparation_recovery", active_release / "scripts/server.py"
    )
    values = server.read_env(state / "server.env")
    if {k: values[k] for k in server.IMAGE_KEYS} != progress["old_images"]:
        raise ValueError(
            "Preparation recovery cannot restore a changed image configuration."
        )
    installer = module(
        "release_previous_engineering",
        active_release / "scripts/engineering_install.py",
    )
    installer.install(active_release, state)
    compose = server.command(state, project) + ["--profile", "*"]
    resume_services(server, compose, progress["previous_services"])
    return True


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def protected_tables(snapshot):
    result = PROTECTED_TABLES | {
        k for k in snapshot["tables"] if k.endswith("_versions")
    }
    if not result <= snapshot["tables"].keys():
        raise ValueError("Required financial authority tables are missing.")
    return sorted(result)


def check_preserved(before, after, names):
    if not all(before["tables"].get(k) == after["tables"].get(k) for k in names):
        raise ValueError(
            "Protected financial or owner settings changed during release installation."
        )


def installed_services(server, compose, revision, images):
    ids = (
        server.run(compose + ["ps", "-q"], stdout=subprocess.PIPE)
        .stdout.decode()
        .split()
    )
    containers = json.loads(subprocess.check_output(["docker", "inspect", *ids]))
    expected = {
        "api": images["TAURIC_API_IMAGE"],
        "web": images["TAURIC_WEB_IMAGE"],
        "openclaw": images["TAURIC_OPENCLAW_IMAGE"],
        "worker": images["TAURIC_WORKER_IMAGE"],
    }
    services = []
    for container in containers:
        name = container["Config"]["Labels"]["com.docker.compose.service"]
        if (
            container["State"]["Status"] != "running"
            or container["State"].get("Health", {}).get("Status") != "healthy"
        ):
            raise ValueError("Installed services are not all healthy.")
        if name != "db":
            reference = expected.get(name, images["TAURIC_API_IMAGE"])
            inspected = json.loads(
                subprocess.check_output(["docker", "image", "inspect", reference])
            )[0]
            if (
                inspected["Id"] != container["Image"]
                or inspected["Config"]["Labels"]["org.opencontainers.image.revision"]
                != revision
                or reference not in inspected["RepoDigests"]
            ):
                raise ValueError(
                    "A running service differs from the checked release image."
                )
        services.append(
            {"service": name, "healthy": True, "image_id": container["Image"]}
        )
    return sorted(services, key=lambda item: item["service"])


def loaded_source(server, compose, tree):
    code = """import hashlib,json
from pathlib import Path
def blob(data): return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\\0"+data).hexdigest()
root=Path('/app/tauric')
print(json.dumps({'backend/tauric/'+p.relative_to(root).as_posix():blob(p.read_bytes()) for p in root.rglob('*') if p.is_file() and p.suffix in {'.py','.txt'}}))
"""
    actual = json.loads(
        server.run(
            compose + ["exec", "-T", "api", "python", "-c", code],
            stdout=subprocess.PIPE,
        ).stdout
    )
    expected = {
        v["path"]: v["sha"]
        for v in tree
        if v["type"] == "blob"
        and v["path"].startswith("backend/tauric/")
        and Path(v["path"]).suffix in {".py", ".txt"}
    }
    if actual != expected:
        raise ValueError("Loaded API source differs from the published commit.")
    node = """const fs=require('fs'),c=require('crypto');const blob=p=>{const d=fs.readFileSync(p);return c.createHash('sha1').update(Buffer.from('blob '+d.length+'\\0')).update(d).digest('hex')};console.log(JSON.stringify({plugin:blob('/app/plugin/src/index.ts'),instructions:blob('/state/chat-workspace/AGENTS.md'),image_instructions:blob('/app/chat_instructions.txt')}));"""
    claw = json.loads(
        server.run(
            compose + ["exec", "-T", "openclaw", "node", "-e", node],
            stdout=subprocess.PIPE,
        ).stdout
    )
    expected_tree = {v["path"]: v["sha"] for v in tree if v["type"] == "blob"}
    if (
        claw["plugin"] != expected_tree["openclaw/plugin/src/index.ts"]
        or claw["instructions"] != expected_tree["backend/tauric/chat_instructions.txt"]
        or claw["image_instructions"] != claw["instructions"]
    ):
        raise ValueError("Loaded OpenClaw source differs from the published commit.")
    return {
        "python_and_instruction_files": len(actual),
        "plugin_and_saved_instructions_match": True,
    }


def install(
    *,
    active_release,
    candidate,
    state,
    project,
    revision,
    journal,
    journal_path,
    authorize,
    tree,
):
    """Reentry observes the original installation; migrations are not blindly replayed."""
    old = module("release_previous_server", active_release / "scripts/server.py")
    new = module("release_candidate_server", candidate / "scripts/server.py")
    old_compose = old.command(state, project) + ["--profile", "*"]
    compose = new.command(state, project) + ["--profile", "*"]
    values = old.read_env(state / "server.env")
    images = new.read_env(candidate / "images.env")
    new.validate_images(images)
    progress = journal.setdefault("installation", {})
    previous_manifest = json.loads(
        (active_release / "release-manifest.json").read_text()
    )
    candidate_manifest = json.loads((candidate / "release-manifest.json").read_text())
    if candidate_manifest["revision"] != revision:
        raise ValueError("Candidate installation revision mismatch.")
    if not progress:
        verify_active_ingress(active_release, previous_manifest)
        authorize()
        if not engineering_idle(state):
            raise EngineeringBusy()
        if {k: values[k] for k in old.IMAGE_KEYS} != previous_manifest["images"]:
            raise ValueError("The installed base changed before this release.")
        active = (
            old.run(
                old_compose + ["ps", "--status", "running", "--services"],
                stdout=subprocess.PIPE,
            )
            .stdout.decode()
            .split()
        )
        if "db" not in active:
            raise ValueError(
                "Production database is not available for a recovery backup."
            )
        progress.update(
            {
                "previous_services": active,
                "old_images": previous_manifest["images"],
                "non_image_checksum": hashlib.sha256(
                    json.dumps(
                        {k: v for k, v in values.items() if k not in old.IMAGE_KEYS},
                        sort_keys=True,
                    ).encode()
                ).hexdigest(),
                "phase": "freeze_requested",
            }
        )
        save(journal_path, journal)
    if progress["phase"] == "freeze_requested":
        authorize()
        old.run(
            old_compose
            + ["stop", *[n for n in progress["previous_services"] if n != "db"]],
            stdout=subprocess.PIPE,
        )
        # A chat that drained during shutdown may have submitted one last job.
        # Resume unchanged services and wait for that original execution.
        if not engineering_idle(state):
            resume_services(old, old_compose, progress["previous_services"])
            progress.clear()
            save(journal_path, journal)
            raise EngineeringBusy()
        before = old.snapshot(old_compose)
        private = Path(values["TAURIC_SECRETS_DIR"])
        names = [
            *old.PRIVATE_FILES,
            "engineering/private.pem",
            "engineering/public/public.pem",
            "operations/github-app.pem",
            "operations/github-app.json",
            "operations/release-private.pem",
            "operations/spool/public/public.pem",
        ]
        preserved = {n: old.checksum(private / n) for n in names}
        preserved.update(
            {
                str(p.relative_to(private)): old.checksum(p)
                for p in (private / "engineering/spool/receipts").glob("*")
                if p.is_file()
            }
        )
        progress.update(
            {
                "before": before,
                "protected": protected_tables(before),
                "private_hashes": preserved,
                "historical": json.loads(
                    old.run(
                        old_compose
                        + [
                            "run",
                            "--rm",
                            "--no-deps",
                            "-T",
                            "api",
                            "python",
                            "-c",
                            HISTORY,
                        ],
                        stdout=subprocess.PIPE,
                    ).stdout
                ),
                "execution_signature": json.loads(
                    old.run(
                        old_compose
                        + [
                            "run",
                            "--rm",
                            "--no-deps",
                            "-T",
                            "api",
                            "python",
                            "-c",
                            SIGNATURE,
                        ],
                        stdout=subprocess.PIPE,
                    ).stdout
                ),
                "phase": "frozen",
            }
        )
        save(journal_path, journal)
    if progress["phase"] == "frozen":
        # Backup excludes its own output: keep it outside the copied secrets tree.
        backup = Path("/srv/private-backups") / (
            "openclaw-release-" + journal["id"] + ".tar.gz"
        )
        if backup.exists():
            raise ValueError(
                "Interrupted backup outcome needs reconciliation before installation."
            )
        result = old.backup(state, project, backup)
        progress.update(
            {"backup": str(backup), "backup_result": result, "phase": "backed_up"}
        )
        save(journal_path, journal)
    backup = Path(progress["backup"])
    if old.checksum(backup) != progress["backup_result"]["sha256"]:
        raise ValueError("The original recovery backup changed.")
    if progress["phase"] == "backed_up":
        restore_state = Path("/srv/tauric-release-rehearsals") / journal["id"]
        restore_project = "tauric_release_" + journal["id"].replace("-", "")[:24]
        if restore_state.exists():
            raise ValueError(
                "Interrupted restoration needs reconciliation; the backup remains intact."
            )
        restored = old.restore_check(restore_state, restore_project, backup)
        isolated = new.command(restore_state, restore_project) + ["--profile", "*"]
        restored_values = new.read_env(restore_state / "server.env")
        restored_values.update(images)
        write_configuration(restore_state / "server.env", restored_values)
        try:
            new.run(
                isolated + ["run", "--rm", "--no-deps", "-T", "migrate"],
                stdout=subprocess.PIPE,
            )
            migrated = new.snapshot(isolated)
            check_preserved(progress["before"], migrated, progress["protected"])
            candidate_signature = json.loads(
                new.run(
                    isolated
                    + [
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "api",
                        "python",
                        "-c",
                        SIGNATURE,
                    ],
                    stdout=subprocess.PIPE,
                ).stdout
            )
            if candidate_signature != progress["execution_signature"]:
                raise ValueError(
                    "The candidate changes qualified execution identity; separate trusted qualification is required."
                )
            if migrated["schema"] != migrated["expected_schema"]:
                raise ValueError(
                    "Candidate restoration did not reach its required schema."
                )
        finally:
            new.run(isolated + ["stop", "db"], stdout=subprocess.PIPE)
        progress.update(
            {
                "restore": restored,
                "expected_schema": migrated["schema"],
                "phase": "rehearsed",
            }
        )
        save(journal_path, journal)
    if progress["phase"] == "rehearsed":
        authorize()
        installer = module(
            "release_engineering_installer",
            candidate / "scripts/engineering_install.py",
        )
        progress["engineering"] = installer.install(candidate, state)
        # Write-ahead phase prevents blind replay if migration loses its response.
        progress["phase"] = "migration_requested"
        save(journal_path, journal)
        values.update(images)
        write_configuration(state / "server.env", values)
        new.run(
            compose + ["run", "--rm", "--no-deps", "-T", "migrate"],
            stdout=subprocess.PIPE,
        )
    if progress["phase"] == "migration_requested":
        current_values = new.read_env(state / "server.env")
        if {k: current_values[k] for k in new.IMAGE_KEYS} != images:
            raise ValueError("Interrupted image configuration requires reconciliation.")
        migrated = new.snapshot(compose)
        if migrated["schema"] != progress["expected_schema"]:
            raise ValueError("Migration outcome is uncertain; it was not repeated.")
        check_preserved(progress["before"], migrated, progress["protected"])
        progress["phase"] = "startup_requested"
        progress["startup_attempts"] = 0
        save(journal_path, journal)
    if progress["phase"] == "startup_requested":
        if progress["startup_attempts"] >= 2:
            # A lost second response can still have succeeded. Observe the exact
            # original images before deciding that bounded recovery exhausted.
            ready = installed_services(new, compose, revision, images)
            if {s["service"] for s in ready} != set(progress["previous_services"]):
                raise ValueError(
                    "Bounded installation recovery exhausted; inspect the original release."
                )
        else:
            progress["startup_attempts"] += 1
            save(journal_path, journal)
            new.validate(state, project)
            resume_services(new, compose, progress["previous_services"])
        progress["phase"] = "verify_requested"
        save(journal_path, journal)
    if progress["phase"] == "verify_requested":
        after = new.snapshot(compose)
        # The strict authority/settings snapshot was verified after migration
        # and before workers started. Healthy authorized workflows may now append
        # order/evidence rows; those appends must not invalidate that check.
        if after["schema"] != progress["expected_schema"]:
            raise ValueError("Installed schema differs from the rehearsed release.")
        historical = json.loads(
            new.run(
                compose + ["exec", "-T", "api", "python", "-c", HISTORY],
                stdout=subprocess.PIPE,
            ).stdout
        )
        if not all(historical.get(k) == v for k, v in progress["historical"].items()):
            raise ValueError(
                "Historical financial readiness evidence changed during installation."
            )
        private = Path(new.read_env(state / "server.env")["TAURIC_SECRETS_DIR"])
        if not all(
            new.checksum(private / k) == v
            for k, v in progress["private_hashes"].items()
        ):
            raise ValueError(
                "Private configuration or original execution receipts changed."
            )
        actual_values = new.read_env(state / "server.env")
        if (
            hashlib.sha256(
                json.dumps(
                    {k: v for k, v in actual_values.items() if k not in new.IMAGE_KEYS},
                    sort_keys=True,
                ).encode()
            ).hexdigest()
            != progress["non_image_checksum"]
        ):
            raise ValueError("Non-image configuration changed during installation.")
        signature = json.loads(
            new.run(
                compose + ["exec", "-T", "api", "python", "-c", SIGNATURE],
                stdout=subprocess.PIPE,
            ).stdout
        )
        if signature != progress["execution_signature"]:
            raise ValueError(
                "The qualified execution identity changed; separate trusted recovery is required."
            )
        services = installed_services(new, compose, revision, images)
        if {s["service"] for s in services} != set(progress["previous_services"]):
            raise ValueError(
                "The complete previous production service set was not recovered."
            )
        loaded = loaded_source(new, compose, tree)
        subprocess.run(
            ["systemctl", "is-active", "--quiet", "tauric-engineering.service"],
            check=True,
        )
        progress.update(
            {
                "phase": "verified",
                "result": {
                    "deployment_verified": True,
                    "revision": revision,
                    "backup_sha256": progress["backup_result"]["sha256"],
                    "backup_restoration_verified": True,
                    "protected_tables_preserved_before_workers_started": len(
                        progress["protected"]
                    ),
                    "historical_rows_preserved": len(progress["historical"]),
                    "private_configuration_preserved": True,
                    "services": services,
                    "loaded_source": loaded,
                },
            }
        )
        save(journal_path, journal)
    if progress["phase"] != "verified":
        raise ValueError(
            "Installation did not reach independently verified completion."
        )
    return progress["result"]
