"""Create an isolated browser-test database and private temporary credentials."""

import base64
import json
import os
import secrets
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
target = Path(
    os.environ.get("TAURIC_BROWSER_FIXTURE", root / "artifacts/browser-test.json")
)
key_file = (
    target.with_suffix(".key")
    if "TAURIC_BROWSER_FIXTURE" in os.environ
    else root / "artifacts/browser-model-key"
)
if target.exists():
    raise SystemExit(
        "A browser fixture already exists; preserve it or clean it up first."
    )
sys.path.insert(0, str(root / "backend"))
from tauric.cli import main
from tauric.config import runtime_config
from tauric.database import engine

values = dict(
    line.split("=", 1)
    for line in (root / ".env").read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)
name = "tauric_browser_" + uuid4().hex[:12]
admin_url = make_url(
    f"postgresql+psycopg://tauric_admin:{values['POSTGRES_PASSWORD']}@127.0.0.1:{values.get('DB_PORT', '54330')}/postgres"
)
administrator = create_engine(admin_url, isolation_level="AUTOCOMMIT")
with administrator.connect() as connection:
    connection.execute(text(f'CREATE DATABASE "{name}"'))
os.environ["TAURIC_DATABASE_URL"] = admin_url.set(database=name).render_as_string(
    hide_password=False
)
runtime_config.cache_clear()
config = Config(str(root / "backend/alembic.ini"))
config.set_main_option("script_location", str(root / "backend/migrations"))
command.upgrade(config, "head")
url = admin_url.set(
    database=name, username="tauric_app", password=values["TAURIC_DB_PASSWORD"]
)
password = secrets.token_urlsafe(24)
os.environ["TAURIC_DATABASE_URL"] = url.render_as_string(hide_password=False)
runtime_config.cache_clear()
with (
    patch("sys.argv", ["tauric", "create-owner", "--username", "browser-owner"]),
    patch("getpass.getpass", return_value=password),
):
    main()
key_fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(key_fd, "wb") as handle:
    handle.write(base64.urlsafe_b64encode(secrets.token_bytes(32)))
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as handle:
    json.dump(
        {
            "database_name": name,
            "database_url": url.render_as_string(hide_password=False),
            "username": "browser-owner",
            "password": password,
        },
        handle,
    )
print(
    "Created isolated browser test database; credentials are in an ignored private file."
)
engine().dispose()
administrator.dispose()
