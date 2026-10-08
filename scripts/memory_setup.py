"""Create local memory credentials without printing them or changing chat routes."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HONCHO_REF = "06ed1929cf017c333a87c41d130bb6a0605d91c0"


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as file:
        file.write(text)


def encoded(value):
    return (
        base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode())
        .decode()
        .rstrip("=")
    )


def main():
    source = ROOT / ".runtime-honcho"
    ref = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if ref != HONCHO_REF:
        raise SystemExit("Honcho source does not match the reviewed version pin.")
    env = ROOT / ".env.honcho"
    if not env.exists():
        secret, password = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        private_write(
            env,
            f"HONCHO_DB_PASSWORD={password}\nAUTH_USE_AUTH=true\nAUTH_JWT_SECRET={secret}\nLLM_OPENAI_API_KEY=local-only\n",
        )
    else:
        values = dict(
            line.split("=", 1) for line in env.read_text().splitlines() if "=" in line
        )
        secret = values["AUTH_JWT_SECRET"]
    config = ROOT / ".env.memory-private.json"
    if not config.exists():
        token = (
            encoded({"alg": "HS256", "typ": "JWT"})
            + "."
            + encoded({"t": "", "ad": True})
        )
        token += "." + base64.urlsafe_b64encode(
            hmac.new(secret.encode(), token.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        private_write(
            config,
            json.dumps(
                {
                    "url": "http://honcho:8000",
                    "token": token,
                    "learning_enabled": True,
                }
            )
            + "\n",
        )
    private = ROOT / ".env.knowledge"
    private.mkdir(mode=0o700, exist_ok=True)
    os.chmod(private, 0o700)
    (private / "Obsidian").mkdir(mode=0o755, exist_ok=True)
    print(
        "Local Honcho credentials and private Obsidian folder are ready. Existing chat routes were preserved."
    )


if __name__ == "__main__":
    main()
