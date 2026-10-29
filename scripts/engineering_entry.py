"""Fixed entry point inside the isolated container; no production credentials."""

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

TEST_MARKER = "TAURIC_TEST_RESULT="


def test_database(workspace, *, write_environment=True):
    """Disposable database wholly inside the network-disabled execution container."""
    binary = "/usr/lib/postgresql/15/bin/"
    data = "/tmp/tauric-test-postgres"
    with open("/tmp/tauric-test-postgres.log", "wb") as log:
        subprocess.run(
            [binary + "initdb", "-D", data, "--auth=trust", "--no-locale", "--encoding=UTF8"],
            check=True,
            stdout=log,
            stderr=log,
        )
        subprocess.run(
            [
                binary + "pg_ctl",
                "-D",
                data,
                "-l",
                "/tmp/tauric-test-postgres.log",
                "-o",
                "-h 127.0.0.1 -p 54330 -k /tmp -c max_connections=25 -c shared_buffers=16MB -c fsync=off",
                "-w",
                "start",
            ],
            check=True,
            stdout=log,
            stderr=log,
        )
        subprocess.run(
            [
                binary + "psql",
                "-h",
                "127.0.0.1",
                "-p",
                "54330",
                "-d",
                "postgres",
                "-v",
                "ON_ERROR_STOP=1",
                "-c",
                "CREATE ROLE tauric_admin SUPERUSER LOGIN; CREATE ROLE tauric_app LOGIN;",
            ],
            check=True,
            stdout=log,
            stderr=log,
        )
    if write_environment:
        (workspace / ".env").write_text(
            "POSTGRES_PASSWORD=isolated-test-only\nTAURIC_DB_PASSWORD=isolated-test-only\nDB_PORT=54330\n"
        )


def verification_subject(workspace, env):
    test_database(workspace, write_environment=False)
    subprocess.run(["/usr/lib/postgresql/15/bin/createdb", "-h", "127.0.0.1", "-p", "54330", "-U", "tauric_admin", "tauric_verification"], check=True)
    admin = {**env, "TAURIC_DATABASE_URL": "postgresql+psycopg://tauric_admin@127.0.0.1:54330/tauric_verification"}
    subprocess.run([sys.executable, "-m", "alembic", "-c", str(workspace / "backend/alembic.ini"), "upgrade", "head"],
                   cwd=workspace / "backend", env=admin, check=True)
    app = {**env, "TAURIC_DATABASE_URL": "postgresql+psycopg://tauric_app@127.0.0.1:54330/tauric_verification",
           "TAURIC_ALLOWED_ORIGINS": '["http://127.0.0.1:18000"]'}
    # No submitted command or test runner controls the verification process.
    return subprocess.call([sys.executable, "-m", "uvicorn", "tauric.app:app", "--host", "127.0.0.1", "--port", "18000"], cwd=workspace, env=app)


def test_command(script, report="/tmp/tauric-tests.xml"):
    args = shlex.split(script)
    blocked = {
        "--help",
        "-h",
        "--version",
        "-V",
        "--collect-only",
        "--co",
        "--setup-only",
        "--fixtures",
        "--fixtures-per-test",
    }
    if any(arg.split("=", 1)[0] in blocked for arg in args):
        raise ValueError(
            "A test receipt requires executed tests, not discovery or help."
        )
    if args[:3] == ["python", "-m", "pytest"]:
        if any(
            arg.startswith(("--junit", "--override-ini")) or arg == "-o"
            for arg in args[3:]
        ):
            raise ValueError("The executor controls the test result file.")
        return [sys.executable, "-I", "-m", "pytest", *args[3:], "--junitxml=" + report]
    if args[:2] == ["node", "--test"]:
        if any(arg.startswith("--test-reporter") for arg in args):
            raise ValueError("The executor controls the test reporter.")
        return ["node", "--test", "--test-reporter=tap", *args[2:]]
    raise ValueError("Test work must invoke python -m pytest or node --test directly.")


def test_result(args, output, report):
    if "pytest" in args[:4]:
        root = ET.parse(report).getroot()
        suites = list(root) if root.tag == "testsuites" else [root]
        tests = sum(int(row.get("tests", 0)) for row in suites)
        skipped = sum(int(row.get("skipped", 0)) for row in suites)
        failures = sum(
            int(row.get("failures", 0)) + int(row.get("errors", 0)) for row in suites
        )
        return {"executed": tests - skipped, "failed": failures, "runner": "pytest"}
    counts = {
        key: int(value)
        for key, value in re.findall(
            r"^# (tests|pass|fail) (\d+)\s*$", output, re.MULTILINE
        )
    }
    return {
        "executed": counts.get("pass", 0) + counts.get("fail", 0),
        "failed": counts.get("fail", 0),
        "runner": "node:test",
    }


def main():
    request = json.load(sys.stdin)
    workspace = Path("/workspace")
    marker = workspace / ".tauric-workspace"
    if request["purpose"] == "verify" and (request["script"] != "api_smoke" or not marker.is_file()):
        raise ValueError("Initialize the workspace first; verification only supports the fixed api_smoke profile.")
    if not marker.exists():
        for source in Path("/source").iterdir():
            target = workspace / source.name
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copyfile(source, target)
        subprocess.run(["git", "init", "-q", "/workspace"], check=True)
        subprocess.run(["git", "config", "user.name", "OpenClaw"], check=True)
        subprocess.run(
            ["git", "config", "user.email", "openclaw@localhost"], check=True
        )
        subprocess.run(["git", "add", "."], check=True)
        subprocess.run(["git", "commit", "-qm", "Verified release source"], check=True)
        marker.write_text("isolated development; no production authority\n")
    for package in (() if request["purpose"] == "verify" else ("frontend", "openclaw")):
        dependencies = workspace / package / "node_modules"
        if not dependencies.exists():
            dependencies.mkdir()
            for installed in Path(
                "/opt/engineering-deps/" + package + "/node_modules"
            ).iterdir():
                if installed.name not in {".cache", ".tmp"}:
                    (dependencies / installed.name).symlink_to(
                        installed, target_is_directory=installed.is_dir()
                    )
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "LANG": "C.UTF-8",
        "PYTHONPATH": "/workspace/backend",
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }
    if request["purpose"] == "verify":
        return verification_subject(workspace, env)
    report = "/tmp/tauric-tests.xml"
    args = (
        test_command(request["script"], report)
        if request["purpose"] == "test"
        else ["/bin/sh", "-c", request["script"]]
    )
    if request["purpose"] == "test" and "pytest" in args[:4]:
        test_database(workspace)
    with tempfile.TemporaryFile() as output:
        child = subprocess.Popen(
            args,
            cwd=workspace,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = child.wait(timeout=request["timeout_seconds"])
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            code = 124
        output.seek(0, 2)
        size = output.tell()
        output.seek(max(0, size - 30000))
        captured = output.read(30000).decode("utf-8", "replace")
        sys.stdout.write(captured)
        if size > 30000:
            sys.stdout.write("\n[Output truncated to the last 30 KB.]\n")
        if request["purpose"] == "test":
            try:
                result = test_result(args, captured, report)
            except (OSError, ValueError, ET.ParseError):
                result = {"executed": 0, "failed": 1, "runner": "unverified"}
            print("\n" + TEST_MARKER + json.dumps(result, sort_keys=True), flush=True)
            if result["executed"] < 1 or result["failed"]:
                code = code or 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
