"""Credentials stay encrypted, redacted and effective across API/worker processes."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import data_providers, market
from tauric.app import app
from tauric.config import runtime_config
from tauric.credential_keys import stage_rotation
from tauric.models import MarketConnectionVersion

TOKEN = "fixture-oanda-token"
KEY = "fixture-alpaca-key"
SECRET = "fixture-alpaca-secret"


def change(provider="oanda", **values):
    return {
        "environment": "practice" if provider == "oanda" else "paper",
        "credential_action": "replace",
        "token": TOKEN if provider == "oanda" else "",
        "key": KEY if provider == "alpaca" else "",
        "secret": SECRET if provider == "alpaca" else "",
        "expected_revision": 0,
        **values,
    }


@pytest.fixture(autouse=True)
def empty_host(monkeypatch, tmp_path):
    path = tmp_path / "market-data.json"
    path.write_text("{}")
    monkeypatch.setenv("TAURIC_MARKET_CONNECTIONS_FILE", str(path))
    runtime_config.cache_clear()
    return path


def test_authentication_and_csrf(client):
    url = "/api/v1/market-connections/oanda"
    assert client.get("/api/v1/market-connections").status_code == 401
    assert client.put(url, json=change()).status_code == 401
    assert client.post(url + "/test", json={"expected_revision": 0}).status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert client.put(url, json=change()).status_code == 403
    assert client.post(url + "/test", json={"expected_revision": 0}).status_code == 403


def test_key_rotation_preserves_saved_broker_credentials_and_immutable_revisions(signed_in, database):
    url = "/api/v1/market-connections/oanda"
    assert signed_in.put(url, json=change()).status_code == 200
    with Session(database[0]) as db:
        old = db.scalar(select(MarketConnectionVersion))
        historical_id, historical_ciphertext = old.id, old.encrypted_config
    key_file = Path(runtime_config().model_key_file)
    staged = key_file.with_suffix(".next")
    stage_rotation(key_file, staged)
    staged.replace(key_file)
    with Session(database[0]) as db:
        assert data_providers.connections(db).oanda.token.get_secret_value() == TOKEN
    response = signed_in.put(url, json=change(expected_revision=1, credential_action="keep", token=""))
    assert response.status_code == 200 and response.json()["revision"] == 2
    with Session(database[0]) as db:
        assert db.get(MarketConnectionVersion, historical_id).encrypted_config == historical_ciphertext
        assert data_providers.connections(db).oanda.token.get_secret_value() == TOKEN


def test_encryption_redaction_immutable_history_and_worker_access(signed_in, database):
    for provider in ("oanda", "alpaca"):
        response = signed_in.put(f"/api/v1/market-connections/{provider}", json=change(provider))
        assert response.status_code == 200 and response.json()["configured"]
        assert response.json()["revision"] == 1
    for url in (
        "/api/v1/market-connections",
        "/api/v1/market-providers",
        "/api/v1/audit",
        "/api/v1/resources/market_connection/oanda",
        "/api/v1/resources/market_connection/alpaca",
    ):
        response = signed_in.get(url)
        assert response.status_code == 200
        assert all(secret not in response.text for secret in (TOKEN, KEY, SECRET))
    with Session(database[0]) as worker:
        config = data_providers.connections(worker)
        assert config.oanda.token.get_secret_value() == TOKEN
        assert config.alpaca.key.get_secret_value() == KEY
        assert config.alpaca.secret.get_secret_value() == SECRET
        records = worker.scalars(select(MarketConnectionVersion)).all()
        assert len(records) == 2
        assert all(
            secret not in row.encrypted_config for secret in (TOKEN, KEY, SECRET) for row in records
        )
    for sql in (
        "UPDATE market_connection_versions SET encrypted_config='changed'",
        "DELETE FROM market_connection_versions",
        "TRUNCATE market_connection_versions CASCADE",
    ):
        with pytest.raises(DBAPIError), database[1].begin() as admin:
            admin.execute(text(sql))


def test_keep_replace_clear_and_host_fallback(signed_in, database, empty_host):
    empty_host.write_text(json.dumps({"oanda": {"token": "older-host-token"}}))
    before = signed_in.get("/api/v1/market-connections").json()[0]
    assert before["source"] == "host" and before["configured"]
    url = "/api/v1/market-connections/oanda"
    assert signed_in.put(url, json=change()).status_code == 200
    assert (
        signed_in.put(
            url, json=change(expected_revision=1, credential_action="keep", token="")
        ).json()["revision"]
        == 2
    )
    assert signed_in.put(url, json=change(expected_revision=1)).status_code == 409
    assert (
        signed_in.put(
            url,
            json=change(
                expected_revision=2, environment="live", credential_action="keep", token=""
            ),
        ).status_code
        == 422
    )
    assert (
        signed_in.put(url, json=change(expected_revision=2, token="replacement-fixture")).json()[
            "revision"
        ]
        == 3
    )
    cleared = signed_in.put(
        url, json=change(expected_revision=3, credential_action="clear", token="")
    )
    assert cleared.status_code == 200 and not cleared.json()["configured"]
    assert cleared.json()["source"] == "settings"
    with Session(database[0]) as worker:
        assert not data_providers.connections(worker).oanda.token.get_secret_value()
    assert not signed_in.get("/api/v1/market-providers").json()[1]["configured"]


def test_alpaca_keep_changes_feed_without_reentering_credentials(signed_in, database):
    url = "/api/v1/market-connections/alpaca"
    assert signed_in.put(url, json=change("alpaca")).status_code == 200
    response = signed_in.put(
        url,
        json=change(
            "alpaca",
            expected_revision=1,
            credential_action="keep",
            key="",
            secret="",
            feed="sip",
            delay_minutes=0,
        ),
    )
    assert response.status_code == 200 and response.json()["feed"] == "sip"
    with Session(database[0]) as worker:
        value = data_providers.connections(worker).alpaca
        assert value.key.get_secret_value() == KEY and value.secret.get_secret_value() == SECRET
        assert value.delay_minutes == 0


def test_account_id_edit_keeps_token_and_older_requests_preserve_account(signed_in, database):
    url = "/api/v1/market-connections/oanda"
    account = "101-001-1234567-001"
    assert signed_in.put(url, json=change(account_id=account)).json()["account_id"] == account
    response = signed_in.put(
        url,
        json=change(
            expected_revision=1,
            credential_action="keep",
            token="",
            account_id="101-001-1234567-002",
        ),
    )
    assert response.status_code == 200 and response.json()["account_id"].endswith("002")
    legacy = signed_in.put(
        url, json=change(expected_revision=2, credential_action="keep", token="")
    )
    assert legacy.status_code == 200 and legacy.json()["account_id"].endswith("002")
    with Session(database[0]) as worker:
        assert data_providers.connections(worker).oanda.token.get_secret_value() == TOKEN
        record = worker.scalar(
            select(MarketConnectionVersion).order_by(MarketConnectionVersion.revision.desc())
        )
        assert "101-001-1234567-002" not in record.encrypted_config
    cleared = signed_in.put(
        url, json=change(expected_revision=3, credential_action="clear", token="")
    )
    assert cleared.status_code == 200 and cleared.json()["account_id"] == ""


@pytest.mark.parametrize(
    "account", ["../accounts", "101?token=secret", "bad-account", "1\n2", "-1", "1--2"]
)
def test_account_id_validation(signed_in, account):
    response = signed_in.put("/api/v1/market-connections/oanda", json=change(account_id=account))
    assert response.status_code == 422 and TOKEN not in response.text


@pytest.mark.parametrize(
    "changes",
    [
        {"token": ""},
        {"token": "fixture\nheader"},
        {"token": "x" * 4097},
        {"environment": "paper"},
        {"key": SECRET},
        {"credential_action": "keep"},
        {"delay_minutes": 16},
        {"expected_revision": -1},
    ],
)
def test_invalid_changes_are_redacted(signed_in, changes):
    response = signed_in.put("/api/v1/market-connections/oanda", json=change(**changes))
    assert response.status_code == 422
    assert (
        TOKEN not in response.text
        and SECRET not in response.text
        and "fixture\nheader" not in response.text
    )


def test_missing_encryption_key_and_saved_settings_do_not_fall_back(signed_in, monkeypatch):
    url = "/api/v1/market-connections/oanda"
    assert signed_in.put(url, json=change()).status_code == 200
    monkeypatch.setenv("TAURIC_MODEL_KEY_FILE", "/nonexistent/tauric-key")
    runtime_config.cache_clear()
    for url in ("/api/v1/market-connections", "/api/v1/market-providers"):
        assert signed_in.get(url).status_code == 503


def test_cannot_save_without_encryption_key(signed_in, monkeypatch, database):
    monkeypatch.setenv("TAURIC_MODEL_KEY_FILE", "/nonexistent/tauric-key")
    runtime_config.cache_clear()
    assert not signed_in.get("/api/v1/market-connections").json()[0]["storage_ready"]
    response = signed_in.put("/api/v1/market-connections/oanda", json=change())
    assert response.status_code == 503 and TOKEN not in response.text
    with Session(database[0]) as worker:
        assert not worker.scalars(select(MarketConnectionVersion)).all()


def test_concurrent_saves_conflict_instead_of_overwriting(signed_in):
    headers, cookies = dict(signed_in.headers), dict(signed_in.cookies)

    def save(_):
        with TestClient(app, headers=headers, cookies=cookies) as client:
            return client.put("/api/v1/market-connections/oanda", json=change()).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [200, 409]


def test_refresh_receives_saved_credentials_without_calling_provider(signed_in, monkeypatch):
    signed_in.put("/api/v1/market-connections/oanda", json=change())
    from fastapi import HTTPException

    def collect(spec, interval, *, config):
        assert config.oanda.token.get_secret_value() == TOKEN
        raise HTTPException(503, "Offline test: saved credentials reached the collector.")

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", collect)
    result = signed_in.post("/api/v1/market/oanda:fx:EUR_USD/refresh", json={"interval": "15m"})
    assert result.status_code == 503 and "reached the collector" in result.text
    assert TOKEN not in result.text


def test_connection_check_is_read_only_and_redacted(signed_in, database, monkeypatch):
    import httpx
    from test_market_adapters import dt, oanda_payload, transport

    from tauric.models import MarketSnapshot

    account = "101-001-1234567-001"
    signed_in.put("/api/v1/market-connections/oanda", json=change(account_id=account))
    paths = []

    def handle(request):
        assert request.method == "GET" and request.headers["Authorization"] == "Bearer " + TOKEN
        paths.append(request.url.path)
        if request.url.path == f"/v3/accounts/{account}/instruments":
            return httpx.Response(200, json={"instruments": [{"name": "EUR_USD"}]})
        assert request.url.path == "/v3/instruments/EUR_USD/candles"
        return httpx.Response(200, json=oanda_payload())

    transport(monkeypatch, handle)
    monkeypatch.setattr(data_providers, "now", lambda: dt("2026-10-03T12:00"))
    response = signed_in.post(
        "/api/v1/market-connections/oanda/test", json={"expected_revision": 1}
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["symbol"] == "EUR/USD" and result["account_checked"]
    assert result["quality"] == "verified" and result["candles"] == 3
    assert result["revision"] == 1 and len(paths) == 2
    assert TOKEN not in response.text and account not in response.text
    saved_view = signed_in.get("/api/v1/market-connections").json()[0]
    assert saved_view["last_check"] == result
    changed = signed_in.put(
        "/api/v1/market-connections/oanda",
        json=change(expected_revision=1, credential_action="keep", token=""),
    )
    assert changed.status_code == 200 and changed.json()["last_check"] is None
    with Session(database[0]) as worker:
        assert len(worker.scalars(select(MarketConnectionVersion)).all()) == 2
        assert not worker.scalars(select(MarketSnapshot)).all()
    activity = signed_in.get("/api/v1/audit").text
    assert (
        "market_connection.checked" in activity
        and TOKEN not in activity
        and account not in activity
    )


@pytest.mark.parametrize("failure", ["auth", "malformed"])
def test_connection_check_failures_are_sanitized_and_audited(signed_in, monkeypatch, failure):
    import httpx
    from test_market_adapters import oanda_payload, transport

    signed_in.put("/api/v1/market-connections/oanda", json=change())

    def handle(request):
        if failure == "auth":
            return httpx.Response(401, json={"error": TOKEN})
        payload = oanda_payload()
        payload["candles"][0]["mid"]["h"] = "0"
        return httpx.Response(200, json=payload)

    transport(monkeypatch, handle)
    response = signed_in.post(
        "/api/v1/market-connections/oanda/test", json={"expected_revision": 1}
    )
    assert response.status_code == (503 if failure == "auth" else 502)
    assert TOKEN not in response.text
    activity = signed_in.get("/api/v1/audit").text
    assert "market_connection.check_failed" in activity and TOKEN not in activity


def test_connection_check_requires_current_revision_before_provider_calls(signed_in, monkeypatch):
    signed_in.put("/api/v1/market-connections/oanda", json=change())

    def forbidden(*args, **kwargs):
        raise AssertionError("Stale checks must not contact providers")

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", forbidden)
    result = signed_in.post("/api/v1/market-connections/oanda/test", json={"expected_revision": 0})
    assert result.status_code == 409


def test_unconfigured_check_never_contacts_provider(signed_in, monkeypatch):
    from test_market_adapters import transport

    def forbidden(request):
        raise AssertionError("No credential means no outgoing request")

    transport(monkeypatch, forbidden)
    for provider in ("oanda", "alpaca"):
        response = signed_in.post(
            f"/api/v1/market-connections/{provider}/test", json={"expected_revision": 0}
        )
        assert response.status_code == 503
