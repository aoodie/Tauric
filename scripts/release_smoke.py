"""Rehearse a release only in uniquely named disposable Docker projects."""

import argparse
import http.cookiejar
import json
import secrets
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import server
import server_soak


def redact_diagnostics(diagnostics, private, password=None):
    values = {password} if password else set()

    def collect(value, sensitive=False):
        if isinstance(value, str) and value and (sensitive or len(value) >= 12):
            values.add(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                collect(
                    item,
                    any(
                        word in key.lower()
                        for word in (
                            "password",
                            "token",
                            "secret",
                            "key",
                            "authorization",
                        )
                    ),
                )
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for path in private.iterdir():
        if path.is_file():
            value = path.read_text().strip()
            if value:
                values.add(value)
                try:
                    collect(json.loads(value))
                except json.JSONDecodeError:
                    pass
    for value in sorted(values, key=len, reverse=True):
        diagnostics = diagnostics.replace(quote(value, safe=""), "[private]")
        diagnostics = diagnostics.replace(value, "[private]")
    return diagnostics


def health_diagnostics(compose):
    container_ids = (
        subprocess.run(
            compose + ["ps", "--all", "--quiet"],
            capture_output=True,
            check=False,
            timeout=20,
        )
        .stdout.decode()
        .split()
    )
    if not container_ids:
        return ""
    output = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{.Name}} health={{json .State.Health}} oom={{json .State.OOMKilled}} memory={{.HostConfig.Memory}}",
            *container_ids,
        ],
        capture_output=True,
        check=False,
        timeout=20,
    ).stdout.decode(errors="replace")
    output += "\n" + subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_ids],
        capture_output=True,
        check=False,
        timeout=20,
    ).stdout.decode(errors="replace")
    return output


def request(opener, origin, path, body=None, method="GET", csrf=None, expected=200):
    headers = {"Origin": origin}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    req = urllib.request.Request(
        origin + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers=headers,
    )
    try:
        response = opener.open(req, timeout=10)
    except urllib.error.HTTPError as error:
        if error.code != expected:
            raise RuntimeError("Release HTTP acceptance check failed.") from None
        return None
    if response.status != expected:
        raise RuntimeError("Release HTTP acceptance check failed.")
    content = response.read()
    return (
        json.loads(content)
        if content and "application/json" in response.headers.get("Content-Type", "")
        else content
    )


def rehearse(images, output, local=False, ai=True, emit_failure_diagnostics=False):
    token = uuid4().hex[:12]
    project = "tauric_release_" + token
    restored_project = "tauric_restore_" + token
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="tauric-release-") as temporary:
        directory = Path(temporary)
        state, recovered = directory / "state", directory / "recovered"
        server.setup(state, images, port=port, local=local)
        result = server.preflight(state, project, local)
        result["tested_images"] = {
            key: value
            for key, value in server.read_env(images).items()
            if key in server.IMAGE_KEYS
        }
        compose = server.command(state, project) + ["--profile", "execution"]
        if ai:
            compose += ["--profile", "analysis", "--profile", "governance"]
        recovery_compose = None
        startup_diagnostics = ""
        password = None
        try:
            startup = subprocess.run(
                compose + ["up", "-d", "--wait", "--wait-timeout", "240"],
                capture_output=True,
                check=False,
            )
            startup_diagnostics = (startup.stdout + startup.stderr).decode(
                errors="replace"
            )
            if startup.returncode:
                raise RuntimeError(
                    "Release startup failed; redacted diagnostics saved."
                )
            if emit_failure_diagnostics:
                result["startup_readiness_diagnostics"] = redact_diagnostics(
                    health_diagnostics(compose), state / "secrets"
                )
            result["fresh_install_ready"] = True
            server.run(
                compose
                + [
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "-c",
                    "import importlib.util; from pathlib import Path; assert importlib.util.find_spec('pytest') is None; assert importlib.util.find_spec('ruff') is None; assert not Path('/app/tests').exists()",
                ],
                stdout=subprocess.PIPE,
            )
            result["test_tools_excluded"] = True
            # Use the real owner bootstrap with an ephemeral password through
            # stdin, never a command argument or persisted fixture credential.
            bootstrap = "import getpass,json,sys; data=json.load(sys.stdin); getpass.getpass=lambda prompt:data['password']; sys.argv=['tauric','create-owner','--username','release-owner']; from tauric.cli import main; main()"
            password = secrets.token_urlsafe(32)
            server.run(
                compose
                + ["run", "--rm", "--no-deps", "-T", "api", "python", "-c", bootstrap],
                input=json.dumps({"password": password}).encode(),
                stdout=subprocess.PIPE,
            )
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
            )
            origin = f"http://localhost:{port}"
            request(opener, origin, "/health/ready")
            request(opener, origin, "/internal/openclaw/runtime-config", expected=404)
            request(opener, origin, "/api/v1/settings", expected=401)
            session = request(
                opener,
                origin,
                "/api/v1/session",
                {"username": "release-owner", "password": password},
                "POST",
            )
            csrf = session["csrf_token"]
            changed = request(
                opener,
                origin,
                "/api/v1/settings/ui.density",
                {
                    "scope": "user",
                    "value": "comfortable",
                    "expected_revision": 0,
                    "reason": "Isolated release persistence check",
                },
                "PUT",
                csrf,
            )
            assert changed["effective_value"] == "comfortable"
            account = request(
                opener,
                origin,
                "/api/v1/paper/accounts",
                {
                    "request_id": str(uuid4()),
                    "reason": "Isolated simulated release account",
                    "name": "Release paper account",
                    "currency": "USDT",
                    "starting_balance": "10000",
                    "policy": {
                        "risk_percent": "1",
                        "max_total_risk_percent": "3",
                        "max_exposure_percent": "80",
                        "max_positions": 5,
                        "fee_bps": "10",
                        "slippage_bps": "5",
                        "quote_max_age_seconds": 60,
                        "decision_ttl_seconds": 300,
                    },
                },
                "POST",
                csrf,
                expected=201,
            )
            assert account["id"]
            result["owner_login_and_saved_configuration"] = True
            result["simulated_account_created"] = True
            server.run(compose + ["restart", "api"], stdout=subprocess.PIPE)
            deadline = time.monotonic() + 60
            while True:
                try:
                    request(opener, origin, "/health/ready")
                    break
                except (RuntimeError, OSError):
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(1)
            settings = request(opener, origin, "/api/v1/settings")
            assert (
                next(
                    row for row in settings if row["definition"]["key"] == "ui.density"
                )["effective_value"]
                == "comfortable"
            )
            result["api_restart_persistence"] = True
            backup = directory / "backup.tar.gz"
            result["backup"] = server.backup(state, project, backup, local)
            result["restore"] = server.restore_check(
                recovered, restored_project, backup, local
            )
            recovery_compose = server.command(recovered, restored_project) + [
                "--profile",
                "*",
            ]
            # Restart the whole isolated stack using the same persistent volumes.
            server.run(compose + ["stop"], stdout=subprocess.PIPE)
            server.run(
                compose + ["up", "-d", "--wait", "--wait-timeout", "240"],
                stdout=subprocess.PIPE,
            )
            request(opener, origin, "/api/v1/session")
            result["whole_stack_restart_persistence"] = True
            observation = server_soak.monitor(
                state,
                project,
                directory / "short-soak.json",
                hours=20 / 3600,
                interval=5,
                workload="Isolated startup/account/settings/restart fixture; no analysis or broker workload",
                local=local,
            )
            assert observation["all_samples_healthy"]
            result["short_resource_observation"] = {
                "elapsed_seconds": observation["elapsed_seconds"],
                "samples": len(observation["samples"]),
                "all_samples_healthy": True,
                "representative_24_hour_soak_verified": False,
            }
            result.update(
                model_calls=0,
                broker_calls=0,
                host_reboot_verified=False,
                connected_automatic_cycle_verified=False,
                production_acceptance_complete=False,
            )
        except Exception as error:
            diagnostics = (
                startup_diagnostics
                + "\n"
                + getattr(error, "diagnostics", "")
                + "\n"
                + subprocess.run(
                    compose + ["logs", "--no-color", "--tail", "50"],
                    capture_output=True,
                    check=False,
                ).stdout.decode(errors="replace")
            )
            diagnostics += "\n" + subprocess.run(
                compose + ["ps", "--all", "--format", "json"],
                capture_output=True,
                check=False,
            ).stdout.decode(errors="replace")
            try:
                diagnostics += "\n" + health_diagnostics(compose)
            except subprocess.TimeoutExpired:
                diagnostics += "\nReadiness diagnostics timed out."
            if (recovered / "server.env").exists():
                recovered_compose = server.command(recovered, restored_project) + [
                    "--profile",
                    "*",
                ]
                diagnostics += "\nRestoration project:\n" + subprocess.run(
                    recovered_compose + ["logs", "--no-color", "--tail", "50"],
                    capture_output=True,
                    check=False,
                    timeout=20,
                ).stdout.decode(errors="replace")
            diagnostics = redact_diagnostics(diagnostics, state / "secrets", password)
            output.parent.mkdir(parents=True, exist_ok=True)
            failure = output.with_suffix(".failure.log")
            failure.write_text(diagnostics)
            failure.chmod(0o600)
            if emit_failure_diagnostics:
                print("Redacted disposable release diagnostics:\n" + diagnostics)
            raise
        finally:
            # These names are random and created by this invocation, never the
            # workstation or a user-selected project. Cleanup even on failure.
            for cleanup in (
                compose,
                recovery_compose
                or (
                    server.command(recovered, restored_project) + ["--profile", "*"]
                    if (recovered / "server.env").exists()
                    else None
                ),
            ):
                if cleanup:
                    server.run(
                        cleanup + ["down", "--volumes", "--remove-orphans"],
                        stdout=subprocess.PIPE,
                    )
        result["disposable_projects_removed"] = True
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2) + "\n")
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--local-images", action="store_true")
    parser.add_argument("--skip-ai", action="store_true")
    parser.add_argument("--emit-failure-diagnostics", action="store_true")
    args = parser.parse_args()
    print(
        json.dumps(
            rehearse(
                args.images,
                args.output,
                args.local_images,
                not args.skip_ai,
                args.emit_failure_diagnostics,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
