import copy
import json
import sys
from datetime import timedelta
from decimal import Decimal
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_market_adapters import FX, bar, spec
from test_market_analysis import configured  # noqa: F401
from test_native_broker_setup import Model as SetupModel
from test_native_broker_setup import context as setup_context

from tauric import analysis, forex_macro, market, native_fx
from tauric.config import runtime_config
from tauric.market_calendars import default_context, periods
from tauric.market_contracts import AnalysisRequest
from tauric.models import AnalysisJob, MarketSnapshot, Owner, now
from tauric.native_runner import run

pytestmark = pytest.mark.usefixtures("configured")


def daily_fixture(instrument, stamp=None):
    stamp = stamp or now()
    context = default_context(instrument)
    bars = []
    for index, (op, close) in enumerate(periods(context, "1d", stamp - timedelta(days=100), stamp)):
        candle = bar(op, close, str(Decimal(1) + Decimal(index) / 1000), sides=True)
        candle["closed"] = close <= stamp
        bars.append(candle)
    return bars, stamp, stamp, context.model_dump(mode="json")


def policy_fixture(instrument):
    stamp = now()
    currencies = [instrument.base_currency, instrument.quote_currency]
    sources = []
    for currency in currencies:
        label, timezone, news_url, calendar_url = forex_macro.SOURCES[currency]
        for kind, url in (("news", news_url), ("calendar", calendar_url)):
            items = (
                [
                    {
                        "title": f"Offline {currency} policy headline",
                        "published_at": stamp.isoformat(),
                        "url": url,
                    }
                ]
                if kind == "news"
                else [
                    {
                        "label": f"Offline {currency} policy meeting",
                        "date": (stamp + timedelta(days=7)).date().isoformat(),
                        "time": None,
                        "timezone": timezone,
                        "url": url,
                    }
                ]
            )
            unavailable = currency == "GBP"
            sources.append(
                {
                    "currency": currency,
                    "kind": kind,
                    "label": label,
                    "url": url,
                    "checked_at": stamp.isoformat(),
                    "status": "unavailable" if unavailable else "available",
                    "items": [] if unavailable else items,
                    "warning": "Offline unavailable source fixture" if unavailable else None,
                }
            )
    context = {
        "version": forex_macro.VERSION,
        "fetched_at": stamp.isoformat(),
        "currencies": currencies,
        "sources": sources,
        "coverage": "Offline policy fixture",
        "limitations": ["Synthetic policy metadata; no external requests."],
    }
    return {"data": context, "checksum": forex_macro.digest(context)}


@pytest.fixture
def daily_data(monkeypatch, tmp_path):
    path = tmp_path / "market.json"
    path.write_text("{}")
    monkeypatch.setenv("TAURIC_MARKET_CONNECTIONS_FILE", str(path))
    runtime_config.cache_clear()
    state = {"calls": 0, "fault": None}

    def collect(instrument, interval, *, config=None):
        assert interval == "1d"
        state["calls"] += 1
        stamp = now() - timedelta(days=10) if state["fault"] == "outdated" else now()
        bars, provider_time, receipt, context = daily_fixture(instrument, stamp)
        if state["fault"] == "short":
            bars = bars[-10:]
        if state["fault"] == "gap":
            bars.pop(-8)
        if state["fault"] == "missing_latest":
            bars.pop(max(index for index, candle in enumerate(bars) if candle["closed"]))
        if state["fault"] == "malformed":
            bars[-2]["bid"]["close"] = "999"
        return bars, provider_time, receipt, context

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", collect)
    monkeypatch.setattr(forex_macro, "collect", policy_fixture)
    return state


def enqueue(client, instrument=FX, rid=None, profile="cloud"):
    return client.post(
        "/api/v1/analysis",
        json={"request_id": str(rid or uuid4()), "instrument_id": instrument, "profile": profile},
    )


@pytest.mark.parametrize("instrument", [FX, "oanda:fx:GBP_USD", "oanda:fx:USD_JPY"])
def test_fx_admission_binds_independent_daily_evidence(signed_in, daily_data, database, instrument):
    rid = uuid4()
    response = enqueue(signed_in, instrument, rid)
    assert response.status_code == 202, response.text
    request = response.json()["request"]
    assert request["analysts"] == ["fx_market", "fx_macro", "fx_risk"]
    assert request["forex_context"]["adapter_version"] == native_fx.ADAPTER_VERSION
    assert enqueue(signed_in, instrument, rid).json()["id"] == response.json()["id"]
    assert daily_data["calls"] == 1
    with Session(database[0]) as db:
        assert db.get(AnalysisJob, response.json()["id"]).snapshot_id is None
        source = native_fx.evidence(db, request["instrument"], request["forex_context"])
        assert 20 <= len(source["closed_daily_bars"]) <= 60
        assert all(b["closed"] for b in source["closed_daily_bars"])
        assert source["base_currency"] == request["instrument"]["base_currency"]
        assert source["quote_currency"] == request["instrument"]["quote_currency"]
        assert source["quality"]["missing_bars"] == 0
        assert db.scalar(select(func.count()).select_from(MarketSnapshot)) == 1
        wrong = copy.deepcopy(request["forex_context"])
        wrong["checksum"] = "wrong"
        with pytest.raises(ValueError, match="binding mismatch"):
            native_fx.evidence(db, request["instrument"], wrong)


@pytest.mark.parametrize(
    "fault,status",
    [("short", 422), ("gap", 422), ("outdated", 422), ("missing_latest", 422), ("malformed", 502)],
)
def test_fx_bad_daily_evidence_never_queues_a_model_run(
    signed_in, daily_data, database, fault, status
):
    daily_data["fault"] = fault
    response = enqueue(signed_in)
    assert response.status_code == status, response.text
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


@pytest.mark.parametrize(
    "context",
    [
        "chat_context",
        "autonomous_context",
        "portfolio_research_context",
    ],
)
def test_fx_is_manual_daily_research_only(database, context):
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        body = AnalysisRequest(request_id=uuid4(), instrument_id=FX, profile="cloud")
        with pytest.raises(Exception) as error:
            analysis.enqueue(db, owner, body, **{context: {"fixture": True}})
        assert error.value.status_code == 422


@pytest.mark.parametrize("profile", ["cloud", "local"])
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("assessment_mode", ["none", "qualification", "broker"])
def test_fx_worker_uses_native_clients_and_never_the_stock_graph(
    signed_in, daily_data, monkeypatch, tmp_path, profile, legacy, assessment_mode
):
    job = enqueue(signed_in, profile=profile).json()
    if legacy:
        job["request"]["forex_context"]["adapter_version"] = native_fx.LEGACY_VERSION
        job["request"]["forex_context"].pop("macro_snapshot")
        job["request"]["forex_context"].pop("supplemental_snapshot", None)
    qualified = assessment_mode == "qualification"
    broker_setup = assessment_mode == "broker"
    setup_model = SetupModel()
    if broker_setup:
        job["request"]["broker_context"] = setup_context("fx")
        job["request"]["mode"] = "broker_entry_assessment"
    if qualified:
        job["request"]["qualification_context"] = {
            "evidence_checksum": "fixture-exact-checksum",
            "assessment_input": {
                "strategy": {"id": "fixture-strategy"},
                "intended_mode": "research_comparison",
            },
        }
    calls = []

    class LLM:
        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content="Synthetic EUR/USD daily report", tool_calls=[])

        def with_structured_output(self, schema):
            if broker_setup:
                return setup_model.with_structured_output(schema)

            def structured(messages):
                calls.append(messages)
                return {
                    "strategy_version_id": "fixture-strategy",
                    "evidence_checksum": "fixture-exact-checksum",
                    "intended_mode": "research_comparison",
                    "decision": "incomplete",
                    "rationale": "Synthetic exact-evidence review; no trading authority.",
                    "limitations": ["No real model call"],
                }

            return SimpleNamespace(invoke=structured)

    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return SimpleNamespace(get_llm=lambda: LLM())

    modules = {
        "tradingagents": {"__version__": "0.5.2"},
        "tradingagents.default_config": {"DEFAULT_CONFIG": {}},
        "langchain_core.callbacks": {"BaseCallbackHandler": type("Callback", (), {})},
        "tradingagents.llm_clients": {
            "create_llm_client": factory,
            "build_llm_kwargs": lambda config: {"max_retries": config["llm_max_retries"]},
        },
    }
    for name, attrs in modules.items():
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("OPENAI_API_KEY", "restore-test-key")
    result = run(job, tmp_path)
    assert len(calls) == (2 if legacy else 3) + int(qualified)
    if broker_setup:
        assert len(setup_model.calls) == 1
        assert result["broker_setup_assessment"]["decision"] == "approve"
        assert result["broker_setup_assessment"]["execution_authority"] is False
        assert result["effective_config"]["forex_model_invocations"] == (2 if legacy else 3) + 1
    if qualified:
        assert result["strategy_assessment"]["evidence_checksum"] == "fixture-exact-checksum"
        assert result["strategy_assessment"]["execution_authority"] is False
    assert all(call["max_retries"] == 0 for call in factory_calls)
    assert all(
        call["provider"] == ("openai" if profile == "cloud" else "openai_compatible")
        for call in factory_calls
    )
    assert result["adapter_version"] == (
        native_fx.LEGACY_VERSION if legacy else native_fx.ADAPTER_VERSION
    )
    assert result["native_rating"] == "REVIEW"
    assert not result["execution_authority"] and result["governance"] is None
    assert result["forex_evidence"]["binding"] == job["request"]["forex_context"]
    assert "stock providers" not in result["data_context"]
    assert "Headlines do not supply article bodies" in calls[0][0][1]
    if not legacy:
        assert "macro_report" in result["reports"]
        assert result["forex_evidence"]["macro_context"]["currencies"] == ["EUR", "USD"]
    else:
        assert "macro_report" not in result["reports"]
        assert "macro_context" not in result["forex_evidence"]
        assert "Policy news and calendar observations were not supplied" in result["data_context"]
    assert json.loads((tmp_path / "progress.json").read_text())[-1] == "final_trade_decision"


@pytest.mark.parametrize("content,tools", [("", []), ("x", [{"name": "order"}]), ([], [])])
def test_fx_invalid_or_tool_request_output_stops_without_retry(content, tools):
    calls = []

    class LLM:
        def invoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content=content, tool_calls=tools)

    with pytest.raises(ValueError):
        native_fx.research(LLM(), LLM(), {})
    assert len(calls) == 1


def test_fx_snapshot_contents_are_rechecked_against_checksum(database, daily_data):
    with Session(database[0]) as db:
        instrument = spec(FX)
        binding = native_fx.prepare(db, instrument)
        snapshot = db.get(MarketSnapshot, binding["snapshot_id"])
        snapshot.data = {**snapshot.data, "source": "changed"}
        with pytest.raises(ValueError, match="checksum mismatch"):
            native_fx.evidence(db, instrument.model_dump(), binding)
        db.rollback()
