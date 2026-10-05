"""Remove only the disposable database named in the private browser-test fixture."""

import json
import os
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

root = Path(__file__).resolve().parents[1]
target = Path(
    os.environ.get("TAURIC_BROWSER_FIXTURE", root / "artifacts/browser-test.json")
)
if not target.exists():
    raise SystemExit("No browser-test fixture to remove.")
fixture = json.loads(target.read_text())
name = fixture["database_name"]
if not re.fullmatch(r"tauric_browser_[0-9a-f]{12}", name):
    raise SystemExit("Refusing to remove an unexpected database.")
values = dict(
    line.split("=", 1)
    for line in (root / ".env").read_text().splitlines()
    if line and not line.startswith("#") and "=" in line
)
url = make_url(fixture["database_url"]).set(
    database="postgres", username="tauric_admin", password=values["POSTGRES_PASSWORD"]
)
administrator = create_engine(url, isolation_level="AUTOCOMMIT")
with administrator.connect() as connection:
    connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
administrator.dispose()
target.unlink()
key_file = (
    target.with_suffix(".key")
    if "TAURIC_BROWSER_FIXTURE" in os.environ
    else root / "artifacts/browser-model-key"
)
key_file.unlink(missing_ok=True)
print("Removed browser-test database and temporary credentials; screenshots retained.")
