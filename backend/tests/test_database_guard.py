import json
from types import SimpleNamespace

import pytest
from database_guard import local_cluster, validate_target, verify_connection
from sqlalchemy.engine import URL

NAME = "tauric_test_0123456789abcdef"
URL_VALUE = URL.create(
    "postgresql+psycopg", username="tauric_admin", password="test-only",
    host="127.0.0.1", port=54330, database="postgres",
)


@pytest.mark.parametrize("changes", [
    {"host": "production.example"}, {"host": "::1"}, {"database": "tauric"},
    {"username": "tauric_app"}, {"query": {"host": "production.example"}}, {"port": 543},
])
def test_unsafe_test_targets_are_refused(changes):
    with pytest.raises(RuntimeError, match="identity"):
        validate_target(URL_VALUE.set(**changes), NAME)


@pytest.mark.parametrize("name", ["tauric", "postgres", "tauric_test_prod", NAME + '"; DROP DATABASE tauric; --'])
def test_only_generated_disposable_database_names_are_allowed(name):
    with pytest.raises(RuntimeError, match="identity"):
        validate_target(URL_VALUE, name)


def test_loopback_tunnel_to_another_cluster_is_refused_before_ddl():
    statements = []

    def execute(statement):
        statements.append(str(statement))
        return SimpleNamespace(one=lambda: ("postgres", "different-cluster"))

    with pytest.raises(RuntimeError, match="identity"):
        verify_connection(SimpleNamespace(execute=execute), URL_VALUE, NAME, "local-cluster")
    assert len(statements) == 1 and statements[0].startswith("SELECT ")


@pytest.mark.parametrize("endpoint", ["ssh://production", "tcp://127.0.0.1:2375"])
def test_remote_docker_endpoints_are_refused(tmp_path, monkeypatch, endpoint):
    local_cluster.cache_clear()
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    with pytest.raises(RuntimeError, match="identity"):
        local_cluster(tmp_path, 54330)


@pytest.mark.parametrize("wrong_checkout", [False, True])
def test_cluster_identity_is_bound_to_checkout_container_and_port(tmp_path, monkeypatch, wrong_checkout):
    import database_guard

    local_cluster.cache_clear()
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    responses = iter([
        "a" * 64,
        json.dumps({
            "com.docker.compose.project": "tauric_foundation",
            "com.docker.compose.service": "db",
            "com.docker.compose.project.config_files": str(
                tmp_path / ("other/compose.yml" if wrong_checkout else "deploy/compose.dev.yml")
            ),
        }),
        json.dumps({"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "54330"}]}),
        "1234567890",
    ])
    monkeypatch.setattr(database_guard, "command", lambda *args: next(responses))
    if wrong_checkout:
        with pytest.raises(RuntimeError, match="identity"):
            local_cluster(tmp_path, 54330)
    else:
        assert local_cluster(tmp_path, 54330) == "1234567890"
    local_cluster.cache_clear()
