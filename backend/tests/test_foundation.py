from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import services
from tauric.database import database_session
from tauric.defaults import default_layout
from tauric.models import Owner, OwnerSession, now


def change(client, value="comfortable", scope="user", revision=0, **extra):
    return client.put(
        "/api/v1/settings/ui.density",
        json={
            "scope": scope,
            "value": value,
            "expected_revision": revision,
            "reason": "Test preference change",
            **extra,
        },
    )


def test_private_routes_and_no_public_registration(client):
    for path in [
        "/api/v1/settings",
        "/api/v1/workspaces",
        "/api/v1/audit",
        "/api/v1/session",
        "/api/v1/resources/owner/anything",
    ]:
        assert client.get(path).status_code == 401
    assert client.post("/api/v1/owners", json={}).status_code == 404
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").status_code == 200


def test_cookie_csrf_origin_and_logout(signed_in):
    cookie = signed_in.cookies.get("tauric_session")
    assert cookie
    headers = dict(signed_in.headers)
    signed_in.headers.pop("X-CSRF-Token")
    assert change(signed_in).status_code == 403
    signed_in.headers.update(headers)
    signed_in.headers["Origin"] = "https://untrusted.example"
    assert change(signed_in).status_code == 403
    signed_in.headers["Origin"] = "http://testserver"
    assert signed_in.delete("/api/v1/session").status_code == 204
    assert signed_in.get("/api/v1/settings").status_code == 401
    signed_in.cookies.set("tauric_session", cookie)
    assert signed_in.get("/api/v1/settings").status_code == 401


def test_login_cookie_attributes_and_secret_redaction(client):
    response = client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    response = client.post(
        "/api/v1/session", json={"username": {}, "password": {"secret": "must-not-be-echoed"}}
    )
    assert response.status_code == 422
    assert "must-not-be-echoed" not in response.text
    assert "no-store" in response.headers["cache-control"]


def test_https_configuration_sets_secure_cookie(client, monkeypatch):
    from tauric.config import runtime_config

    monkeypatch.setenv("TAURIC_SECURE_COOKIES", "true")
    runtime_config.cache_clear()
    response = client.post(
        "/api/v1/session",
        json={"username": "test-owner", "password": "Test-only-password-2026"},
    )
    assert response.status_code == 200
    assert "secure" in response.headers["set-cookie"].lower()


def test_owner_bootstrap_never_overwrites_an_existing_owner(database, monkeypatch):
    from tauric.cli import main

    with Session(database[0]) as db:
        original_hash = db.scalar(select(Owner)).password_hash
    monkeypatch.setattr("sys.argv", ["tauric", "create-owner", "--username", "other-owner"])
    monkeypatch.setattr("getpass.getpass", lambda _: "Another-test-password-2026")
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    with Session(database[0]) as db:
        owners = list(db.scalars(select(Owner)))
        assert len(owners) == 1
        assert owners[0].username == "test-owner" and owners[0].password_hash == original_hash


def test_expired_session(signed_in, database):
    with Session(database[0]) as db:
        session = db.scalar(select(OwnerSession))
        session.expires_at = now() - timedelta(seconds=1)
        db.commit()
    assert signed_in.get("/api/v1/session").status_code == 401


def test_login_rate_limit_persists_between_clients(client):
    for _ in range(5):
        assert (
            client.post(
                "/api/v1/session", json={"username": "not-owner", "password": "wrong"}
            ).status_code
            == 401
        )
    response = client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert response.status_code == 429 and response.headers.get("Retry-After")


def test_setting_inheritance_restore_and_revision_conflict(signed_in):
    assert change(signed_in, "terminal", "global").status_code == 200
    response = change(signed_in, "comfortable")
    assert response.json()["source_scope"] == "user"
    assert change(signed_in, "compact").status_code == 409
    response = change(signed_in, None, revision=1, inherit=True)
    assert response.json()["effective_value"] == "terminal"
    assert response.json()["source_scope"] == "global"
    response = signed_in.post(
        "/api/v1/settings/ui.density/restore/1?scope=user",
        json={"expected_revision": 2, "reason": "Restore prior preference"},
    )
    assert response.status_code == 200 and response.json()["user_revision"] == 3
    assert response.json()["effective_value"] == "comfortable"
    history = signed_in.get("/api/v1/settings/ui.density/history").json()
    assert [item["revision"] for item in history] == [3, 2, 1]
    assert history[-1]["value"] == "comfortable" and history[1]["inherited"]
    actions = [item["action"] for item in signed_in.get("/api/v1/audit").json()["items"]]
    assert "setting.restored" in actions and "setting.inherited" in actions


@pytest.mark.parametrize(
    "key,value,scope",
    [
        ("ui.density", "invalid", "user"),
        ("ui.timezone", "Unknown/Timezone", "user"),
        ("system.worker_concurrency", True, "global"),
        ("system.worker_concurrency", 0, "global"),
        ("system.worker_concurrency", 4, "user"),
        ("workspace.confirm_unsaved", "false", "user"),
    ],
)
def test_invalid_settings_do_not_create_history(signed_in, key, value, scope):
    response = signed_in.put(
        f"/api/v1/settings/{key}",
        json={
            "scope": scope,
            "value": value,
            "expected_revision": 0,
            "reason": "Invalid setting test",
        },
    )
    assert response.status_code == 422
    assert signed_in.get(f"/api/v1/settings/{key}/history?scope={scope}").json() == []


def test_layout_save_clone_reset_restore_and_validation(signed_in):
    original = signed_in.get("/api/v1/workspaces").json()[0]
    path = f"/api/v1/workspaces/{original['id']}"
    layout = deepcopy(original["layout"])
    layout["layout"]["children"][0]["weight"] = 60
    response = signed_in.put(
        path + "/layout",
        json={"layout": layout, "expected_revision": 1, "reason": "Changed panel proportions"},
    )
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert (
        signed_in.put(
            path + "/layout",
            json={"layout": layout, "expected_revision": 1, "reason": "Stale panel proportions"},
        ).status_code
        == 409
    )
    clone = signed_in.post(
        "/api/v1/workspaces", json={"name": "Research", "clone_from": original["id"]}
    ).json()
    assert clone["layout"] == layout and clone["id"] != original["id"]
    assert (
        signed_in.post(
            path + "/reset", json={"expected_revision": 2, "reason": "Restore default arrangement"}
        ).json()["revision"]
        == 3
    )
    restored = signed_in.post(
        path + "/restore/2",
        json={"expected_revision": 3, "reason": "Restore preferred arrangement"},
    ).json()
    assert restored["layout"] == layout and restored["revision"] == 4
    assert len(signed_in.get(path + "/history").json()) == 4
    invalid = deepcopy(layout)
    invalid["layout"]["children"][1]["children"][0]["component"] = "untrusted-panel"
    assert (
        signed_in.put(
            path + "/layout",
            json={"layout": invalid, "expected_revision": 4, "reason": "Invalid panel test"},
        ).status_code
        == 422
    )
    assert signed_in.get(path).json()["revision"] == 4


@pytest.mark.parametrize(
    "table", ["audit_events", "setting_versions", "layout_versions", "setting_definitions"]
)
def test_database_blocks_history_mutation_even_for_migration_owner(signed_in, database, table):
    change(signed_in)
    admin = database[1]
    column = "key" if table == "setting_definitions" else "id"
    for query in [
        f"UPDATE {table} SET {column}={column}",
        f"DELETE FROM {table}",
        f"TRUNCATE {table} CASCADE",
    ]:
        with pytest.raises(DBAPIError), admin.begin() as connection:
            connection.execute(text(query))
    with pytest.raises(DBAPIError), database[0].begin() as connection:
        connection.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER ALL"))


def test_resource_resolution_and_audit_pagination(signed_in):
    change(signed_in)
    page1 = signed_in.get("/api/v1/audit?limit=1").json()
    page2 = signed_in.get(f"/api/v1/audit?limit=1&cursor={page1['next_cursor']}").json()
    assert page1["items"][0]["id"] != page2["items"][0]["id"]
    event = page1["items"][0]
    assert signed_in.get(f"/api/v1/resources/audit/{event['id']}").status_code == 200
    assert (
        signed_in.get("/api/v1/resources/setting/ui.density").json()["data"]["effective_value"]
        == "comfortable"
    )
    assert signed_in.get("/api/v1/resources/unknown/id").status_code == 404


def test_unavailable_database_is_not_reported_ready(client):
    from tauric.app import app

    class BrokenStorage:
        def scalar(self, *_):
            raise RuntimeError("database down")

    app.dependency_overrides[database_session] = lambda: BrokenStorage()
    try:
        assert client.get("/health/ready").status_code == 503
    finally:
        app.dependency_overrides.clear()


def test_parallel_setting_updates_have_one_winner(database):
    application_engine = database[0]
    with Session(application_engine) as db:
        owner_id = db.scalar(select(Owner.id))

    def update(value):
        with Session(application_engine) as db:
            owner = db.get(Owner, owner_id)
            try:
                services.change_setting(
                    db, owner, "ui.density", "user", value, False, 0, "Concurrent setting change"
                )
                return 200
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, ["terminal", "comfortable"])) == [200, 409]


def test_parallel_layout_updates_have_one_winner(database):
    application_engine = database[0]
    with Session(application_engine) as db:
        owner = db.scalar(select(Owner))
        owner_id = owner.id
        workspace_id = services.create_workspace(db, owner, "Concurrency").id

    def update(weight):
        with Session(application_engine) as db:
            owner = db.get(Owner, owner_id)
            layout = default_layout()
            layout["layout"]["children"][0]["weight"] = weight
            try:
                services.change_layout(
                    db, owner, workspace_id, layout, 1, "Concurrent layout change"
                )
                return 200
            except HTTPException as error:
                return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(update, [50, 60])) == [200, 409]
