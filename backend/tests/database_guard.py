"""Verify test DDL reaches this checkout's local Docker PostgreSQL cluster."""

import json
import os
import re
import subprocess
from functools import cache

from sqlalchemy import text

MESSAGE = "Test storage identity could not be verified. Start this checkout's local development database; remote databases and tunnels are refused."


def command(*args):
    try:
        return subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError(MESSAGE) from None


def validate_target(url, name):
    if (
        url.drivername != "postgresql+psycopg"
        or url.host != "127.0.0.1"
        or url.database != "postgres"
        or url.username != "tauric_admin"
        or url.query
        or not url.port
        or not 1024 <= url.port <= 65535
        or not re.fullmatch(r"tauric_test_[a-f0-9]{16}", name)
    ):
        raise RuntimeError(MESSAGE)


@cache
def local_cluster(root, port):
    # A loopback socket alone could be an SSH tunnel. Bind its server identity
    # to the running database of this exact checkout through a local Docker API.
    endpoint = (
        os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
    )
    if not endpoint:
        endpoint = json.loads(
            command("docker", "context", "inspect", "--format", "{{json .Endpoints.docker.Host}}")
        )
    if not isinstance(endpoint, str) or not endpoint.startswith("unix:///"):
        raise RuntimeError(MESSAGE)
    compose_file = root / "deploy/compose.dev.yml"
    container = command(
        "docker", "compose", "--env-file", str(root / ".env"),
        "-f", str(compose_file), "ps", "--status", "running", "-q", "db",
    )
    if not re.fullmatch(r"[a-f0-9]{64}", container):
        raise RuntimeError(MESSAGE)
    labels = json.loads(command("docker", "inspect", "--format", "{{json .Config.Labels}}", container))
    ports = json.loads(command("docker", "inspect", "--format", "{{json .NetworkSettings.Ports}}", container))
    bindings = ports.get("5432/tcp") or []
    if (
        labels.get("com.docker.compose.project") != "tauric_foundation"
        or labels.get("com.docker.compose.service") != "db"
        or labels.get("com.docker.compose.project.config_files") != str(compose_file)
        or not bindings
        or any(p.get("HostIp") != "127.0.0.1" or p.get("HostPort") != str(port) for p in bindings)
    ):
        raise RuntimeError(MESSAGE)
    identity = command(
        "docker", "exec", container, "psql", "-XAt", "--username=tauric_admin",
        "--dbname=postgres", "--command=SELECT system_identifier FROM pg_control_system()",
    )
    if not re.fullmatch(r"[0-9]{1,20}", identity):
        raise RuntimeError(MESSAGE)
    return identity


def verify_connection(connection, url, name, expected):
    validate_target(url, name)
    database, identity = connection.execute(
        text("SELECT current_database(), system_identifier::text FROM pg_control_system()")
    ).one()
    if database != "postgres" or identity != expected:
        raise RuntimeError(MESSAGE)
