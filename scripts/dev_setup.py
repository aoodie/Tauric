"""Create local development secrets without printing or overwriting them."""

import base64
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
try:
    market_fd = os.open(
        root / ".env.market-data.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
except FileExistsError:
    print("Existing private market-data connections were preserved.")
else:
    with os.fdopen(market_fd, "w") as handle:
        handle.write((root / "deploy/market-data.example.json").read_text())
    print(
        "Created empty private FX/stock data connections. No broker orders are enabled."
    )
target = root / ".env"
try:
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
except FileExistsError:
    fd = None
if fd is not None:
    with os.fdopen(fd, "w") as handle:
        handle.write("POSTGRES_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
        handle.write("TAURIC_DB_PASSWORD=" + secrets.token_urlsafe(32) + "\n")
        handle.write(
            'DB_PORT=54330\nWEB_PORT=8080\nTAURIC_ALLOWED_ORIGINS=["http://localhost:8080","http://127.0.0.1:8080"]\n'
        )
    print("Created private development configuration. No credentials were printed.")
else:
    print("Existing development configuration was preserved.")
try:
    profile_fd = os.open(
        root / ".env.models.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
except FileExistsError:
    print("Existing private model profiles were preserved.")
else:
    with os.fdopen(profile_fd, "w") as handle:
        handle.write((root / "deploy/model-profiles.example.json").read_text())
    print("Created empty cloud/local model profiles. No model calls are enabled.")
try:
    key_fd = os.open(
        root / ".env.model-key", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
except FileExistsError:
    print("Existing model encryption key was preserved.")
else:
    with os.fdopen(key_fd, "wb") as handle:
        handle.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    print(
        "Created private encryption key for AI settings. Keep it with protected backups."
    )

try:
    service_fd = os.open(
        root / ".env.openclaw-private.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
except FileExistsError:
    print("Existing private OpenClaw service tokens were preserved.")
else:
    import json

    with os.fdopen(service_fd, "w") as handle:
        json.dump(
            {
                "gateway_token": secrets.token_urlsafe(48),
                "platform_token": secrets.token_urlsafe(48),
            },
            handle,
        )
    print("Created private OpenClaw service tokens. No credentials were printed.")
