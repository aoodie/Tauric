"""Daily full backup with isolated restoration, encrypted retention and no new authority."""

import argparse
import fcntl
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import backup_vault
import server
from release_publish import save

UTC = timezone.utc
QUANT_UNITS = ("tauric-quant-lean.service", "tauric-quant-qlib.service")


def research_archive(destination, root=Path("/srv/tauric-quant")):
    sources = {}
    for name in ("artifacts", "control", "source"):
        for path in sorted((root / name).rglob("*")):
            if path.is_symlink():
                raise ValueError("Research recovery inventory refuses symbolic links.")
            if path.is_file():
                sources[path.relative_to(root).as_posix()] = path
    if not sources:
        raise ValueError("Research recovery inventory is empty.")
    inventory = {name: server.checksum(path) for name, path in sources.items()}
    data = json.dumps({"files": inventory, "runtime_binaries_included": False}, sort_keys=True).encode()
    with destination.open("xb") as output, tarfile.open(fileobj=output, mode="w:gz") as archive:
        destination.chmod(0o600)
        for name, path in sources.items():
            archive.add(path, arcname=name, recursive=False)
        member = tarfile.TarInfo("research-manifest.json")
        member.size, member.mode = len(data), 0o600
        archive.addfile(member, io.BytesIO(data))
    return {"sha256": server.checksum(destination), "files": len(inventory)}


def restore_research(archive, parent):
    with (
        tempfile.TemporaryDirectory(prefix="research-recovery-", dir=parent) as folder,
        tarfile.open(archive) as package,
    ):
        manifest = json.load(package.extractfile("research-manifest.json"))
        names = set()
        for member in package:
            path = Path(member.name)
            if not member.isfile() or path.is_absolute() or ".." in path.parts or member.name in names:
                raise ValueError("Research archive contains an unsafe or duplicate member.")
            names.add(member.name)
            if member.name == "research-manifest.json":
                continue
            expected = manifest["files"].get(member.name)
            if not expected:
                raise ValueError("Unexpected research recovery member.")
            target = Path(folder) / path
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with package.extractfile(member) as source, target.open("xb") as output:
                target.chmod(0o600)
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(block)
                    digest.update(block)
            if digest.hexdigest() != expected:
                raise ValueError("Restored research artifact differs from its recorded hash.")
        if names != set(manifest["files"]) | {"research-manifest.json"}:
            raise ValueError("Research recovery inventory is incomplete.")
    return {"files_restored": len(manifest["files"]), "all_hashes_match": True, "background_services_started": False}


def retain_verified(output, keep=14):
    """Only prune this scheduler's archives after verified encrypted off-host copy."""
    records = [p for p in sorted(output.glob("scheduled-????-??-??.json"), reverse=True) if json.loads(p.read_text()).get("phase") == "verified"]
    removed = []
    for path in records[keep:]:
        value = json.loads(path.read_text())
        if value.get("phase") != "verified" or value.get("offhost_copy_verified") is not True:
            continue
        expected = [output / ("scheduled-" + value["day"] + suffix) for suffix in (".tar.gz.vault", "-research.tar.gz.vault")]
        if any(p.parent != output or not p.is_file() or p.is_symlink() for p in expected):
            continue
        for archive in expected:
            archive.unlink()
        path.unlink()
        removed.append(value["day"])
    return removed


def backup(state, output, day):
    # Root-only maintenance shares its lock with the trusted release installer.
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = output / ("scheduled-" + day + ".json")
    report = json.loads(path.read_text()) if path.exists() else {"day": day, "phase": "new"}
    archive = output / ("scheduled-" + day + ".tar.gz")
    vault = archive.with_suffix(".gz.vault")
    research = output / ("scheduled-" + day + "-research.tar.gz")
    research_vault = research.with_suffix(".gz.vault")
    if report["phase"] == "verified":
        expected = {k: report["encryption"][k] for k in ("plaintext_bytes", "plaintext_sha256")}
        if backup_vault.recover(vault, backup_vault.key_material(state / "secrets/backup-key")) != expected:
            raise ValueError("The original encrypted scheduled backup changed.")
        if backup_vault.recover(research_vault, backup_vault.key_material(state / "secrets/backup-key")) != {k: report["research_encryption"][k] for k in ("plaintext_bytes", "plaintext_sha256")}:
            raise ValueError("The original encrypted research backup changed.")
        return report
    compose = server.command(state, "tauric_server") + ["--profile", "*"]
    if report["phase"] == "new":
        code = "from sqlalchemy import text;from tauric.database import engine;\nwith engine().connect() as d:\n d.execute(text('SET TRANSACTION READ ONLY'));print(d.scalar(text(\"SELECT count(*) FROM quant_records WHERE kind='quant_job' AND payload->>'status' IN ('queued','running')\")))"
        jobs = server.run(compose + ["exec", "-T", "api", "python", "-c", code], stdout=subprocess.PIPE)
        if jobs.stdout.strip() != b"0":
            raise ValueError("Original research jobs must finish before a consistent full backup.")
        units = [name for name in QUANT_UNITS if subprocess.run(["systemctl", "is-active", "--quiet", name], capture_output=True, check=False).returncode == 0]
        report.update(phase="backup_requested", quant_units=units)
        save(path, report)
        for unit in units:
            subprocess.run(["systemctl", "stop", unit], check=True, capture_output=True)
        try:
            def frozen():
                report["research_backup"] = research_archive(research)
                save(path, report)

            report["backup"] = server.backup(state, "tauric_server", archive, frozen_hook=frozen)
            report["phase"] = "backed_up"
            save(path, report)
        finally:
            for unit in units:
                subprocess.run(["systemctl", "start", unit], check=True, capture_output=True)
    if report["phase"] == "backup_requested":
        raise ValueError("Original backup outcome requires reconciliation; it was not repeated.")
    restore_state = state.parent / ("tauric-scheduled-recovery-" + day)
    restore_project = "tauric_scheduled_" + day.replace("-", "")
    if report["phase"] == "backed_up":
        report["phase"] = "restore_requested"
        save(path, report)
        report["restore"] = server.restore_check(restore_state, restore_project, archive)
        report["research_restore"] = restore_research(research, output)
        server.run(server.command(restore_state, restore_project) + ["--profile", "*", "down", "-v"], stdout=subprocess.PIPE)
        report["phase"] = "restored"
        save(path, report)
    if report["phase"] == "restore_requested":
        raise ValueError("Original restoration outcome requires reconciliation; background services remain stopped.")
    if report["phase"] == "restored":
        report["phase"] = "encryption_requested"
        save(path, report)
        report["encryption"] = backup_vault.seal(archive, vault, state / "secrets/backup-key")
        report["research_encryption"] = backup_vault.seal(research, research_vault, state / "secrets/backup-key")
        report["phase"] = "encrypted"
        save(path, report)
    if report["phase"] == "encryption_requested":
        raise ValueError("Original encryption outcome requires reconciliation; plaintext backup retained.")
    if report["phase"] == "encrypted":
        if server.checksum(archive) != report["encryption"]["plaintext_sha256"]:
            raise ValueError("Original backup changed before encrypted cleanup.")
        archive.unlink()
        if server.checksum(research) != report["research_encryption"]["plaintext_sha256"]:
            raise ValueError("Original research backup changed before encrypted cleanup.")
        research.unlink()
        report.update(phase="verified", encrypted_file=str(vault), verified_at=datetime.now(UTC).isoformat(), plaintext_removed=True, offhost_copy_verified=False)
        save(path, report)
    retain_verified(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state = args.state_dir.resolve()
    with (state / "maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(backup(state, args.output.resolve(), datetime.now(UTC).date().isoformat()), sort_keys=True))


if __name__ == "__main__":
    main()
