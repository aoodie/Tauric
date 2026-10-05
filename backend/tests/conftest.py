from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from tauric.auth import hasher
from tauric.config import runtime_config
from tauric.database import engine
from tauric.models import Owner, identifier
from tauric.services import create_workspace


@pytest.fixture
def database(monkeypatch, tmp_path):
    # Tests use a disposable database; they never delete application data.
    root = Path(__file__).resolve().parents[2]
    key_file = tmp_path / "model-key"
    key_file.write_bytes(Fernet.generate_key())
    monkeypatch.setenv("TAURIC_MODEL_KEY_FILE", str(key_file))
    values = dict(
        line.split("=", 1)
        for line in (root / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    name = "tauric_test_" + uuid4().hex[:16]
    url = make_url(
        f"postgresql+psycopg://tauric_admin:{values['POSTGRES_PASSWORD']}@127.0.0.1:{values.get('DB_PORT', '54330')}/postgres"
    )
    administrator = create_engine(url, isolation_level="AUTOCOMMIT")
    with administrator.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    admin_url = url.set(database=name)
    app_url = admin_url.set(username="tauric_app", password=values["TAURIC_DB_PASSWORD"])
    monkeypatch.setenv("TAURIC_DATABASE_URL", admin_url.render_as_string(hide_password=False))
    runtime_config.cache_clear()
    config = Config(str(root / "backend/alembic.ini"))
    config.set_main_option("script_location", str(root / "backend/migrations"))
    command.upgrade(config, "head")
    test_admin = create_engine(admin_url)
    with test_admin.begin() as connection:
        connection.execute(text(f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC'))
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{name}" TO tauric_app'))
    monkeypatch.setenv("TAURIC_DATABASE_URL", app_url.render_as_string(hide_password=False))
    monkeypatch.setenv("TAURIC_SECURE_COOKIES", "false")
    monkeypatch.setenv("TAURIC_ALLOWED_ORIGINS", '["http://testserver"]')
    runtime_config.cache_clear()
    engine.cache_clear()
    application_engine = engine()
    with Session(application_engine) as db:
        owner = Owner(
            id=identifier(),
            username="test-owner",
            password_hash=hasher.hash("Test-only-password-2026"),
        )
        db.add(owner)
        db.flush()
        create_workspace(db, owner, "Trading")
    yield application_engine, test_admin
    application_engine.dispose()
    test_admin.dispose()
    engine.cache_clear()
    runtime_config.cache_clear()
    with administrator.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    administrator.dispose()


@pytest.fixture
def client(database):
    from tauric.app import app

    with TestClient(app, headers={"Origin": "http://testserver"}) as client:
        yield client


@pytest.fixture
def signed_in(client):
    response = client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client
