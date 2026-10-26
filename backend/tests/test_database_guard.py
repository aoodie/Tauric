import json
import os
from pathlib import Path
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


def test_isolated_executor_identity_never_uses_an_environment_bypass(tmp_path, monkeypatch):
    import database_guard

    monkeypatch.setenv("TAURIC_ISOLATED_TESTS", "true")
    monkeypatch.setattr(database_guard.os, "geteuid", lambda: 501)
    assert database_guard.isolated_cluster(Path("/workspace"), 54330) is None
    monkeypatch.setattr(database_guard.os, "geteuid", lambda: 10001)
    assert database_guard.isolated_cluster(tmp_path, 54330) is None
    assert database_guard.isolated_cluster(Path("/workspace"), 54331) is None


@pytest.mark.parametrize("unsafe", [None, "writable_root", "network", "privileges", "capabilities", "owner", "permissions", "symlink", "control_identity"])
def test_isolated_database_requires_all_sandbox_and_cluster_evidence(monkeypatch, unsafe):
    import database_guard

    original_read, original_stat = Path.read_text, Path.stat

    def read(path, *args, **kwargs):
        if str(path) == "/proc/self/status":
            return "NoNewPrivs:\t" + ("0" if unsafe == "privileges" else "1") + "\nCapEff:\t" + ("01" if unsafe == "capabilities" else "00") + "\nCapPrm: 0\nCapBnd: 0\nCapAmb: 0\n"
        if path.name == "flags":
            return "0x1" if unsafe == "network" else "0x0"
        return original_read(path, *args, **kwargs)

    def stat(path, *args, **kwargs):
        if str(path) == "/tmp/tauric-test-postgres":
            return SimpleNamespace(st_uid=0 if unsafe == "owner" else 10001, st_mode=0o40755 if unsafe == "permissions" else 0o40700)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(database_guard.os, "geteuid", lambda: 10001)
    monkeypatch.setattr(database_guard.os, "statvfs", lambda _: SimpleNamespace(f_flag=0 if unsafe == "writable_root" else os.ST_RDONLY))
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "is_dir", lambda _: True)
    monkeypatch.setattr(Path, "is_symlink", lambda _: unsafe == "symlink")
    monkeypatch.setattr(database_guard.socket, "if_nameindex", lambda: [(1, "lo"), (2, "tunnel0")])
    commands = []
    monkeypatch.setattr(database_guard, "command", lambda *args: commands.append(args) or ("invalid control data" if unsafe == "control_identity" else "Database system identifier: 1234567890\n"))
    if unsafe is None:
        assert database_guard.isolated_cluster(Path("/workspace"), 54330) == "1234567890"
        assert commands == [("/usr/lib/postgresql/15/bin/pg_controldata", "/tmp/tauric-test-postgres")]
    else:
        with pytest.raises(RuntimeError, match="identity"):
            database_guard.isolated_cluster(Path("/workspace"), 54330)
    if unsafe not in {None, "control_identity"}:
        assert commands == []
