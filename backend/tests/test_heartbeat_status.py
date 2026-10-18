"""Read-only heartbeat telemetry, with no production database or model calls."""
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tauric import chat_api, heartbeat_status
from tauric.auth import authenticate
from tauric.config import runtime_config
from tauric.models import now


def fixture_status():
    return {"observed_at": now().isoformat(), "enabled": True, "status": "healthy",
            "model": "tauric-heartbeat:4b", "interval_seconds": 1800,
            "ready": True, "http_status": 200}


def setup_source(monkeypatch, tmp_path, payload):
    secret = tmp_path / "service.json"
    token = "fixture-service-secret-" + "x" * 50
    secret.write_text(json.dumps({"platform_token": token}))
    monkeypatch.setenv("TAURIC_OPENCLAW_PRIVATE_FILE", str(secret))
    runtime_config.cache_clear()

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, length):
            assert length == 8193
            return json.dumps(payload).encode()

    def open_status(request, timeout):
        assert request.full_url == "http://openclaw:18790/heartbeat"
        assert request.headers == {"Authorization": "Bearer " + token}
        assert timeout == 2
        return Response()

    def opener(*handlers):
        assert any(isinstance(h, heartbeat_status.NoRedirect) for h in handlers)
        return SimpleNamespace(open=open_status)

    monkeypatch.setattr(heartbeat_status, "build_opener", opener)


def test_status_is_typed_and_sanitized(monkeypatch, tmp_path):
    setup_source(monkeypatch, tmp_path, fixture_status())
    result = heartbeat_status.read_status()
    assert result.status == "healthy" and result.ready
    assert "fixture-service-secret" not in result.model_dump_json()


def test_bad_payloads_and_stale_results_fail_closed(monkeypatch, tmp_path):
    for changes in [{"status": "made-up"}, {"private_token": "must-never-leak"},
                    {"observed_at": "2000-01-01T00:00:00Z"}, {"model": "external-model"}]:
        setup_source(monkeypatch, tmp_path, {**fixture_status(), **changes})
        result = heartbeat_status.read_status()
        assert result.status == "unavailable" and result.ready is None
        assert "must-never-leak" not in result.model_dump_json()


def test_network_failure_is_unavailable(monkeypatch, tmp_path):
    setup_source(monkeypatch, tmp_path, fixture_status())
    def failed(*args):
        raise OSError("private network diagnostics")
    monkeypatch.setattr(heartbeat_status, "build_opener", failed)
    result = heartbeat_status.read_status()
    assert result.status == "unavailable"
    assert "diagnostics" not in result.model_dump_json()
    assert heartbeat_status.NoRedirect().redirect_request(None, None, 302, "", {}, "https://external") is None


def test_owner_authentication_and_endpoint(monkeypatch):
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as client:
        assert client.get("/api/v1/chat/heartbeat").status_code == 401
        app.dependency_overrides[authenticate] = lambda: SimpleNamespace(id="owner")
        monkeypatch.setattr(chat_api, "read_status", lambda: heartbeat_status.HeartbeatStatus(**fixture_status()))
        response = client.get("/api/v1/chat/heartbeat")
        assert response.status_code == 200 and response.json()["status"] == "healthy"
        assert client.post("/api/v1/chat/heartbeat").status_code == 405
