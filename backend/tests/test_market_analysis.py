import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import ModuleType
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_native_broker_setup import Model as SetupModel
from test_native_broker_setup import context as setup_context

from tauric import analysis, market
from tauric.asset_capabilities import research_capabilities
from tauric.config import runtime_config
from tauric.market_contracts import AnalysisRequest
from tauric.model_profiles import ModelProfile, public_profiles
from tauric.models import AnalysisJob, MarketSnapshot, Owner, WorkerStatus, now
from tauric.native_broker_setup import checksum as setup_checksum
from tauric.native_runner import artifact
from tauric.native_runner import run as run_native

INSTRUMENT = "binance:spot:BTCUSDT"


@pytest.fixture
def provider(monkeypatch):
    current = int(now().timestamp() * 1000)
    start = current // 900000 * 900000
    rows = [
        [
            start + offset * 900000,
            "100.123456789",
            "103",
            "99",
            "101",
            "17.00000001",
            start + (offset + 1) * 900000 - 1,
        ]
        for offset in [-2, -1, 0]
    ]
    state = {"rows": rows, "status": 200, "time": current, "calls": []}
    original = httpx.Client

    def handle(request):
        state["calls"].append(request.url.path)
        if request.url.path.endswith("time"):
            return httpx.Response(200, json={"serverTime": state["time"]})
        return httpx.Response(state["status"], json=state["rows"])

    monkeypatch.setattr(
        market.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    return state


def test_future_receipt_does_not_hide_a_fresh_refresh(signed_in, provider, database, monkeypatch):
    initial = signed_in.post(
        f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"}
    ).json()
    stamp = now()
    with Session(database[0]) as db:
        original = db.get(MarketSnapshot, initial["snapshot_id"])
        future_id = str(uuid4())
        db.add(
            MarketSnapshot(
                id=future_id,
                instrument_id=original.instrument_id,
                interval=original.interval,
                received_at=stamp + timedelta(hours=1),
                provider_time=original.provider_time,
                checksum=original.checksum,
                data=original.data,
            )
        )
        db.commit()
    monkeypatch.setattr(market, "now", lambda: stamp + timedelta(seconds=31))
    refreshed = signed_in.post(
        f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"}
    ).json()
    assert refreshed["state"] == "fresh"
    assert refreshed["snapshot_id"] not in {initial["snapshot_id"], future_id}
    assert len(provider["calls"]) == 4
    assert (
        signed_in.get(f"/api/v1/market/{INSTRUMENT}").json()["snapshot_id"]
        == refreshed["snapshot_id"]
    )


def test_market_is_private_and_writes_require_csrf(client):
    assert client.get("/api/v1/instruments").status_code == 401
    assert client.get(f"/api/v1/market/{INSTRUMENT}").status_code == 401
    assert client.get("/api/v1/analysis/status").status_code == 401
    login = client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert login.status_code == 200
    assert (
        client.post(f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"}).status_code
        == 403
    )


def test_real_contract_preserves_precision_times_and_snapshot(signed_in, provider, database):
    initial = signed_in.get(f"/api/v1/market/{INSTRUMENT}").json()
    assert initial["state"] == "empty"
    result = signed_in.post(f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"})
    assert result.status_code == 200
    data = result.json()
    assert data["candles"][0]["open"] == "100.123456789"
    assert data["candles"][0]["closed"] and not data["candles"][-1]["closed"]
    assert data["availability"] == "observed_at_receipt" and data["gaps"] == 0
    again = signed_in.post(f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"}).json()
    assert again["snapshot_id"] == data["snapshot_id"]
    assert len(provider["calls"]) == 2
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 1
    details = signed_in.get(f"/api/v1/resources/market_snapshot/{data['snapshot_id']}").json()
    assert details["data"]["checksum"] == data["checksum"]
    assert (
        signed_in.get(f"/api/v1/resources/instrument/{INSTRUMENT}").json()["data"]["native_symbol"]
        == "BTC-USD"
    )


@pytest.mark.parametrize(
    "fault",
    ["nan", "negative", "inverted", "future", "unsorted", "wrong_interval", "clock", "outdated"],
)
def test_invalid_provider_data_never_creates_a_snapshot(signed_in, provider, database, fault):
    if fault == "nan":
        provider["rows"][0][1] = "NaN"
    if fault == "negative":
        provider["rows"][0][5] = "-1"
    if fault == "inverted":
        provider["rows"][0][2] = "10"
    if fault == "future":
        provider["rows"][-1][0] = provider["time"] + 900000
    if fault == "unsorted":
        provider["rows"].reverse()
    if fault == "wrong_interval":
        provider["rows"][0][6] += 60000
    if fault == "clock":
        provider["time"] += 500000
    if fault == "outdated":
        for row in provider["rows"]:
            row[0] -= 9000000
            row[6] -= 9000000
    response = signed_in.post(f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"})
    assert response.status_code == 502
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 0


@pytest.mark.parametrize("status,expected", [(429, 429), (418, 429), (500, 502)])
def test_provider_errors_are_honest(signed_in, provider, status, expected):
    provider["status"] = status
    result = signed_in.post(f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"})
    assert result.status_code == expected
    if expected == 429:
        assert result.headers["Retry-After"] == "60"


def test_staleness_uses_receipt_time_and_reports_gaps(signed_in, provider, monkeypatch):
    provider["rows"].pop(1)
    data = signed_in.post(f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"}).json()
    assert data["gaps"] == 1
    actual_now = now()
    monkeypatch.setattr(market, "now", lambda: actual_now + timedelta(minutes=3))
    result = signed_in.get(f"/api/v1/market/{INSTRUMENT}").json()
    assert result["state"] == "stale" and result["snapshot_id"] == data["snapshot_id"]


@pytest.fixture
def configured(database, tmp_path, monkeypatch):
    private = tmp_path / "profiles.json"
    private.write_text(
        json.dumps(
            {
                "cloud": {
                    "provider": "openai",
                    "quick_model": "test-quick",
                    "deep_model": "test-deep",
                    "api_key": "test-secret-never-return",
                },
                "local": {
                    "provider": "openai_compatible",
                    "base_url": "http://127.0.0.1:9/v1",
                    "quick_model": "local-test",
                    "deep_model": "local-test",
                },
            }
        )
    )
    monkeypatch.setenv("TAURIC_MODEL_PROFILES_FILE", str(private))
    runtime_config.cache_clear()
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    return private


def test_cloud_local_profiles_redact_secrets_and_do_not_claim_connectivity(signed_in, configured):
    response = signed_in.get("/api/v1/analysis/status")
    assert "test-secret-never-return" not in response.text
    assert all(profile["configured"] for profile in response.json()["profiles"])
    assert all(
        "verified when a run succeeds" in profile["detail"]
        for profile in response.json()["profiles"]
    )
    configured.write_text('{"cloud":{"provider":"bad","api_key":"invalid-secret"}}')
    result = public_profiles()
    assert not any(profile.configured for profile in result)
    assert "invalid-secret" not in str(result)


def request(client, request_id=None, profile="cloud", **extra):
    return client.post(
        "/api/v1/analysis",
        json={
            "request_id": str(request_id or uuid4()),
            "instrument_id": INSTRUMENT,
            "profile": profile,
            **extra,
        },
    )


def test_unconfigured_or_missing_worker_does_not_queue(signed_in, database):
    assert request(signed_in).status_code == 503
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    assert request(signed_in).status_code == 422
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


def test_analysis_request_is_idempotent_snapshots_settings_and_rejects_mismatch(
    signed_in, configured
):
    rid = uuid4()
    first = request(signed_in, rid)
    assert first.status_code == 202
    assert "test-secret-never-return" not in first.text
    again = request(signed_in, rid)
    assert again.json()["id"] == first.json()["id"]
    assert request(signed_in, rid, profile="local").status_code == 409
    assert first.json()["request"]["mode"] == "current_daily_research"
    assert first.json()["request"]["models"]["quick_model"] == "test-quick"
    assert first.json()["request"]["settings"]["max_tool_rounds"] == 20
    assert request(signed_in, snapshot_id="missing").status_code == 422


@pytest.mark.parametrize(
    "instrument_id,symbol,asset_type,analysts",
    [
        ("alpaca:equity:AAPL", "AAPL", "equity", ["market", "social", "news", "fundamentals"]),
        ("alpaca:equity:MSFT", "MSFT", "equity", ["market", "social", "news", "fundamentals"]),
        ("alpaca:etf:SPY", "SPY", "etf", ["market", "social", "news"]),
    ],
)
def test_stock_daily_research_preserves_route_and_immutable_report(
    signed_in, configured, database, instrument_id, symbol, asset_type, analysts
):
    rid = str(uuid4())
    body = {"request_id": rid, "instrument_id": instrument_id, "profile": "cloud"}
    response = signed_in.post("/api/v1/analysis", json=body)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert signed_in.post("/api/v1/analysis", json=body).json()["id"] == job_id
    assert "test-secret-never-return" not in response.text
    spec = response.json()["request"]["instrument"]
    assert (spec["native_symbol"], spec["native_asset_type"], spec["asset_type"]) == (
        symbol,
        "stock",
        asset_type,
    )
    assert spec["native_research_ready"]
    assert response.json()["request"]["analysts"] == analysts
    with Session(database[0]) as db:
        assert analysis.claim(db) == job_id
        job = db.get(AnalysisJob, job_id)
        result = artifact(
            {"market_report": "Synthetic stock evidence", "final_trade_decision": "Synthetic Hold"},
            "Hold",
            job.request,
            {},
            "0.5.2",
        )
        assert analysis.finish(db, job_id, result)
    saved = signed_in.get(f"/api/v1/analysis/{job_id}").json()
    payload = signed_in.get(f"/api/v1/analysis-artifacts/{saved['artifact_id']}").json()["payload"]
    assert "Alpaca" in payload["data_context"]
    assert "Binance" not in payload["data_context"]
    assert payload["request"]["instrument"]["native_symbol"] == symbol
    assert payload["execution_authority"] is False and payload["governance"] is None
    if asset_type == "etf":
        assert "company fundamentals are omitted" in payload["data_context"]


def test_daily_scope_and_separate_screening_never_enable_live_execution(signed_in, configured):
    instruments = {row["id"]: row for row in signed_in.get("/api/v1/instruments").json()}
    assert instruments["alpaca:equity:AAPL"]["native_research_ready"]
    assert (
        "native assessment and broker execution pending"
        not in instruments["alpaca:etf:SPY"]["analysis_scope"]
    )
    assert instruments["oanda:fx:EUR_USD"]["native_research_ready"]
    rows = {row["asset_class"]: row for row in research_capabilities()}
    for key in ("equity", "etf", "fx"):
        assert rows[key]["native_research_ready"]
        assert rows[key]["screening_ready"] and not rows[key]["live_execution_ready"]


@pytest.mark.parametrize("profile", ["cloud", "local"])
@pytest.mark.parametrize("instrument_id", ["alpaca:equity:AAPL", "alpaca:etf:SPY"])
@pytest.mark.parametrize("broker_setup", [False, True])
def test_worker_dispatches_stock_and_etf_to_native_stock_graph(
    signed_in, configured, monkeypatch, tmp_path, profile, instrument_id, broker_setup
):
    response = signed_in.post(
        "/api/v1/analysis",
        json={"request_id": str(uuid4()), "instrument_id": instrument_id, "profile": profile},
    )
    assert response.status_code == 202
    job = response.json()
    calls = {}
    setup_model = SetupModel()
    if broker_setup:
        context = setup_context(job["request"]["instrument"]["asset_type"])
        context["assessment_input"]["request"]["instrument_id"] = instrument_id
        context["evidence_checksum"] = setup_checksum(context["assessment_input"])
        job["request"]["broker_context"] = context
        job["request"]["mode"] = "broker_entry_assessment"

    class Graph:
        def __init__(self, *, selected_analysts, config, **kwargs):
            calls["analysts"] = selected_analysts
            calls["provider"] = config["llm_provider"]
            self.deep_thinking_llm = setup_model

        def propagate(self, symbol, trade_date, *, asset_type):
            calls["dispatch"] = (symbol, trade_date, asset_type)
            return {"final_trade_decision": "Synthetic result"}, "BUY" if broker_setup else "Hold"

    modules = {
        "tradingagents": {"__version__": "0.5.2"},
        "tradingagents.default_config": {"DEFAULT_CONFIG": {}},
        "tradingagents.graph.trading_graph": {"TradingAgentsGraph": Graph},
        "langchain_core.callbacks": {"BaseCallbackHandler": type("Callback", (), {})},
    }
    for name, attributes in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, name, module)
    # The runner installs provider keys privately; restore the test environment afterward.
    monkeypatch.setenv("OPENAI_API_KEY", "test-before-run")
    result = run_native(job, tmp_path)
    assert calls["dispatch"] == (
        job["request"]["instrument"]["native_symbol"],
        job["request"]["trade_date"],
        "stock",
    )
    assert calls["analysts"] == job["request"]["analysts"]
    assert calls["provider"] == ("openai" if profile == "cloud" else "openai_compatible")
    assert result["execution_authority"] is False
    if broker_setup:
        assert len(setup_model.calls) == 1
        assert result["broker_setup_assessment"]["decision"] == "approve"
        assert (
            result["native_rating"] == "BUY"
            and result["reports"]["final_trade_decision"] == "Synthetic result"
        )
    assert "Alpaca" in result["data_context"]


def test_worker_rejects_an_unsupported_route_before_loading_native_runtime(tmp_path):
    with pytest.raises(ValueError, match="Unsupported native asset route"):
        run_native(
            {"request": {"instrument": {"asset_type": "fx", "native_asset_type": "stock"}}},
            tmp_path,
        )


def test_native_result_is_immutable_and_preserves_reports_without_raw_messages(
    signed_in, configured, database, provider
):
    signed_in.post(f"/api/v1/market/{INSTRUMENT}/refresh", json={"interval": "15m"})
    job_id = request(signed_in).json()["id"]
    with Session(database[0]) as db:
        assert analysis.claim(db) == job_id
        job = db.get(AnalysisJob, job_id)
        result = artifact(
            {
                "market_report": "Original market report",
                "final_trade_decision": "Original Hold report",
                "messages": ["private trace"],
            },
            "Hold",
            job.request,
            {"llm_provider": "openai"},
            "0.5.2",
        )
        assert analysis.finish(db, job_id, result)
    view = signed_in.get(f"/api/v1/analysis/{job_id}").json()
    assert view["status"] == "succeeded"
    report = signed_in.get(f"/api/v1/analysis-artifacts/{view['artifact_id']}").json()
    assert report["payload"]["reports"]["market_report"] == "Original market report"
    assert not report["payload"]["execution_authority"] and "private trace" not in json.dumps(
        report
    )
    for table in ["analysis_artifacts", "analysis_jobs", "market_snapshots", "instruments"]:
        for sql in [f"DELETE FROM {table}", f"TRUNCATE {table} CASCADE"]:
            with pytest.raises(DBAPIError), database[1].begin() as connection:
                connection.execute(text(sql))
    with pytest.raises(DBAPIError), database[0].begin() as connection:
        connection.execute(text("UPDATE analysis_artifacts SET checksum='changed'"))
    with pytest.raises(DBAPIError), database[1].begin() as connection:
        connection.execute(text("UPDATE analysis_jobs SET request='{}'"))


def test_one_worker_claim_wins_and_expired_lease_is_not_requeued(signed_in, configured, database):
    first = request(signed_in).json()["id"]
    second = request(signed_in).json()["id"]

    def take(_):
        with Session(database[0]) as db:
            return analysis.claim(db)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(take, range(2)))
    assert claims.count(first) == 1 and claims.count(None) == 1
    with Session(database[0]) as db:
        job = db.get(AnalysisJob, first)
        job.heartbeat_at = now() - timedelta(seconds=analysis.LEASE_SECONDS + 1)
        db.commit()
        assert analysis.claim(db) == second
        assert db.get(AnalysisJob, first).status == "interrupted"
        assert not analysis.finish(db, first, {"reports": {"final_trade_decision": "Late result"}})


def test_queue_budget_and_configuration_pinning(signed_in, configured, database):
    for _ in range(4):
        assert request(signed_in).status_code == 202
    assert request(signed_in).status_code == 429
    with Session(database[0]) as db:
        worker = db.get(WorkerStatus, "native")
        worker.heartbeat_at = now() - timedelta(seconds=analysis.LEASE_SECONDS + 1)
        db.commit()
    assert not signed_in.get("/api/v1/analysis/status").json()["worker_ready"]
    one = ModelProfile(
        provider="openai_compatible",
        base_url="http://localhost:9/v1",
        quick_model="q",
        deep_model="d",
    )
    two = one.model_copy(update={"quick_model": "other"})
    assert one.fingerprint() != two.fingerprint()
    with pytest.raises(ValueError):
        ModelProfile(provider="openai_compatible", base_url="http://secret:password@localhost/v1")


def test_parallel_identical_requests_make_one_job(configured, database):
    rid = uuid4()

    def submit(_):
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            return analysis.enqueue(
                db,
                owner,
                AnalysisRequest(request_id=rid, instrument_id=INSTRUMENT, profile="cloud"),
            ).id

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert len(set(pool.map(submit, range(2)))) == 1


def test_missing_native_final_report_cannot_be_finalized():
    with pytest.raises(ValueError):
        artifact({"market_report": "Partial analyst report"}, "REVIEW", {}, {}, "0.5.2")
