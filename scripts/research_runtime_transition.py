"""Separate trusted installation of the reviewed certifi security patch only."""

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from release_identity import GitHubApp
from release_publish import save
from release_workflow import completed_release

ROOT = Path("/srv/tauric-quant")
STATE = Path("/srv/tauric-private")
VERSION = "2026.10.18"
WHEEL_SHA256 = "73191c47aaa79da394e8fee2dbc2ec827432cab56311122c9e47abf79e390f62"
LOCKS = ("backend/requirements.lock", "backend/requirements.qlib.lock")
UNITS = ("tauric-quant-lean.service", "tauric-quant-qlib.service")


def inventory(python):
    code = "import json,importlib.metadata as m;print(json.dumps({d.metadata['Name'].lower().replace('_','-'):d.version for d in m.distributions()},sort_keys=True))"
    result = subprocess.run([str(python), "-I", "-c", code], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(result.stdout)


def only_certificate_change(before, after):
    return before.get("certifi") == "2026.7.22" and after == {**before, "certifi": VERSION}


def reviewed_lock(before, after):
    old, new = b"certifi==2026.7.22", b"certifi==2026.10.18"
    return before.count(old) == 1 and after == before.replace(old, new)


def bootstrap_pip(runtime_python, work, log):
    """Build a private pip environment without changing the stripped runtime."""
    environment = work / "installer"
    if environment.is_symlink() or (environment.exists() and not environment.is_dir()):
        raise ValueError("The private runtime installer path is unsafe.")
    subprocess.run(
        [str(runtime_python), "-I", "-m", "venv", "--clear", str(environment)],
        check=True,
        stdout=log,
        stderr=log,
        timeout=180,
    )
    python = environment / "bin/python"
    subprocess.run(
        [str(python), "-I", "-m", "pip", "--isolated", "--version"],
        check=True,
        stdout=log,
        stderr=log,
        timeout=30,
    )
    return python


def runtime_site_packages(python):
    code = "import json,sys,sysconfig;print(json.dumps([sys.prefix,sysconfig.get_path('purelib')]))"
    result = subprocess.run(
        [str(python), "-I", "-c", code],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    prefix, purelib = map(Path, json.loads(result.stdout))
    prefix, purelib = prefix.resolve(), purelib.resolve()
    if not purelib.is_relative_to(prefix) or not purelib.is_dir():
        raise ValueError("The runtime package directory is outside its verified environment.")
    return purelib


def remove_directory(path):
    if path.is_symlink():
        raise ValueError("A runtime package path cannot be a symlink.")
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def recover_certificate_install(site, backup, old_version):
    """Restore any old directories moved by an interrupted package replacement."""
    if backup.is_symlink():
        raise ValueError("The runtime package recovery path cannot be a symlink.")
    if not backup.exists():
        return
    if not backup.is_dir():
        raise ValueError("The runtime package recovery path is not a directory.")
    old_package = backup / "certifi"
    old_metadata = backup / "metadata"
    if old_package.exists():
        remove_directory(site / "certifi")
        os.replace(old_package, site / "certifi")
    if old_metadata.exists():
        remove_directory(site / f"certifi-{VERSION}.dist-info")
        os.replace(old_metadata, site / f"certifi-{old_version}.dist-info")
    if any(backup.iterdir()):
        raise ValueError("Unexpected files remain in the runtime package recovery path.")
    backup.rmdir()


def install_certificate(installer_python, runtime_python, kind, work, request_id, before, log):
    if kind not in {"app", "qlib"} or before.get("certifi") != "2026.7.22":
        raise ValueError("Only the reviewed original app and qlib certificate runtimes are supported.")
    expected_after = {**before, "certifi": VERSION}
    site = runtime_site_packages(runtime_python)
    old_version = before.get("certifi")
    old_metadata = site / f"certifi-{old_version}.dist-info"
    new_metadata = site / f"certifi-{VERSION}.dist-info"
    backup = site.parent / f".tauric-certifi-backup-{request_id}-{kind}"
    stage = site.parent / f".tauric-certifi-stage-{request_id}-{kind}"
    if backup.is_symlink() or stage.is_symlink():
        raise ValueError("A runtime package staging path cannot be a symlink.")
    if stage.exists() and not stage.is_dir():
        raise ValueError("The runtime package staging path is not a directory.")

    current = inventory(runtime_python)
    if current == expected_after and (site / "certifi").is_dir() and new_metadata.is_dir():
        return current, backup
    if backup.exists():
        recover_certificate_install(site, backup, old_version)
        current = inventory(runtime_python)
    if current != before:
        raise ValueError("The runtime package inventory needs reconciliation before retrying.")
    if (site / "certifi").is_symlink() or old_metadata.is_symlink() or new_metadata.is_symlink():
        raise ValueError("The original runtime certificate package cannot be a symlink.")
    if not (site / "certifi").is_dir() or not old_metadata.is_dir() or new_metadata.exists():
        raise ValueError("The original certificate package directories are ambiguous.")

    remove_directory(stage)
    try:
        subprocess.run(
            [
                str(installer_python), "-I", "-m", "pip", "--isolated", "install",
                "--no-index", "--find-links", str(work), "--no-deps", "--require-hashes",
                "--target", str(stage), "-r", str(work / "requirements.txt"),
            ],
            check=True,
            stdout=log,
            stderr=log,
            timeout=180,
        )
        if {path.name for path in stage.iterdir()} != {"certifi", f"certifi-{VERSION}.dist-info"}:
            raise ValueError("The pinned wheel produced an unexpected package layout.")
        backup.mkdir(mode=0o700)
        os.replace(site / "certifi", backup / "certifi")
        os.replace(old_metadata, backup / "metadata")
        os.replace(stage / "certifi", site / "certifi")
        os.replace(stage / f"certifi-{VERSION}.dist-info", new_metadata)
        current = inventory(runtime_python)
        if not only_certificate_change(before, current):
            raise ValueError("A runtime package other than the reviewed certificate package changed.")
        return current, backup
    except Exception:
        recover_certificate_install(site, backup, old_version)
        raise
    finally:
        remove_directory(stage)


def clear_certificate_backup(backup):
    if backup.is_symlink():
        raise ValueError("The runtime package recovery path cannot be a symlink.")
    if backup.exists():
        if not backup.is_dir() or not {path.name for path in backup.iterdir()} <= {"certifi", "metadata"}:
            raise ValueError("The runtime package recovery path is unexpected.")
        shutil.rmtree(backup)


def receipt_path(revision):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Exact runtime transition revision required.")
    return ROOT / "control/dependency-transitions" / (revision + ".json")


def validate(revision, previous, expected, original):
    path = receipt_path(revision)
    if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0 or path.stat().st_mode & 0o077:
        raise ValueError("Changed dependencies need a root-owned separately verified installation receipt.")
    record = json.loads(path.read_text())
    if (record.get("phase") != "installed_verified" or record.get("revision") != revision
            or record.get("base_revision") != previous or record.get("original") != original
            or record.get("locks") != {name: expected[name] for name in LOCKS}
            or record.get("wheel_sha256") != WHEEL_SHA256
            or not set(record.get("units", [])) <= set(UNITS)):
        raise ValueError("Runtime dependency installation is not bound to this release.")
    for kind in ("app", "qlib"):
        if (not only_certificate_change(record["before"][kind], record["after"][kind])
                or inventory(ROOT / "runtimes" / kind / "bin/python") != record["after"][kind]):
            raise ValueError("Installed runtime differs from the separately tested dependency transition.")
    return record


def install(intent_path):
    if (os.geteuid() != 0 or intent_path.is_symlink() or intent_path.stat().st_uid != 0
            or intent_path.stat().st_mode & 0o077 or not intent_path.is_relative_to(STATE)):
        raise ValueError("A private root-owned runtime installation intent is required.")
    intent = json.loads(intent_path.read_text())
    revision, previous = intent["revision"], intent["base_revision"]
    if (intent.get("kind") != "owner_requested_certifi_security_patch"
            or str(UUID(intent["id"])) != intent["id"]
            or not all(re.fullmatch(r"[a-f0-9]{40}", x) for x in (revision, previous))):
        raise ValueError("Only the explicitly reviewed certificate security transition is supported.")
    client = GitHubApp(STATE / "secrets/operations")
    run = client.repo("/actions/runs/" + str(intent["release_run_id"]))
    if not completed_release(client, run, revision, intent["branch"]):
        raise ValueError("The exact source's complete runtime and release checks must pass first.")
    candidate = Path("/srv/tauric/releases/openclaw-owner-" + revision[:12] + "/tauric-release")
    manifest = json.loads((candidate / "release-manifest.json").read_text())
    if manifest["revision"] != revision or any(hashlib.sha256((candidate / n).read_bytes()).hexdigest() != h for n, h in manifest["files"].items()):
        raise ValueError("Candidate runtime installer failed release source verification.")
    from quant_release import active_units, blob, idle, source_files, verify_inventory
    from quant_release import run as execute
    from release_install import compose_command, module

    tree = client.repo("/git/trees/" + revision + "?recursive=1")
    if tree.get("truncated"):
        raise ValueError("The full candidate source inventory is required.")
    expected = source_files(tree["tree"])
    original = json.loads((ROOT / "control/config.json").read_text())
    path = receipt_path(revision)
    if path.exists():
        record = json.loads(path.read_text())
        if record.get("intent") != intent:
            raise ValueError("Runtime transition identity already belongs to other inputs.")
        if record.get("phase") == "installed_verified":
            return validate(revision, previous, expected, record["original"])
    else:
        if json.loads(Path("/srv/tauric/current/release-manifest.json").read_text())["revision"] != previous or original["source_revision"] != previous:
            raise ValueError("The installed runtime base changed before preparation.")
        verify_inventory(ROOT / "source" / previous, original["source_files"])
        image = json.loads((candidate / "engineering-image.json").read_text())["image"]
        info = json.loads(execute(["docker", "image", "inspect", image]))[0]
        if info["Config"]["Labels"].get("org.opencontainers.image.revision") != revision or image not in info["RepoDigests"]:
            raise ValueError("Use the exact checked and already preloaded engineering image.")
        container = "tauric-runtime-locks-" + intent["id"]
        execute(["docker", "create", "--name", container, "--network", "none", "--read-only", "--cap-drop", "ALL", "--entrypoint", "/bin/true", image])
        try:
            with tempfile.TemporaryDirectory(prefix="runtime-locks-", dir=STATE) as folder:
                for name in LOCKS:
                    target = Path(folder) / Path(name).name
                    execute(["docker", "cp", container + ":/source/" + name, str(target)])
                    if blob(target.read_bytes()) != expected[name] or not reviewed_lock((ROOT / "source" / previous / name).read_bytes(), target.read_bytes()):
                        raise ValueError("This installer permits only the reviewed certifi lock change.")
        finally:
            execute(["docker", "rm", container])
        active = active_units()
        record = {"intent": intent, "revision": revision, "base_revision": previous, "original": original,
                  "locks": {name: expected[name] for name in LOCKS}, "units": active, "phase": "prepared",
                  "wheel_sha256": WHEEL_SHA256, "before": {k: inventory(ROOT / "runtimes" / k / "bin/python") for k in ("app", "qlib")}, "after": {}, "requested": []}
        if any(v.get("certifi") != "2026.7.22" for v in record["before"].values()):
            raise ValueError("The original runtime certificate package differs from the reviewed base.")
        save(path, record)
    server = module("runtime_transition_previous_server", Path("/srv/tauric/current/scripts/server.py"))
    if not idle(server, compose_command(server, STATE, "tauric_server") + ["--profile", "*"]):
        raise ValueError("Original research jobs must finish before runtime maintenance.")
    for unit in record["units"]:
        subprocess.run(["systemctl", "stop", unit], check=True, capture_output=True)
    if not idle(server, compose_command(server, STATE, "tauric_server") + ["--profile", "*"]):
        raise ValueError("Research arrived during runtime maintenance; reconcile it before patching.")
    work = STATE / ("runtime-patch-" + intent["id"])
    work.mkdir(mode=0o700, exist_ok=True)
    requirement = work / "requirements.txt"
    requirement.write_text("certifi==" + VERSION + " --hash=sha256:" + WHEEL_SHA256 + "\n")
    with (work / "install.log").open("ab") as log:
        (work / "install.log").chmod(0o600)
        installer_python = bootstrap_pip(ROOT / "runtimes/app/bin/python", work, log)
        subprocess.run([str(installer_python), "-I", "-m", "pip", "--isolated", "download", "--index-url", "https://pypi.org/simple", "--no-deps", "--only-binary=:all:", "--require-hashes", "-r", str(requirement), "--dest", str(work)], check=True, stdout=log, stderr=log, timeout=180)
        wheels = list(work.glob("certifi-*.whl"))
        if len(wheels) != 1 or hashlib.sha256(wheels[0].read_bytes()).hexdigest() != WHEEL_SHA256:
            raise ValueError("The downloaded certificate wheel does not match its reviewed hash.")
        for kind in ("app", "qlib"):
            python = ROOT / "runtimes" / kind / "bin/python"
            if kind not in record["requested"]:
                record["requested"].append(kind)
                save(path, record)
            after, backup = install_certificate(
                installer_python, python, kind, work, intent["id"], record["before"][kind], log
            )
            record["after"][kind] = after
            save(path, record)
            clear_certificate_backup(backup)
    record.update(phase="installed_verified", verified_at=datetime.now(timezone.utc).isoformat())
    save(path, record)
    return validate(revision, previous, expected, original)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intent", type=Path, required=True)
    args = parser.parse_args()
    with (STATE / "maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        value = install(args.intent.absolute())
        print(json.dumps({"phase": value["phase"], "revision": value["revision"], "workers_kept_stopped_until_source_activation": value["units"]}))


if __name__ == "__main__":
    main()
