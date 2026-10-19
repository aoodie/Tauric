"""Fixed host supervision for the separately installed, research-only engines.

This helper neither installs dependencies nor changes Tauric's trading services.
The operator records an already verified release and its extracted source hashes
in /srv/tauric-quant/control/config.json before installing the generated units.
"""

import argparse
import hashlib
import ipaddress
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote

ROOT = Path("/srv/tauric-quant")
ENGINES = ("qlib", "lean")
PROJECT = "tauric_server"
LEAN_IMAGE = "quantconnect/lean@sha256:7a72e2b753bada616422d47ddb80857e9544d1d68210fa86fe453e482a39833b"


def read_config():
    config = json.loads((ROOT / "control/config.json").read_text())
    revision = config["source_revision"]
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("A complete verified source revision is required")
    if config["lean_image"] != LEAN_IMAGE:
        raise ValueError("The research engine must use the reviewed image digest")
    files = config["source_files"]
    if (
        not files
        or not {"backend/tauric/quant_worker.py", "backend/runners/qlib_runner.py"}
        <= files.keys()
    ):
        raise ValueError("The research source inventory is incomplete")
    for name, digest in files.items():
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.parts[0] != "backend"
            or not re.fullmatch(r"[a-f0-9]{64}", digest)
        ):
            raise ValueError("Invalid research source inventory entry")
    return config


def verify_source(config):
    manifest = json.loads(Path("/srv/tauric/current/release-manifest.json").read_text())
    revision = config["source_revision"]
    if manifest["revision"] != revision:
        raise ValueError(
            "Revalidate the research workers against the installed release"
        )
    source = ROOT / "source" / revision
    for name, digest in config["source_files"].items():
        path = source / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Installed research source integrity failed")
    return source


def capture(args):
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode:
        # Never include environment values or connection URLs in service logs.
        raise RuntimeError("Fixed research host inspection failed")
    return result.stdout


def database_ip(inspection):
    if len(inspection) != 1:
        raise ValueError("Expected exactly one Tauric database container")
    container = inspection[0]
    labels = container["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != PROJECT
        or labels.get("com.docker.compose.service") != "db"
        or not container["State"]["Running"]
        or container["State"].get("Health", {}).get("Status") != "healthy"
    ):
        raise ValueError("The installed Tauric database must be healthy")
    addresses = [
        network["IPAddress"]
        for name, network in container["NetworkSettings"]["Networks"].items()
        if name.startswith(PROJECT + "_") and "db" in (network.get("Aliases") or [])
    ]
    if len(addresses) != 1 or not ipaddress.ip_address(addresses[0]).is_private:
        raise ValueError("Expected one private Tauric database address")
    return addresses[0]


def environment_text(values):
    def quoted(value):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

    if any(
        "\n" in value or "\r" in value or "\0" in value for value in values.values()
    ):
        raise ValueError("Invalid private environment value")
    return "".join(f"{key}={quoted(value)}\n" for key, value in values.items())


def prepare(engine):
    config = read_config()
    source = verify_source(config)
    if subprocess.run(
        ["systemctl", "is-active", "--quiet", "tauric-engineering.service"], check=False
    ).returncode:
        raise ValueError("Research workers wait for the host backup supervisor")
    api_ids = capture(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
            "--filter",
            "label=com.docker.compose.service=api",
            "--format",
            "{{.ID}}",
        ]
    ).split()
    if len(api_ids) != 1:
        raise ValueError("Research workers wait for the installed API")
    api = json.loads(capture(["docker", "inspect", api_ids[0]]))[0]
    if (
        api["Config"]["Labels"].get("org.opencontainers.image.revision")
        != config["source_revision"]
        or api["State"].get("Health", {}).get("Status") != "healthy"
    ):
        raise ValueError("Revalidate research source against the healthy installed API")
    ids = capture(
        [
            "docker",
            "ps",
            "--filter",
            "label=com.docker.compose.project=" + PROJECT,
            "--filter",
            "label=com.docker.compose.service=db",
            "--format",
            "{{.ID}}",
        ]
    ).split()
    if len(ids) != 1:
        raise ValueError("Expected exactly one running Tauric database")
    address = database_ip(json.loads(capture(["docker", "inspect", ids[0]])))
    password = Path("/srv/tauric-private/secrets/database-app").read_text().strip()
    if not password:
        raise ValueError("The private application database credential is missing")
    values = {
        "TAURIC_DATABASE_URL": f"postgresql+psycopg://tauric_app:{quote(password, safe='')}@{address}:5432/tauric",
        "PYTHONPATH": str(source / "backend"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    target = Path("/run/tauric-quant") / (engine + ".env")
    content = environment_text(values)
    fd, filename = tempfile.mkstemp(prefix=engine + ".", dir=target.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def owned_lean_container(container):
    name = container.get("Name", "").removeprefix("/tauric-lab-")
    if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", name):
        return False
    mounts = container.get("Mounts", [])
    return (
        container.get("Config", {}).get("Image") == LEAN_IMAGE
        and len(mounts) == 1
        and mounts[0].get("Type") == "bind"
        and mounts[0].get("Destination") == "/job"
        and mounts[0].get("Source") == str(ROOT / "artifacts/lean" / name)
    )


def cleanup(engine):
    if engine != "lean":
        return
    ids = capture(["docker", "ps", "-q", "--filter", "name=^/tauric-lab-"]).split()
    if ids:
        for container in json.loads(capture(["docker", "inspect", *ids])):
            if owned_lean_container(container):
                capture(["docker", "rm", "-f", container["Id"]])


def unit(engine):
    if engine not in ENGINES:
        raise ValueError("Unknown research engine")
    helper = ROOT / "control/quant_host.py"
    artifact = ROOT / "artifacts" / engine
    docker = "SupplementaryGroups=docker\n" if engine == "lean" else ""
    memory = "3G" if engine == "qlib" else "512M"
    return f"""[Unit]
Description=Tauric {engine} research worker
After=docker.service network-online.target tauric-engineering.service
Wants=network-online.target
Requires=docker.service
PartOf=tauric-engineering.service
StartLimitIntervalSec=900
StartLimitBurst=60

[Service]
Type=simple
User=tauric-quant-{engine}
Group=tauric-quant-{engine}
{docker}WorkingDirectory={ROOT}
EnvironmentFile=-/run/tauric-quant/{engine}.env
ExecStartPre=+/usr/bin/python3 {helper} prepare --engine {engine}
ExecStart={ROOT}/runtimes/app/bin/python -m tauric.quant_worker --engine {engine} --python {ROOT}/runtimes/qlib/bin/python --artifacts {artifact}
ExecStopPost=+/usr/bin/python3 {helper} cleanup --engine {engine}
Restart=on-failure
RestartSec=15
TimeoutStopSec=30
KillMode=control-group
UMask=0077
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectHome=yes
ProtectSystem=strict
ReadWritePaths=/run/tauric-quant {artifact}
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=yes
RestrictSUIDSGID=yes
CPUQuota=200%
MemoryMax={memory}
TasksMax=256
OOMPolicy=stop

[Install]
WantedBy=multi-user.target tauric-engineering.service
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "cleanup", "unit"))
    parser.add_argument("--engine", choices=ENGINES, required=True)
    args = parser.parse_args()
    if args.action == "unit":
        print(unit(args.engine), end="")
    else:
        if os.geteuid() != 0:
            raise SystemExit("The fixed host preparation helper requires root")
        (prepare if args.action == "prepare" else cleanup)(args.engine)


if __name__ == "__main__":
    main()
