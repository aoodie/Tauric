"""Install a private, persistent 2 GiB host swap buffer without changing Tauric.

Run only through the trusted operator recovery path. Container limits, financial
settings, application images, fstab and kernel tuning remain unchanged. An
uncertain result must be reconciled with --check, never by recreating the file.
"""

import argparse
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path("/var/lib/tauric-host-recovery")
UNIT_ROOT = Path("/etc/systemd/system")
SIZE = 2 * 1024**3
DISK_RESERVE = 10 * 1024**3


def command(args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=60
    ).stdout.strip()


def parse_swaps(value):
    rows = []
    for line in value.splitlines()[1:]:
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) != 5:
            raise ValueError("Unexpected kernel swap record; reconcile it.")
        rows.append(
            {
                "name": fields[0],
                "size": int(fields[2]) * 1024,
                "used": int(fields[3]) * 1024,
            }
        )
    return rows


def swaps():
    return parse_swaps(Path("/proc/swaps").read_text())


def unit_name(path):
    return command(["systemd-escape", "--path", "--suffix=swap", str(path)])


def unit_text(path):
    return (
        "[Unit]\nDescription=Tauric host memory buffer\n\n"
        f"[Swap]\nWhat={path}\nOptions=pri=10\n\n"
        "[Install]\nWantedBy=swap.target\n"
    )


def validate_file(path, size=SIZE, owner=0):
    value = path.lstat()
    if (
        not stat.S_ISREG(value.st_mode)
        or value.st_uid != owner
        or stat.S_IMODE(value.st_mode) != 0o600
        or value.st_nlink != 1
        or value.st_size != size
        or value.st_blocks * 512 < size
    ):
        raise ValueError("Existing memory buffer metadata is unsafe; reconcile it.")
    if command(["blkid", "-p", "-s", "TYPE", "-o", "value", str(path)]) != "swap":
        raise ValueError(
            "Existing file is not a verified swap buffer; do not format it."
        )


def ensure_unit(path, text):
    if path.is_symlink():
        raise ValueError("A symlink cannot supply the memory-buffer unit.")
    if path.exists():
        if path.read_text() != text:
            raise ValueError("Existing memory-buffer unit differs; preserve it.")
        return
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644
    )
    with os.fdopen(descriptor, "w") as output:
        output.write(text)
        output.flush()
        os.fsync(output.fileno())


def check(root=ROOT):
    path = root / "swapfile"
    active = next((item for item in swaps() if item["name"] == str(path)), None)
    name = unit_name(path)
    states = {
        state: subprocess.run(
            ["systemctl", "--quiet", state, name],
            check=False,
            capture_output=True,
            timeout=15,
        ).returncode
        == 0
        for state in ("is-active", "is-enabled")
    }
    if active:
        validate_file(path)
        if not SIZE - 65536 <= int(active["size"]) <= SIZE:
            raise ValueError("The active buffer has an unexpected capacity.")
    return {"path": str(path), "unit": name, "active": active, **states}


def apply(root=ROOT, unit_root=UNIT_ROOT):
    if os.geteuid() != 0:
        raise ValueError("Host memory recovery requires the trusted root operator.")
    if root.is_symlink():
        raise ValueError("Memory-buffer directory must not be a symlink.")
    path = root / "swapfile"
    existing = swaps()
    if existing and not any(item["name"] == str(path) for item in existing):
        raise ValueError("An existing host swap configuration needs reconciliation.")
    if command(["findmnt", "-no", "FSTYPE", "--target", str(root.parent)]) != "ext4":
        raise ValueError("This recovery path requires the verified ext4 filesystem.")
    required = DISK_RESERVE + (0 if path.exists() else SIZE)
    if shutil.disk_usage(root.parent).free < required:
        raise ValueError("Host disk reserve is insufficient for this memory buffer.")
    root.mkdir(mode=0o700, exist_ok=True)
    value = root.lstat()
    if value.st_uid != 0 or stat.S_IMODE(value.st_mode) != 0o700:
        raise ValueError("Memory-buffer directory must be private and root-owned.")
    if path.is_symlink():
        raise ValueError("A symlink cannot supply the memory buffer.")
    if path.exists():
        validate_file(path)  # Never truncate or reformat an existing file.
    else:
        descriptor = os.open(
            path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        try:
            os.posix_fallocate(descriptor, 0, SIZE)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        command(["mkswap", "--label", "TAURIC_RAM_BUFFER", str(path)])
        validate_file(path)
    name = unit_name(path)
    ensure_unit(unit_root / name, unit_text(path))
    command(["systemd-analyze", "verify", str(unit_root / name)])
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "enable", "--now", name])
    result = check(root)
    if not (result["active"] and result["is-active"] and result["is-enabled"]):
        raise RuntimeError(
            "Activation is incomplete; reconcile --check before retrying."
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["apply", "check"])
    args = parser.parse_args()
    result = apply() if args.action == "apply" else check()
    print(json.dumps(result, sort_keys=True))
    if args.action == "check" and not (
        result["active"] and result["is-active"] and result["is-enabled"]
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
