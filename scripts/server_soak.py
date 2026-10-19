"""Observe an existing server; never start workloads, model calls or orders."""

import argparse
import json
import math
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import server

UTC = timezone.utc  # Native host tools support Python 3.10.


def observe(directory, project):
    compose = server.command(directory, project) + ["--profile", "*"]
    ids = (
        server.run(compose + ["ps", "--all", "-q"], stdout=subprocess.PIPE)
        .stdout.decode()
        .split()
    )
    if not ids:
        raise ValueError("There are no server containers to observe.")
    containers = json.loads(
        server.run(["docker", "inspect", *ids], stdout=subprocess.PIPE).stdout
    )
    states = {}
    active = []
    for container in containers:
        name = container["Config"]["Labels"]["com.docker.compose.service"]
        if name == "migrate":
            continue
        state = container["State"]
        states[name] = {
            "container_id": container["Id"],
            "running": state["Running"],
            "health": state.get("Health", {}).get("Status", "unavailable"),
            "oom_killed": state["OOMKilled"],
            "restart_count": container["RestartCount"],
        }
        if state["Running"]:
            active.append(container["Id"])
    metrics = []
    if active:
        raw = server.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", *active],
            stdout=subprocess.PIPE,
        ).stdout.decode()
        for line in raw.splitlines():
            row = json.loads(line)
            metrics.append(
                {
                    key: row.get(key)
                    for key in (
                        "Name",
                        "CPUPerc",
                        "MemUsage",
                        "MemPerc",
                        "NetIO",
                        "BlockIO",
                        "PIDs",
                    )
                }
            )
    return {
        "utc": datetime.now(UTC).isoformat(),
        "services": states,
        "metrics": metrics,
        "available_disk_bytes": shutil.disk_usage(directory).free,
        "healthy": all(
            row["running"] and row["health"] == "healthy" and not row["oom_killed"]
            for row in states.values()
        ),
    }


def monitor(
    directory,
    project,
    output,
    hours=24,
    interval=60,
    workload="unrecorded",
    local=False,
):
    if not math.isfinite(hours) or not 0 < hours <= 168 or not 1 <= interval <= 3600:
        raise ValueError("Choose 0–168 hours and a 1–3600 second sampling interval.")
    server.validate(directory, project, local)
    if output.exists():
        raise ValueError("Soak report exists and cannot be overwritten.")
    output.parent.mkdir(parents=True, exist_ok=True)
    server.private_write(output, "{}\n")
    started = time.monotonic()
    samples = []
    baseline = None
    result = {}
    while True:
        sample = observe(directory, project)
        sample_restarts = {
            name: (row["container_id"], row["restart_count"])
            for name, row in sample["services"].items()
        }
        baseline = baseline if baseline is not None else sample_restarts
        sample["no_unexpected_restarts"] = sample_restarts == baseline
        samples.append(sample)
        elapsed = time.monotonic() - started
        result = {
            "requested_hours": hours,
            "elapsed_seconds": round(elapsed, 3),
            "requested_duration_complete": elapsed >= hours * 3600,
            "workload_description": workload,
            "all_samples_healthy": all(
                s["healthy"] and s["no_unexpected_restarts"] for s in samples
            ),
            "samples": samples,
            "representative_workload_verified": False,
            "production_acceptance_complete": False,
        }
        temporary = output.with_suffix(output.suffix + ".next")
        server.private_write(temporary, json.dumps(result, indent=2) + "\n")
        temporary.replace(output)
        if result["requested_duration_complete"]:
            return result
        time.sleep(min(interval, max(0, hours * 3600 - elapsed)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir", type=Path, default=server.ROOT / ".env.server-state"
    )
    parser.add_argument("--project", default="tauric_server")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument(
        "--workload",
        required=True,
        help="Describe the configured representative universe, cadence and external inference route",
    )
    parser.add_argument("--local-images", action="store_true")
    args = parser.parse_args()
    result = monitor(
        args.state_dir.resolve(),
        args.project,
        args.output.resolve(),
        args.hours,
        args.interval,
        args.workload,
        args.local_images,
    )
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "samples"}, indent=2
        )
    )
    if not result["all_samples_healthy"]:
        parser.exit(
            1, "The observation contains unhealthy services or unexpected restarts.\n"
        )


if __name__ == "__main__":
    main()
