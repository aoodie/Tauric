"""The parent deadline terminates a drip-fed provider without rebilling."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tauric import discovery_transport


def test_overall_deadline_terminates_continuously_dripping_response(monkeypatch):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.send_response(200)
            self.end_headers()
            for _ in range(100):
                try:
                    self.wfile.write(b" ")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                time.sleep(0.02)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(discovery_transport, "DEADLINE_SECONDS", 0.4)
    started = time.monotonic()
    try:
        with pytest.raises(discovery_transport.ProviderFailure) as error:
            discovery_transport.request_json(f"http://127.0.0.1:{server.server_port}", {}, {})
        assert error.value.diagnostic == {"category": "overall_deadline"}
        assert time.monotonic() - started < 2
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize("status,body,category", [
    (401, b"private provider text", "http_status"),
    (200, b"not JSON", "invalid_json"),
    (200, b"x" * 64001, "response_too_large"),
])
def test_safe_error_categories_exclude_raw_provider_content(monkeypatch, status, body, category):
    import httpx

    transport = httpx.MockTransport(lambda request: httpx.Response(
        status, content=body, headers={"x-request-id": "safe-request-123"}))
    real_client = httpx.Client
    monkeypatch.setattr(discovery_transport.httpx, "Client", lambda **kwargs: real_client(transport=transport))
    result = discovery_transport.fetch({"url": "https://example.org", "headers": {}, "body": {}})
    assert result["error"] == {"category": category, "http_status": status,
                              "provider_request_id": "safe-request-123"}
    assert "private provider" not in json.dumps(result)


def test_a_request_id_cannot_echo_the_provider_key(monkeypatch):
    import httpx

    transport = httpx.MockTransport(lambda request: httpx.Response(
        401, headers={"x-request-id": "private-api-key"}))
    real_client = httpx.Client
    monkeypatch.setattr(discovery_transport.httpx, "Client", lambda **kwargs: real_client(transport=transport))
    result = discovery_transport.fetch({"url": "https://example.org", "headers": {"Authorization": "Bearer private-api-key"}, "body": {}})
    assert "private-api-key" not in json.dumps(result)
