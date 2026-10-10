"""Prepare and operate a digest-pinned Tauric server without printing secrets."""

import argparse
import base64
import hashlib
import ipaddress
import json
import os
import platform
import posixpath
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_KEYS = (
    "TAURIC_API_IMAGE",
    "TAURIC_WORKER_IMAGE",
    "TAURIC_WEB_IMAGE",
    "TAURIC_OPENCLAW_IMAGE",
)
PRIVATE_FILES = (
    "database-admin",
    "database-app",
    "model-profiles.json",
    "market-data.json",
    "model-key",
    "openclaw-private.json",
)


def read_env(path):
    values = {}
    for line in path.read_text().splitlines():
        if line and not line.startswith("#"):
            key, value = line.split("=", 1)
            if key in values:
                raise ValueError("Duplicate configuration key.")
            values[key] = value
    return values


def private_write(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(content if isinstance(content, bytes) else content.encode())


def validate_images(values, local=False):
    for key in IMAGE_KEYS:
        value = values.get(key, "")
        if not re.fullmatch(r"[a-z0-9./_-]+@sha256:[a-f0-9]{64}", value) and (
            not local or not re.fullmatch(r"sha256:[a-f0-9]{64}", value)
        ):
            raise ValueError(f"{key} must identify an immutable image digest.")


def public_ip_address(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.version == 4 and address.is_global


def setup(directory, images, domain=None, port=8080, local=False):
    values = read_env(images)
    validate_images(values, local)
    if (
        domain
        and not public_ip_address(domain)
        and not re.fullmatch(
            r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?",
            domain,
        )
    ):
        raise ValueError(
            "Supply a DNS hostname or public IPv4 address without a scheme, path or port."
        )
    if not 1024 <= port <= 65535:
        raise ValueError("Private web port must be between 1024 and 65535.")
    if directory.exists():
        raise ValueError(
            "State directory already exists; setup never overwrites credentials."
        )
    directory.mkdir(mode=0o700, parents=True)
    private = directory / "secrets"
    private.mkdir(mode=0o700)
    for name in ("database-admin", "database-app"):
        private_write(private / name, secrets.token_urlsafe(48))
    for name, example in (
        ("model-profiles.json", "model-profiles.example.json"),
        ("market-data.json", "market-data.example.json"),
    ):
        private_write(private / name, (ROOT / "deploy" / example).read_bytes())
    private_write(
        private / "model-key", base64.urlsafe_b64encode(secrets.token_bytes(32))
    )
    private_write(
        private / "openclaw-private.json",
        json.dumps(
            {
                key: secrets.token_urlsafe(48)
                for key in ("gateway_token", "platform_token")
            }
        ),
    )
    # Compose file-backed secrets preserve host permissions. The enclosing
    # directory is 0700; readable leaf files let non-root containers consume
    # only their explicitly mounted secrets without any bootstrap capabilities.
    for name in PRIVATE_FILES:
        (private / name).chmod(0o644)
    values = {key: values[key] for key in IMAGE_KEYS}
    values.update(
        TAURIC_SECRETS_DIR=str(private.resolve()),
        WEB_PORT=str(port),
        TAURIC_SECURE_COOKIES="true" if domain else "false",
        TAURIC_ALLOWED_ORIGINS=json.dumps(
            [f"https://{domain}" if domain else f"http://localhost:{port}"],
            separators=(",", ":"),
        ),
    )
    if domain:
        values["TAURIC_DOMAIN"] = domain
    private_write(
        directory / "server.env",
        "".join(f"{key}={value}\n" for key, value in values.items()),
    )


def command(directory, project):
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,62}", project):
        raise ValueError("Use a distinct lowercase Compose project name.")
    values = read_env(directory / "server.env")
    result = [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        str(directory / "server.env"),
        "-f",
        str(ROOT / "deploy/compose.server.yml"),
    ]
    if values.get("TAURIC_DOMAIN"):
        override = (
            "compose.public-ip.yml"
            if public_ip_address(values["TAURIC_DOMAIN"])
            else "compose.https.yml"
        )
        result += ["-f", str(ROOT / "deploy" / override)]
    recovery = ROOT / "deploy/compose.gateway-recovery.yml"
    if recovery.is_file():
        result += ["-f", str(recovery)]
    return result


class ServerOperationError(RuntimeError):
    def __init__(self, result):
        super().__init__(
            "Server operation failed; inspect local service diagnostics privately."
        )
        self.diagnostics = ""
        for value in (result.stdout, result.stderr):
            if value:
                self.diagnostics += (
                    value.decode(errors="replace")
                    if isinstance(value, bytes)
                    else value
                )


def run(args, **kwargs):
    result = subprocess.run(args, stderr=subprocess.PIPE, check=False, **kwargs)
    if result.returncode:
        # Docker/SQL failures may contain expanded connection details.
        raise ServerOperationError(result)
    return result


def validate(directory, project, local=False):
    values = read_env(directory / "server.env")
    validate_images(values, local)
    private = Path(values["TAURIC_SECRETS_DIR"])
    for path in (directory, directory / "server.env", private):
        if path.is_symlink() or not path.exists() or path.stat().st_mode & 0o077:
            raise ValueError(
                "Private configuration must exist without group/other access or symlinks."
            )
    for name in PRIVATE_FILES:
        path = private / name
        if path.is_symlink() or not path.is_file() or path.stat().st_mode & 0o022:
            raise ValueError("Secret files must exist and reject group/other writes.")
        if path.stat().st_mode & 0o444 != 0o444:
            raise ValueError(
                "File-mounted secrets need readable leaf permissions inside private directories."
            )
    rendered = json.loads(
        run(
            command(directory, project)
            + ["--profile", "*", "config", "--format", "json"],
            capture_output=False,
            stdout=subprocess.PIPE,
        ).stdout
    )
    for name, service in rendered["services"].items():
        if service.get("build"):
            raise ValueError("Server installation cannot build images.")
        if name != "web" and service.get("ports"):
            raise ValueError("Only the web ingress may publish ports.")
        if name != "db" and "TAURIC_DATABASE_URL" in service.get("environment", {}):
            raise ValueError("Database credentials must use private files.")
        if name not in {"db", "web"} and (
            service.get("user")
            != ("1000:1000" if name == "openclaw" else "10001:10001")
            or service.get("cap_add")
            or "ALL" not in service.get("cap_drop", [])
            or not service.get("read_only")
        ):
            raise ValueError(
                "Application services must run unprivileged with read-only roots and no capabilities."
            )
    api = rendered["services"]["api"]["environment"]
    if values.get("TAURIC_DOMAIN"):
        if api["TAURIC_SECURE_COOKIES"] != "true" or json.loads(
            api["TAURIC_ALLOWED_ORIGINS"]
        ) != ["https://" + values["TAURIC_DOMAIN"]]:
            raise ValueError(
                "Public ingress requires HTTPS cookies and the exact HTTPS origin."
            )
    else:
        for port in rendered["services"]["web"]["ports"]:
            if port.get("host_ip") != "127.0.0.1":
                raise ValueError("Private ingress must bind to loopback.")
    return {
        "configuration_valid": True,
        "services": len(rendered["services"]),
        "public_https": bool(values.get("TAURIC_DOMAIN")),
    }


def preflight(directory, project, local=False):
    result = validate(directory, project, local)
    info = json.loads(
        run(["docker", "info", "--format", "{{json .}}"], stdout=subprocess.PIPE).stdout
    )
    version = (
        run(["docker", "compose", "version", "--short"], stdout=subprocess.PIPE)
        .stdout.decode()
        .strip()
        .lstrip("v")
    )
    numbers = tuple(int(value) for value in version.split("-")[0].split(".")[:3])
    if numbers < (2, 24, 4):
        raise ValueError("Compose 2.24.4 or later is required for the HTTPS override.")
    if info["OSType"] != "linux":
        raise ValueError("Linux containers are required.")
    result.update(
        host_system=platform.system(),
        docker_architecture=info["Architecture"],
        docker_memory_bytes=info["MemTotal"],
        available_disk_bytes=shutil.disk_usage(directory).free,
        compose_version=version,
        clock_utc=datetime.now(UTC).isoformat(),
        clean_linux_host_verified=False,
    )
    # This reports capacity; a production workload needs measured soak evidence.
    return result


def snapshot(compose):
    return json.loads(
        run(
            compose
            + [
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "api",
                "python",
                "-m",
                "tauric.release_check",
                "snapshot",
            ],
            stdout=subprocess.PIPE,
        ).stdout
    )


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def volume_name(compose, service, destination):
    ids = (
        run(compose + ["ps", "-a", "-q", service], stdout=subprocess.PIPE)
        .stdout.decode()
        .split()
    )
    if not ids:
        return None
    mounts = json.loads(
        run(
            ["docker", "inspect", ids[0], "--format", "{{json .Mounts}}"],
            stdout=subprocess.PIPE,
        ).stdout
    )
    return next(
        (
            mount["Name"]
            for mount in mounts
            if mount["Destination"] == destination and mount["Type"] == "volume"
        ),
        None,
    )


def freeze_engineering(directory):
    """Freeze isolated writes before copying private keys, receipts and bounded disks."""
    values = read_env(directory / "server.env")
    private = Path(values["TAURIC_SECRETS_DIR"]) / "engineering"
    if not (private / "install.json").is_file():
        return False
    active = (
        subprocess.run(
            ["systemctl", "is-active", "--quiet", "tauric-engineering.service"],
            check=False,
        ).returncode
        == 0
    )
    if active:
        run(["systemctl", "stop", "tauric-engineering.service"], stdout=subprocess.PIPE)
    executions = (
        run(
            ["docker", "ps", "-q", "--filter", "label=io.tauric.engineering=v1"],
            stdout=subprocess.PIPE,
        )
        .stdout.decode()
        .split()
    )
    if executions:
        # Interrupted development is reconciled on resume; its stable identity is retained.
        run(["docker", "kill", *executions], stdout=subprocess.PIPE)
    for target in sorted((private / "workspaces").glob("*/mount")):
        if (
            subprocess.run(["mountpoint", "-q", str(target)], check=False).returncode
            == 0
        ):
            run(["umount", str(target)], stdout=subprocess.PIPE)
    return active


def backup(directory, project, output, local=False):
    validate(directory, project, local)
    if output.exists():
        raise ValueError("Backup output exists; it will not be overwritten.")
    compose = command(directory, project) + ["--profile", "*"]
    # Preserve exactly which services were running, and freeze all application
    # writes before capturing the database plus filesystem state.
    active = (
        run(
            compose + ["ps", "--status", "running", "--services"],
            stdout=subprocess.PIPE,
        )
        .stdout.decode()
        .split()
    )
    if "db" not in active:
        raise ValueError("The database must be running before backup.")
    stopped = [name for name in active if name != "db"]
    engineering_active = False
    with tempfile.TemporaryDirectory(prefix="tauric-backup-") as folder:
        staging = Path(folder)
        try:
            if stopped:
                run(compose + ["stop", *stopped], stdout=subprocess.PIPE)
            engineering_active = freeze_engineering(directory)
            before = snapshot(compose)
            dump = staging / "database.dump"
            with dump.open("wb") as target:
                run(
                    compose
                    + [
                        "exec",
                        "-T",
                        "db",
                        "pg_dump",
                        "-U",
                        "tauric_admin",
                        "--format=custom",
                        "tauric",
                    ],
                    stdout=target,
                )
            values = read_env(directory / "server.env")
            shutil.copytree(values["TAURIC_SECRETS_DIR"], staging / "secrets")
            shutil.copyfile(directory / "server.env", staging / "server.env")
            volumes = {}
            for service, destination, filename in (
                ("openclaw", "/state", "openclaw-state.tar.gz"),
                ("worker", "/runtime", "native-runtime.tar.gz"),
                ("web", "/data", "caddy-data.tar.gz"),
                ("web", "/config", "caddy-config.tar.gz"),
            ):
                volume = volume_name(compose, service, destination)
                if volume:
                    with (staging / filename).open("wb") as target:
                        run(
                            [
                                "docker",
                                "run",
                                "--rm",
                                "--network",
                                "none",
                                "--read-only",
                                "--mount",
                                f"type=volume,source={volume},target=/data,readonly",
                                "--entrypoint",
                                "tar",
                                values["TAURIC_API_IMAGE"],
                                "-C",
                                "/data",
                                "-czf",
                                "-",
                                ".",
                            ],
                            stdout=target,
                        )
                    volumes[filename] = {"service": service, "destination": destination}
            files = {
                str(path.relative_to(staging)): checksum(path)
                for path in staging.rglob("*")
                if path.is_file()
            }
            (staging / "manifest.json").write_text(
                json.dumps(
                    {
                        "format": 1,
                        "created_utc": datetime.now(UTC).isoformat(),
                        "snapshot": before,
                        "files": files,
                        "volumes": volumes,
                    },
                    indent=2,
                )
                + "\n"
            )
            output.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with (
                os.fdopen(fd, "wb") as target,
                tarfile.open(fileobj=target, mode="w:gz") as archive,
            ):
                for path in sorted(staging.iterdir()):
                    archive.add(path, arcname=path.name)
        finally:
            if engineering_active:
                run(
                    ["systemctl", "start", "tauric-engineering.service"],
                    stdout=subprocess.PIPE,
                )
            if stopped:
                run(compose + ["start", *stopped], stdout=subprocess.PIPE)
    return {
        "backup_created": True,
        "sha256": checksum(output),
        "tables": len(before["tables"]),
        "runtime_volumes": len(volumes),
        "previous_services_resumed": True,
    }


def extract_backup(archive, staging):
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        for member in members:
            path = Path(member.name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not (member.isfile() or member.isdir())
            ):
                raise ValueError("Backup contains an unsafe member.")
        source.extractall(staging, filter="data")
    manifest = json.loads((staging / "manifest.json").read_text())
    if manifest["format"] != 1:
        raise ValueError("Unsupported backup format.")
    actual = {
        str(path.relative_to(staging)) for path in staging.rglob("*") if path.is_file()
    } - {"manifest.json"}
    if actual != set(manifest["files"]):
        raise ValueError("Backup file inventory differs from its manifest.")
    for name, expected in manifest["files"].items():
        if checksum(staging / name) != expected:
            raise ValueError("Backup file checksum failed.")
    for name in manifest["volumes"]:
        with tarfile.open(staging / name) as volume:
            for member in volume.getmembers():
                path = Path(member.name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or not (
                        member.isfile()
                        or member.isdir()
                        or member.issym()
                        or member.islnk()
                    )
                ):
                    raise ValueError("Runtime backup contains an unsafe member.")
                if member.issym() or member.islnk():
                    base = str(path.parent) if member.issym() else "."
                    target = posixpath.normpath(posixpath.join(base, member.linkname))
                    if target.startswith(("/", "../")) or target == "..":
                        raise ValueError("Runtime backup link escapes its volume.")
    return manifest


def restore_check(directory, project, archive, local=False):
    # This command only accepts a NEW configuration directory and NEW project.
    # It never starts API/background services against restored trading policies.
    if directory.exists():
        raise ValueError("Restore requires a new state directory.")
    with tempfile.TemporaryDirectory(prefix="tauric-restore-") as folder:
        staging = Path(folder)
        manifest = extract_backup(archive, staging)
        directory.mkdir(mode=0o700, parents=True)
        private_write(
            directory / "RESTORED_REVIEW_REQUIRED",
            "Restored settings may authorize trading. Review policies before starting services.\n",
        )
        shutil.copytree(staging / "secrets", directory / "secrets")
        values = read_env(staging / "server.env")
        values["TAURIC_SECRETS_DIR"] = str((directory / "secrets").resolve())
        private_write(
            directory / "server.env",
            "".join(f"{key}={value}\n" for key, value in values.items()),
        )
        for path in (directory / "secrets").iterdir():
            path.chmod(0o644 if path.is_file() else 0o700)
        for folder in ("requests", "cancel"):
            target = directory / "secrets" / "engineering" / "spool" / folder
            if target.is_dir():
                os.chown(target, 10001, 10001)
        (directory / "secrets").chmod(0o700)
        validate(directory, project, local)
        compose = command(directory, project) + ["--profile", "*"]
        existing = run(
            compose + ["ps", "-a", "-q"], stdout=subprocess.PIPE
        ).stdout.strip()
        existing_volumes = run(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={project}",
            ],
            stdout=subprocess.PIPE,
        ).stdout.strip()
        if existing or existing_volumes:
            raise ValueError("Restore refuses an existing Compose project or volume.")
        run(compose + ["up", "-d", "--wait", "db"], stdout=subprocess.PIPE)
        with (staging / "database.dump").open("rb") as original:
            run(
                compose
                + [
                    "exec",
                    "-T",
                    "db",
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "-U",
                    "tauric_admin",
                    "-d",
                    "tauric",
                ],
                stdin=original,
                stdout=subprocess.PIPE,
            )
        restored = snapshot(compose)
        if restored != manifest["snapshot"]:
            raise ValueError(
                "Restored database differs from the captured table hashes."
            )
        for filename, binding in manifest["volumes"].items():
            # Create stopped containers to obtain exact Compose-owned volumes.
            run(
                compose + ["create", binding["service"]],
                stdout=subprocess.PIPE,
            )
            volume = volume_name(compose, binding["service"], binding["destination"])
            with (staging / filename).open("rb") as original:
                run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "-i",
                        "--network",
                        "none",
                        "--read-only",
                        "--mount",
                        f"type=volume,source={volume},target=/data",
                        "--entrypoint",
                        "tar",
                        values["TAURIC_API_IMAGE"],
                        "-C",
                        "/data",
                        "-xzf",
                        "-",
                    ],
                    stdin=original,
                    stdout=subprocess.PIPE,
                )
            with tempfile.TemporaryFile() as recaptured:
                run(
                    [
                        "docker",
                        "run",
                        "--rm",
                        "--network",
                        "none",
                        "--read-only",
                        "--mount",
                        f"type=volume,source={volume},target=/data,readonly",
                        "--entrypoint",
                        "tar",
                        values["TAURIC_API_IMAGE"],
                        "-C",
                        "/data",
                        "-czf",
                        "-",
                        ".",
                    ],
                    stdout=recaptured,
                )
                # Check extracted file content and permissions, independent of
                # gzip headers and directory enumeration order.
                recaptured.seek(0)
                with (
                    tarfile.open(staging / filename) as a,
                    tarfile.open(fileobj=recaptured) as b,
                ):

                    def inventory(tar):
                        return {
                            m.name: (
                                m.type,
                                m.mode,
                                m.uid,
                                m.gid,
                                hashlib.sha256(tar.extractfile(m).read()).hexdigest()
                                if m.isfile()
                                else m.linkname,
                            )
                            for m in tar.getmembers()
                        }

                    if inventory(a) != inventory(b):
                        raise ValueError("Restored runtime volume differs from backup.")
        return {
            "database_restored": True,
            "table_hashes_match": True,
            "schema": restored["schema"],
            "tables": len(restored["tables"]),
            "runtime_volumes_verified": len(manifest["volumes"]),
            "background_services_started": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".env.server-state")
    parser.add_argument("--project", default="tauric_server")
    parser.add_argument(
        "--local-images",
        action="store_true",
        help="Permit local image IDs only for isolated rehearsals",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    prepare = sub.add_parser("setup")
    prepare.add_argument("--images", type=Path, required=True)
    prepare.add_argument("--domain")
    prepare.add_argument("--port", type=int, default=8080)
    sub.add_parser("check")
    sub.add_parser("preflight")
    start = sub.add_parser("start")
    start.add_argument("--with-ai", action="store_true")
    start.add_argument("--with-execution", action="store_true")
    owner = sub.add_parser("create-owner")
    owner.add_argument("--username", required=True)
    save = sub.add_parser("backup")
    save.add_argument("--output", type=Path, required=True)
    restore = sub.add_parser("restore-check")
    restore.add_argument("--archive", type=Path, required=True)
    args = parser.parse_args()
    directory = args.state_dir.resolve()
    try:
        if args.action == "setup":
            setup(directory, args.images, args.domain, args.port, args.local_images)
            result = {"private_configuration_created": True, "trading_enabled": False}
        elif args.action in ("check", "preflight"):
            result = (validate if args.action == "check" else preflight)(
                directory, args.project, args.local_images
            )
        elif args.action == "backup":
            result = backup(
                directory, args.project, args.output.resolve(), args.local_images
            )
        elif args.action == "restore-check":
            result = restore_check(
                directory, args.project, args.archive, args.local_images
            )
        else:
            validate(directory, args.project, args.local_images)
            compose = command(directory, args.project)
            if args.action == "start":
                if (directory / "RESTORED_REVIEW_REQUIRED").exists():
                    raise ValueError(
                        "Restored policy review is required before starting application services."
                    )
                if args.with_ai:
                    compose += ["--profile", "analysis", "--profile", "governance"]
                if args.with_execution:
                    compose += ["--profile", "execution"]
                run(
                    compose + ["up", "-d", "--wait", "--wait-timeout", "240"],
                    stdout=subprocess.PIPE,
                )
                result = {"services_started": True}
            else:
                run(
                    compose
                    + [
                        "run",
                        "--rm",
                        "api",
                        "python",
                        "-m",
                        "tauric.cli",
                        "create-owner",
                        "--username",
                        args.username,
                    ]
                )
                result = {"owner_created": True}
        print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError, OSError) as error:
        # Only our bounded errors are safe; OS exceptions may contain file names
        # and provider content is never forwarded by run().
        if isinstance(error, OSError):
            parser.exit(
                1, "Server operation could not access a required file or command.\n"
            )
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
