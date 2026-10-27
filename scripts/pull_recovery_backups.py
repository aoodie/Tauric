"""Pull and authenticate encrypted Oracle checkpoints into independent private storage."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import backup_vault

UTC = timezone.utc
REMOTE = "/srv/private-backups/scheduled/"
INVENTORY = '''import pathlib,json,hashlib
p=pathlib.Path("/srv/private-backups/scheduled")
rows=[]
for r in sorted(p.glob("scheduled-????-??-??.json")):
 d=json.loads(r.read_text())
 if d.get("phase")!="verified":continue
 files=[]
 for suffix,field in ((".tar.gz.vault","encryption"),("-research.tar.gz.vault","research_encryption")):
  f=p/("scheduled-"+d["day"]+suffix)
  h=hashlib.sha256()
  with f.open("rb") as stream:
   for b in iter(lambda:stream.read(1048576),b""):h.update(b)
  files.append({"name":f.name,"bytes":f.stat().st_size,"sha256":h.hexdigest(),"recovery":{k:d[field][k] for k in ("plaintext_bytes","plaintext_sha256")}})
 rows.append({"day":d["day"],"files":files})
print(json.dumps(rows))
'''


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def private_save(path, value):
    if path.is_symlink():
        raise ValueError("Refusing a linked checkpoint receipt.")
    pending = path.with_name(path.name + ".pending")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    pending.replace(path)


def private_directory(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink() or path.stat().st_uid != os.getuid():
        raise ValueError("Use an owned real private backup directory.")
    path.chmod(0o700)


def ssh(config, code, output=subprocess.PIPE):
    if not re.fullmatch(r"[a-zA-Z0-9_.-]+@[a-zA-Z0-9.-]+", config["host"]):
        raise ValueError("Use a fixed SSH user and hostname.")
    command = ["ssh", "-i", config["identity"], "-o", "StrictHostKeyChecking=yes",
               "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=15",
               "-o", "ServerAliveCountMax=3", config["host"], "sudo -n python3 -"]
    result = subprocess.run(command, input=code.encode(), stdout=output,
                            stderr=subprocess.PIPE, timeout=600, check=False)
    if result.returncode:
        raise RuntimeError("The pinned-host backup read or receipt handoff failed.")
    return result.stdout


def validate_inventory(row):
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["day"]):
        raise ValueError("Invalid checkpoint date.")
    expected = {"scheduled-" + row["day"] + s for s in (".tar.gz.vault", "-research.tar.gz.vault")}
    if len(row["files"]) != 2 or {f["name"] for f in row["files"]} != expected:
        raise ValueError("The complete encrypted checkpoint pair is required.")
    for item in row["files"]:
        if (type(item["bytes"]) is not int or not 28 <= item["bytes"] <= 10 * 1024**3
                or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                or not re.fullmatch(r"[a-f0-9]{64}", item["recovery"]["plaintext_sha256"])):
            raise ValueError("Invalid encrypted checkpoint identity.")


def receive(config, item, destination, key):
    path = destination / item["name"]
    if path.is_symlink():
        raise ValueError("Refusing a linked backup destination.")
    if not path.exists():
        pending = path.with_name(path.name + ".pending")
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        code = "import pathlib,sys,shutil; shutil.copyfileobj(pathlib.Path(" + repr(REMOTE + item["name"]) + ").open('rb'),sys.stdout.buffer)"
        with os.fdopen(fd, "wb") as output:
            ssh(config, code, output)
            output.flush()
            os.fsync(output.fileno())
        if pending.stat().st_size != item["bytes"] or checksum(pending) != item["sha256"]:
            raise ValueError("The transferred encrypted checkpoint does not match its original.")
        pending.replace(path)
    if path.stat().st_size != item["bytes"] or checksum(path) != item["sha256"]:
        raise ValueError("An existing off-host archive changed; it was not overwritten.")
    if backup_vault.recover(path, key) != item["recovery"]:
        raise ValueError("Off-host authentication/recovery differs from the original checkpoint.")


def acknowledge(config, row, receipt):
    # This call is authenticated by the owner's pinned-host SSH identity. Only
    # the existing checkpoint's copy receipt changes; no service/config is changed.
    code = '''import pathlib,json,hashlib
row=ROW
receipt=RECEIPT
p=pathlib.Path("/srv/private-backups/scheduled")
record=p/("scheduled-"+row["day"]+".json")
d=json.loads(record.read_text())
if d.get("phase")!="verified":raise ValueError("Checkpoint is no longer verified")
for f in row["files"]:
 h=hashlib.sha256()
 with (p/f["name"]).open("rb") as s:
  for b in iter(lambda:s.read(1048576),b""):h.update(b)
 if h.hexdigest()!=f["sha256"]:raise ValueError("Original checkpoint changed")
d.update(offhost_copy_verified=True,offhost_receipt=receipt)
t=record.with_suffix(".offhost-pending")
t.write_text(json.dumps(d,indent=2)+"\\n");t.chmod(0o600);t.replace(record)
'''.replace("ROW", repr(row)).replace("RECEIPT", repr(receipt))
    ssh(config, code)


def pull(config):
    destination, key_path = Path(config["destination"]), Path(config["key_file"])
    private_directory(destination)
    private_directory(key_path.parent)
    if not key_path.exists():
        fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as output:
            ssh(config, "import pathlib,sys;sys.stdout.buffer.write(pathlib.Path('/srv/tauric-private/secrets/backup-key').read_bytes())", output)
    key = backup_vault.key_material(key_path)
    inventory = json.loads(ssh(config, INVENTORY))
    receipts = []
    for row in inventory:
        validate_inventory(row)
        for item in row["files"]:
            receive(config, item, destination, key)
        receipt_path = destination / ("scheduled-" + row["day"] + "-receipt.json")
        receipt = {"day": row["day"], "destination": str(destination), "verified_at": datetime.now(UTC).isoformat(),
                   "files": row["files"], "authenticated_recovery_verified": True,
                   "plaintext_written": False, "key_stored_separately": True}
        if receipt_path.is_symlink():
            raise ValueError("Refusing a linked off-host receipt.")
        pending = receipt_path.with_suffix(".pending")
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as output:
            json.dump(receipt, output, indent=2)
        pending.replace(receipt_path)
        acknowledge(config, row, receipt)
        receipts.append(receipt_path.name)
    return {"verified_checkpoints": receipts, "destination": str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if args.config.is_symlink() or args.config.stat().st_mode & 0o077:
        raise ValueError("Use a private regular backup-pull configuration.")
    print(json.dumps(pull(json.loads(args.config.read_text())), indent=2))


if __name__ == "__main__":
    main()
