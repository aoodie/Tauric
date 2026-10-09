"""Consume image-only pull tokens from SSH stdin; never save credentials."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def validate(payload, expected):
    if (
        not re.fullmatch(r"[a-f0-9]{40}", expected)
        or payload["revision"] != expected
        or len(payload["pulls"]) != 4
    ):
        raise ValueError(
            "A matching full release revision and four images are required."
        )
    seen = set()
    for item in payload["pulls"]:
        match = re.fullmatch(
            r"ghcr.io/aoodie/tauric-(api|worker|web|openclaw)@(sha256:[a-f0-9]{64})",
            item["image"],
        )
        if (
            not match
            or match[1] in seen
            or not isinstance(item.get("token"), str)
            or not 20 < len(item["token"]) < 16384
        ):
            raise ValueError("Invalid immutable image identity or pull credential.")
        seen.add(match[1])
        repository = item["image"].split("@", 1)[0].removeprefix("ghcr.io/")
        if item["scope"] != "repository:" + repository + ":pull":
            raise ValueError("Only an exact image repository pull scope is permitted.")
    if seen != {"api", "worker", "web", "openclaw"}:
        raise ValueError("The release image set is incomplete.")


def pull(payload, expected):
    validate(payload, expected)
    info = json.loads(
        subprocess.run(
            ["docker", "info", "--format", "{{json .}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    address = info["Containerd"]["Address"]
    namespace = info["Containerd"]["Namespaces"]["Containers"]
    assert (
        address
        in {
            "/run/containerd/containerd.sock",
            "/var/run/docker/containerd/containerd.sock",
        }
        and namespace == "moby"
    )
    output = {"revision": expected, "images": {}}
    seen = set()
    for item in payload["pulls"]:
        match = re.fullmatch(
            r"ghcr.io/aoodie/tauric-(api|worker|web|openclaw)@(sha256:[a-f0-9]{64})",
            item["image"],
        )
        assert match and match[1] not in seen
        seen.add(match[1])
        repository = item["image"].split("@", 1)[0]
        assert (
            item["scope"]
            == "repository:" + repository.removeprefix("ghcr.io/") + ":pull"
        )
        with tempfile.TemporaryDirectory(
            prefix="tauric-image-pull-", dir="/dev/shm"
        ) as temporary:
            hosts = Path(temporary) / "ghcr.io"
            hosts.mkdir(mode=0o700)
            config = (
                'server = "https://ghcr.io"\n[host."https://ghcr.io"]\ncapabilities = ["pull", "resolve"]\n[host."https://ghcr.io".header]\nAuthorization = ['
                + json.dumps("Bearer " + item["token"])
                + "]\n"
            )
            fd = os.open(
                hosts / "hosts.toml", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(fd, "w") as stream:
                stream.write(config)
            result = subprocess.run(
                [
                    "ctr",
                    "--address",
                    address,
                    "--namespace",
                    namespace,
                    "images",
                    "pull",
                    "--local",
                    "--hosts-dir",
                    temporary,
                    "--platform",
                    "linux/amd64",
                    item["image"],
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=300,
            )
            if result.returncode:
                terms = [
                    term
                    for term in ("401", "403", "unauthorized", "expired", "denied")
                    if term in (result.stdout + result.stderr).lower()
                ]
                raise RuntimeError(
                    "Image-only registry pull failed for "
                    + match[1]
                    + "; "
                    + ",".join(terms)
                )
        result = subprocess.run(
            ["docker", "image", "inspect", item["image"]],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0
        image = json.loads(result.stdout)[0]
        assert (
            image["Config"]["Labels"]["org.opencontainers.image.revision"] == expected
        )
        assert (
            image["Architecture"] == "amd64" and item["image"] in image["RepoDigests"]
        )
        output["images"][match[1]] = {
            "reference": item["image"],
            "id": image["Id"],
            "architecture": image["Architecture"],
        }
        print(json.dumps({"preloaded": match[1], "revision": expected}), flush=True)
    assert seen == {"api", "worker", "web", "openclaw"}
    output["persistent_credentials_saved"] = False
    output["ephemeral_ram_configuration_removed"] = True
    path = Path("/srv/private-backups") / (
        "trader-dashboard-preloaded-" + expected[:12] + ".json"
    )
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(output, stream, indent=2)
    print(json.dumps(output), flush=True)


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--revision", required=True)
        pull(json.load(sys.stdin), parser.parse_args().revision)
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except (OSError, ValueError, KeyError, AssertionError):
        print(
            "Image preload verification failed; credentials were not logged.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
