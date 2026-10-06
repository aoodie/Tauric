"""TradingView OAuth/privacy and economic evidence checks; no real accounts."""

import base64
import copy
import hashlib
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient
from test_market_adapters import FX, spec

from tauric import forex_macro, forex_sources
from tauric import tradingview_connection as conn
from tauric import tradingview_mcp as mcp
from tauric import tradingview_sources as sources
from tauric.app import app
from tauric.config import runtime_config
from tauric.models import Owner, TradingViewOAuthFlow

STAMP = datetime(2026, 10, 4, 12, tzinfo=UTC)


@pytest.mark.parametrize("transport", ["oauth", "mcp"])
def test_slow_provider_acknowledgements_complete_without_retry(monkeypatch, transport):
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            value = json.loads(body) if self.path == "/mcp" else {}
            method = value.get("method", "oauth")
            requests.append(method)
            if method in {"oauth", "notifications/initialized"}:
                time.sleep(11)
            if method == "notifications/initialized":
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            result = (
                {"protocolVersion": "2025-06-18"}
                if method == "initialize"
                else {"tools": [{"name": name} for name in sorted(mcp.READ_TOOLS)]}
            )
            payload = json.dumps(
                grant()
                if method == "oauth"
                else {"jsonrpc": "2.0", "id": value["id"], "result": result}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    try:
        address = f"http://127.0.0.1:{provider.server_port}"
        if transport == "oauth":
            assert conn.request_json("POST", address + "/oauth", data={}) == grant()
            assert requests == ["oauth"]
        else:
            monkeypatch.setattr(mcp, "SERVER", address + "/mcp")
            with mcp.EconomicMCP("private-access-fixture") as client:
                assert set(client.tools) == mcp.READ_TOOLS
            assert requests == ["initialize", "notifications/initialized", "tools/list"]
    finally:
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)


def grant(**changes):
    return {
        "access_token": "private-access-fixture",
        "refresh_token": "private-refresh-fixture",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "mcp:read",
        **changes,
    }


@pytest.fixture
def oauth(monkeypatch, signed_in):
    runtime_config().allowed_origins.append("http://127.0.0.1:8080")
    signed_in.headers["Origin"] = "http://127.0.0.1:8080"
    calls = []
    monkeypatch.setattr(conn, "metadata", lambda: None)

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url == conn.REGISTER:
            payload = kwargs["json"]
            assert (
                payload["scope"] == "mcp:read" and payload["token_endpoint_auth_method"] == "none"
            )
            return {
                "client_id": "public-client-fixture",
                "redirect_uris": payload["redirect_uris"],
                "token_endpoint_auth_method": "none",
            }
        assert url == conn.TOKEN
        assert kwargs["data"]["resource"] == conn.SERVER
        return grant()

    monkeypatch.setattr(conn, "request_json", request)
    return signed_in, calls


def start(client, revision=0):
    response = client.post("/api/v1/tradingview/connect", json={"expected_revision": revision})
    assert response.status_code == 200, response.text
    return parse_qs(urlsplit(response.json()["authorization_url"]).query)


def finish(client, query, **params):
    return client.get(
        "/api/v1/tradingview/callback",
        params={"state": query["state"][0], "code": "private-code-fixture", **params},
        follow_redirects=False,
    )


def test_signin_pkce_owner_binding_encryption_callback_and_replay(oauth, database):
    client, calls = oauth
    query = start(client)
    assert query["scope"] == ["mcp:read"] and query["resource"] == [conn.SERVER]
    assert "code_verifier" not in query
    with Session(database[0]) as db:
        flow = db.get(TradingViewOAuthFlow, conn.digest(query["state"][0]))
        private = conn.unseal(flow.encrypted_config)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(private["verifier"].encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        assert query["code_challenge"] == [challenge]
        assert private["verifier"] not in flow.encrypted_config
    result = finish(client, query)
    assert (
        result.status_code == 303
        and result.headers["location"] == "http://127.0.0.1:8080/?tradingview=connected"
    )
    assert result.headers["referrer-policy"] == "no-referrer"
    assert calls[-1][2]["data"]["code_verifier"] == private["verifier"]
    assert finish(client, query).status_code == 400
    view = client.get("/api/v1/tradingview/connection")
    assert view.json()["configured"] and view.json()["revision"] == 1
    for secret in [
        "private-code-fixture",
        "private-access-fixture",
        "private-refresh-fixture",
        private["verifier"],
    ]:
        assert secret not in view.text and secret not in client.get("/api/v1/audit").text
    with Session(database[0]) as db:
        assert "private-access-fixture" not in conn.head(db).encrypted_config
        assert conn.saved(db)[0]["access_token"] == "private-access-fixture"


def test_owner_and_csrf_are_required(client):
    assert client.get("/api/v1/tradingview/connection").status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert (
        client.post("/api/v1/tradingview/connect", json={"expected_revision": 0}).status_code == 403
    )


def test_expired_flow_and_changed_revision_are_rejected_before_token_exchange(oauth, database):
    client, calls = oauth
    query = start(client)
    with Session(database[0]) as db:
        flow = db.get(TradingViewOAuthFlow, conn.digest(query["state"][0]))
        flow.expires_at = conn.now() - timedelta(seconds=1)
        db.commit()
    assert finish(client, query).status_code == 400
    query = start(client)
    assert (
        client.post("/api/v1/tradingview/disconnect", json={"expected_revision": 0}).status_code
        == 200
    )
    assert finish(client, query).status_code == 409
    assert all(url != conn.TOKEN for _, url, _ in calls)


def test_denied_signin_is_consumed_without_credentials(oauth):
    client, calls = oauth
    query = start(client)
    result = finish(client, query, error="access_denied")
    assert result.status_code == 303 and "cancelled" in result.headers["location"]
    assert all(url != conn.TOKEN for _, url, _ in calls)
    assert not client.get("/api/v1/tradingview/connection").json()["configured"]


def test_callback_removes_code_from_access_log_scope(oauth):
    client, _ = oauth
    query = start(client)
    observed = []

    async def capture(scope, receive, send):
        await app(scope, receive, send)
        if scope.get("path") == "/api/v1/tradingview/callback":
            observed.append(scope.get("query_string"))

    with TestClient(capture) as receiver:
        receiver.cookies.update(client.cookies)
        result = finish(receiver, query)
        assert result.status_code == 303
    assert observed == [b""]


def test_callback_is_bound_to_original_owner_session(oauth):
    client, _ = oauth
    query = start(client)
    # A second sign-in for the same owner must not consume the first session's flow.
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert finish(client, query).status_code == 400


@pytest.mark.parametrize(
    "change",
    [
        {"scope": "mcp:read mcp:tools"},
        {"token_type": "Other"},
        {"expires_in": -1},
        {"access_token": "token\nunsafe"},
    ],
)
def test_excessive_or_invalid_token_grants_are_rejected(change):
    with pytest.raises(ValueError):
        conn.token_config(
            grant(**change), "client", "http://127.0.0.1:8080/api/v1/tradingview/callback"
        )


def test_refresh_rotation_commits_independently_of_research_rollback(database, monkeypatch):
    monkeypatch.setattr(conn, "now", lambda: STAMP)
    calls = []

    def refresh(method, url, **kwargs):
        calls.append(kwargs["data"])
        return grant(access_token="rotated-access", refresh_token="rotated-refresh")

    monkeypatch.setattr(conn, "request_json", refresh)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        config = conn.token_config(
            grant(), "client", "http://127.0.0.1:8080/api/v1/tradingview/callback"
        )
        config["expires_at"] = (STAMP - timedelta(seconds=1)).isoformat()
        conn.append(db, owner.id, 0, config)
        db.commit()
        updated, revision = conn.credentials(db)
        db.rollback()
        assert revision == 2 and updated["refresh_token"] == "rotated-refresh"
        assert conn.credentials(db)[1] == 2 and len(calls) == 1
        assert calls[0]["resource"] == conn.SERVER


def test_failed_refresh_does_not_corrupt_saved_connection(database, monkeypatch):
    monkeypatch.setattr(conn, "now", lambda: STAMP)

    def fail(*args, **kwargs):
        raise ValueError("private-refresh-fixture")

    monkeypatch.setattr(conn, "request_json", fail)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        config = conn.token_config(
            grant(), "client", "http://127.0.0.1:8080/api/v1/tradingview/callback"
        )
        config["expires_at"] = (STAMP - timedelta(seconds=1)).isoformat()
        conn.append(db, owner.id, 0, config)
        db.commit()
        with pytest.raises(HTTPException) as error:
            conn.credentials(db)
        assert "private-refresh-fixture" not in error.value.detail
        assert conn.saved(db)[1] == 1


def mock_mcp(monkeypatch, stream=False, unsafe=False, hyphenated=False):
    requests = []
    original = httpx.Client

    def response(request):
        data = json.loads(request.content)
        requests.append((request, data))
        if data["method"] == "notifications/initialized":
            return httpx.Response(202)
        if data["method"] == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
        elif data["method"] == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "mcp-tv-" + (name.replace("_", "-") if hyphenated else name),
                        "annotations": {"readOnlyHint": not unsafe},
                    }
                    for name in mcp.READ_TOOLS
                ]
            }
        else:
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {"data": [{"date": "2026-09-28", "value": 3.5}], "unit": "percent"}
                        ),
                    }
                ]
            }
        reply = {"jsonrpc": "2.0", "id": data["id"], "result": result}
        if stream and data["method"] == "tools/call":
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content="data: " + json.dumps(reply) + "\n\n",
            )
        return httpx.Response(
            200, json=reply, headers={"Mcp-Session-Id": "private-session-fixture"}
        )

    monkeypatch.setattr(
        mcp.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs),
    )
    return requests


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("hyphenated", [False, True])
def test_mcp_handshake_json_and_sse_only_allow_economic_reads(monkeypatch, stream, hyphenated):
    requests = mock_mcp(monkeypatch, stream, hyphenated=hyphenated)
    with mcp.EconomicMCP("private-access-fixture") as client:
        assert client.call("get_economic_data", {"symbol": "ECONOMICS:USINTR"})["unit"] == "percent"
        with pytest.raises(ValueError):
            client.call("create_alert", {})
    assert len(requests) == 4
    for request, body in requests:
        assert (
            str(request.url) == conn.SERVER
            and request.headers["Authorization"] == "Bearer private-access-fixture"
        )
        assert "private-access-fixture" not in json.dumps(body)


def test_mutating_tool_annotation_cannot_be_used(monkeypatch):
    mock_mcp(monkeypatch, unsafe=True)
    with pytest.raises(ValueError):
        with mcp.EconomicMCP("private-access-fixture"):
            pass


def event(**changes):
    return {
        "id": "1",
        "title": "CPI YoY",
        "currency": "USD",
        "country": "US",
        "date": "2026-10-02T12:30:00Z",
        "actual": "3.2%",
        "forecast": "3.0%",
        "previous": "3.3%",
        "unit": "%",
        **changes,
    }


def test_calendar_values_forecasts_surprises_and_time_are_distinct():
    items, _ = sources.calendar({"events": [event()]}, "USD", "US", STAMP)
    assert items[0]["surprise"] == {
        "value": "0.2",
        "unit": "percentage points",
        "basis": "actual minus survey consensus; same displayed units",
    }
    for changes in [
        {"date": "2026-10-05T12:30:00Z"},
        {"date": "2026-10-02"},
        {"date": "2026-10-02T12:30:00"},
    ]:
        item = sources.calendar([event(**changes)], "USD", "US", STAMP)[0][0]
        assert item["actual"] is None and item["surprise"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"currency": "EUR"},
        {"country": "JP"},
        {"id": None},
        {"date": "2027-01-01"},
        {"actual": "NaN"},
    ],
)
def test_bad_calendar_binding_is_rejected(changes):
    with pytest.raises(ValueError):
        sources.calendar([event(**changes)], "USD", "US", STAMP)


def test_rate_catalog_is_discovered_and_ambiguous_or_stale_data_rejected():
    catalog = {"symbols": [{"symbol": "ECONOMICS:USINTR", "name": "United States Interest Rate"}]}
    symbol, name = sources.policy_symbol(catalog, "US")
    data = {"symbol": symbol, "unit": "percent", "data": [{"date": "2026-09-28", "value": "3.5"}]}
    assert sources.rates(data, symbol, name, STAMP)[0]["value"] == "3.5"
    with pytest.raises(ValueError):
        sources.policy_symbol(catalog, "EU")
    catalog["symbols"].append(
        {"symbol": "ECONOMICS:USOTHER", "name": "United States Interest Rate"}
    )
    with pytest.raises(ValueError):
        sources.policy_symbol(catalog, "US")
    for change in [
        {"unit": "unknown"},
        {"scale": 1000},
        {"symbol": "ECONOMICS:JPINTR"},
        {"data": [{"date": "2020-01-01", "value": "3.5"}]},
    ]:
        with pytest.raises(ValueError):
            sources.rates({**data, **change}, symbol, name, STAMP)


def test_live_provider_calendar_wrapper_display_scales_and_benchmark_catalog():
    items, _ = sources.calendar(
        {"result": [event(actual=90, forecast=70, unit=None, scale="K")]}, "USD", "US", STAMP
    )
    assert items[0]["unit"] == "K" and items[0]["surprise"]["value"] == "20"
    assert items[0]["surprise"]["unit"] == "K"
    trillions = sources.calendar(
        {"result": [event(actual=6.9, forecast=6.8, unit="$", scale="T")]}, "USD", "US", STAMP
    )[0][0]
    assert trillions["unit"] == "T $" and trillions["surprise"]["value"] == "0.1"
    catalog = {
        "country": {"code": "EU", "name": "European Union"},
        "symbols": [
            {
                "symbol": "ECONOMICS:EUDIR",
                "description": "European Union Deposit Interest Rate",
                "category": "mny",
            },
            {
                "symbol": "ECONOMICS:EUINTR",
                "description": "European Union Interest Rate",
                "category": "mny",
            },
            {
                "symbol": "ECONOMICS:EUIRNM",
                "description": "European Union Interest rate on new mortgages",
                "category": "mny",
            },
        ],
    }
    symbol, name = sources.policy_symbol(catalog, "EU")
    assert symbol == "ECONOMICS:EUINTR"
    data = {
        "success": True,
        "symbol": symbol,
        "unit": "%",
        "scale": 100,
        "series": [{"date": "2026-09-01", "value": 2.65}],
    }
    rate = sources.rates(data, symbol, name, STAMP)[0]
    assert rate["value"] == "2.65" and rate["provider_scale"] == "100"
    with pytest.raises(ValueError):
        sources.policy_symbol({**catalog, "country": {"code": "US"}}, "EU")
    with pytest.raises(ValueError):
        sources.calendar({"result": [event(scale="unknown")]}, "USD", "US", STAMP)


def test_connection_check_persists_then_disconnect_invalidates(oauth, monkeypatch):
    client, _ = oauth
    assert finish(client, start(client)).status_code == 303
    monkeypatch.setattr(sources, "collect", lambda db, spec: [{"status": "available"}] * 4)
    checked = client.post("/api/v1/tradingview/test", json={"expected_revision": 1})
    assert checked.status_code == 200 and checked.json()["ready"]
    assert client.get("/api/v1/tradingview/connection").json()["checked_at"]
    assert (
        client.post("/api/v1/tradingview/disconnect", json={"expected_revision": 0}).status_code
        == 409
    )
    disconnected = client.post("/api/v1/tradingview/disconnect", json={"expected_revision": 1})
    assert not disconnected.json()["configured"] and disconnected.json()["checked_at"] is None


def test_new_tradingview_binding_preserves_legacy_and_rejects_wrong_host(monkeypatch, database):
    from forex_supplement_fixture import fixture

    binding = fixture(spec(FX))
    assert forex_sources.validate(binding, spec(FX).model_dump())
    binding["data"]["version"] = forex_sources.TRADINGVIEW_VERSION
    for source in binding["data"]["sources"][:4]:
        source["url"] = sources.URL
    binding["checksum"] = forex_macro.digest(binding["data"])
    assert forex_sources.validate(binding, spec(FX).model_dump())
    broken = copy.deepcopy(binding)
    broken["data"]["sources"][0]["url"] = "https://evil.example/calendar"
    broken["checksum"] = forex_macro.digest(broken["data"])
    with pytest.raises(ValueError):
        forex_sources.validate(broken, spec(FX).model_dump())


def test_economic_reads_use_one_total_deadline_without_retries(monkeypatch):
    from types import SimpleNamespace

    original = httpx.Client
    clock = {"value": 0}
    calls = []
    monkeypatch.setattr(mcp, "time", SimpleNamespace(monotonic=lambda: clock["value"]))

    def respond(request):
        body = json.loads(request.content)
        calls.append((body["method"], request.extensions["timeout"]["read"]))
        clock["value"] += 9
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        result = (
            {"protocolVersion": "2025-06-18"}
            if body["method"] == "initialize"
            else {"tools": [{"name": name} for name in mcp.READ_TOOLS]}
        )
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(
        mcp.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs),
    )
    with pytest.raises(ValueError, match="bounds"):
        with mcp.EconomicMCP("fixture-only", deadline=20):
            raise AssertionError("Expired evidence must not be used")
    assert calls == [("initialize", 20), ("notifications/initialized", 11), ("tools/list", 2)]
    before = len(calls)
    with pytest.raises(ValueError, match="time budget"):
        with mcp.EconomicMCP("fixture-only", deadline=20):
            pass
    assert len(calls) == before


def test_expired_supplement_deadline_reports_gaps_without_requests(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        sources,
        "credentials",
        lambda _: pytest.fail("No external credential refresh after deadline"),
    )
    spec = SimpleNamespace(base_currency="NZD", quote_currency="USD")
    rows = sources.collect(None, spec, deadline=0)
    assert len(rows) == 4
    assert all(row["status"] == "unavailable" and row["items"] == [] for row in rows)
    assert all("time budget" in row["warning"] for row in rows)
