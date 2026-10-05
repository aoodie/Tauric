from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric.app import app
from tauric.config import runtime_config
from tauric.model_profiles import load_profiles
from tauric.models import ModelConnectionVersion

OLD = "Test-only-password-2026"
NEW = "Changed-test-password-2026"
SECRET = "fixture-secret-not-a-real-key"


def connection(**changes):
    return {
        "provider": "openai",
        "quick_model": "offline-q",
        "deep_model": "offline-d",
        "expected_revision": 0,
        "key_action": "replace",
        "api_key": SECRET,
        **changes,
    }


def password(**changes):
    return {"current_password": OLD, "new_password": NEW, "confirm_password": NEW, **changes}


def test_connection_routes_and_password_require_owner_and_csrf(client):
    assert client.get("/api/v1/model-connections").status_code == 401
    assert client.put("/api/v1/model-connections/cloud", json=connection()).status_code == 401
    assert client.post("/api/v1/owner/password", json=password()).status_code == 401
    client.post("/api/v1/session", json={"username": "test-owner", "password": OLD})
    assert client.put("/api/v1/model-connections/cloud", json=connection()).status_code == 403
    assert client.post("/api/v1/owner/password", json=password()).status_code == 403


def test_saved_connection_encrypted_shared_with_worker_and_never_returns_key(signed_in, database):
    result = signed_in.put("/api/v1/model-connections/cloud", json=connection())
    assert result.status_code == 200
    assert result.json()["has_key"] and result.json()["configured"]
    assert result.json()["revision"] == 1 and SECRET not in result.text
    assert SECRET not in signed_in.get("/api/v1/model-connections").text
    assert SECRET not in signed_in.get("/api/v1/analysis/status").text
    assert SECRET not in signed_in.get("/api/v1/audit").text
    details = signed_in.get("/api/v1/resources/model_connection/cloud")
    assert details.status_code == 200 and SECRET not in details.text
    with Session(database[0]) as worker:
        profiles, error = load_profiles(worker)
        assert not error and profiles["cloud"].api_key.get_secret_value() == SECRET
        record = worker.scalar(select(ModelConnectionVersion))
        assert SECRET not in record.encrypted_config
        assert "offline-q" not in record.encrypted_config
    for sql in [
        "UPDATE model_connection_versions SET encrypted_config='changed'",
        "DELETE FROM model_connection_versions",
        "TRUNCATE model_connection_versions CASCADE",
    ]:
        with pytest.raises(DBAPIError), database[1].begin() as admin:
            admin.execute(text(sql))


def test_keep_replace_clear_and_stale_connection(signed_in, database):
    url = "/api/v1/model-connections/cloud"
    assert signed_in.put(url, json=connection()).status_code == 200
    assert (
        signed_in.put(
            url,
            json=connection(
                expected_revision=1, key_action="keep", api_key="", quick_model="other-q"
            ),
        ).json()["revision"]
        == 2
    )
    assert signed_in.put(url, json=connection(expected_revision=1)).status_code == 409
    assert (
        signed_in.put(
            url,
            json=connection(
                expected_revision=2, key_action="keep", api_key="", provider="anthropic"
            ),
        ).status_code
        == 422
    )
    assert (
        signed_in.put(
            url, json=connection(expected_revision=2, api_key="new-fixture-secret")
        ).json()["revision"]
        == 3
    )
    cleared = signed_in.put(
        url, json=connection(expected_revision=3, key_action="clear", api_key="")
    )
    assert cleared.status_code == 200
    assert not cleared.json()["has_key"] and not cleared.json()["configured"]
    with Session(database[0]) as db:
        assert load_profiles(db)[0]["cloud"].api_key.get_secret_value() == ""
        assert len(db.scalars(select(ModelConnectionVersion)).all()) == 4


def test_invalid_endpoint_and_missing_encryption_key_never_save_secrets(
    signed_in, database, monkeypatch
):
    url = "/api/v1/model-connections/local"
    response = signed_in.put(
        url,
        json=connection(provider="openai_compatible", base_url="http://user:secret@localhost:9/v1"),
    )
    assert response.status_code == 422 and "user:secret" not in response.text
    monkeypatch.setenv("TAURIC_MODEL_KEY_FILE", "/nonexistent/tauric-key")
    runtime_config.cache_clear()
    assert (
        signed_in.put(
            url, json=connection(provider="openai_compatible", base_url="http://127.0.0.1:9/v1")
        ).status_code
        == 503
    )
    with Session(database[0]) as db:
        assert not db.scalars(select(ModelConnectionVersion)).all()


def test_wrong_key_blocks_saved_connections_instead_of_using_host_fallback(signed_in, monkeypatch):
    signed_in.put("/api/v1/model-connections/cloud", json=connection())
    monkeypatch.setenv("TAURIC_MODEL_KEY_FILE", "/nonexistent/tauric-key")
    runtime_config.cache_clear()
    assert signed_in.get("/api/v1/model-connections").status_code == 503
    assert not any(
        profile["configured"]
        for profile in signed_in.get("/api/v1/analysis/status").json()["profiles"]
    )


def test_connection_concurrent_saves_cannot_overwrite(signed_in):
    cookies = dict(signed_in.cookies)
    headers = dict(signed_in.headers)

    def save(_):
        with TestClient(app, headers=headers, cookies=cookies) as client:
            return client.put("/api/v1/model-connections/cloud", json=connection()).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(save, range(2))) == [200, 409]


def test_password_rejects_bad_current_weak_or_mismatch_and_redacts_inputs(signed_in):
    assert (
        signed_in.post(
            "/api/v1/owner/password", json=password(current_password="wrong-secret")
        ).status_code
        == 400
    )
    weak = signed_in.post(
        "/api/v1/owner/password", json=password(new_password="weak", confirm_password="weak")
    )
    assert weak.status_code == 422 and "wrong-secret" not in weak.text and OLD not in weak.text
    assert (
        signed_in.post(
            "/api/v1/owner/password", json=password(confirm_password=NEW + "different")
        ).status_code
        == 422
    )
    assert (
        signed_in.post(
            "/api/v1/owner/password", json=password(new_password=OLD, confirm_password=OLD)
        ).status_code
        == 422
    )
    assert signed_in.get("/api/v1/session").status_code == 200


def test_password_change_revokes_all_sessions_and_old_password(signed_in):
    with TestClient(app, headers={"Origin": "http://testserver"}) as other:
        assert (
            other.post(
                "/api/v1/session", json={"username": "test-owner", "password": OLD}
            ).status_code
            == 200
        )
        result = signed_in.post("/api/v1/owner/password", json=password())
        assert result.status_code == 204 and "Max-Age=0" in result.headers["set-cookie"]
        assert other.get("/api/v1/session").status_code == 401
    assert signed_in.get("/api/v1/session").status_code == 401
    assert (
        signed_in.post(
            "/api/v1/session", json={"username": "test-owner", "password": OLD}
        ).status_code
        == 401
    )
    login = signed_in.post("/api/v1/session", json={"username": "test-owner", "password": NEW})
    assert login.status_code == 200
    activity = signed_in.get("/api/v1/audit").text
    assert "owner.password_changed" in activity and NEW not in activity and OLD not in activity


def test_password_verification_is_throttled(signed_in):
    for _ in range(5):
        assert (
            signed_in.post(
                "/api/v1/owner/password", json=password(current_password="wrong")
            ).status_code
            == 400
        )
    result = signed_in.post("/api/v1/owner/password", json=password())
    assert result.status_code == 429 and result.headers["Retry-After"]
