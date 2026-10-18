"""One job per process, preserving the upstream graph and public report artifacts."""

import contextlib
import json
import os
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from .analysis import UPSTREAM_REVISION
from .model_profiles import KEY_ENV, load_profiles
from .native_scope import NATIVE_ASSET_TYPES, daily_scope

REPORT_FIELDS = (
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "investment_plan",
    "trader_investment_plan",
    "final_trade_decision",
    "risk_report",
    "macro_report",
)


def artifact(state, rating, request, settings, runtime_version):
    reports = {
        key: state[key] for key in REPORT_FIELDS if isinstance(state.get(key), str) and state[key]
    }
    for source, target in (
        ("investment_debate_state", "research_debate"),
        ("risk_debate_state", "risk_debate"),
    ):
        debate = state.get(source)
        if isinstance(debate, dict) and isinstance(debate.get("history"), str):
            reports[target] = debate["history"]
    if not reports.get("final_trade_decision"):
        raise ValueError("Native graph returned no final report")
    return {
        "runtime": "TauricResearch/TradingAgents",
        "version": runtime_version,
        "upstream_revision": UPSTREAM_REVISION,
        "request": request,
        "effective_config": settings,
        "reports": reports,
        "native_rating": str(rating),
        "execution_authority": False,
        "governance": None,
        "data_context": daily_scope(request.get("instrument", {}).get("asset_type"))
        + " Not point-in-time replay or intraday analysis. No trading authority.",
    }


def run(request, directory):
    spec = request["request"]["instrument"]
    product_setup = spec["asset_type"] in {"commodity", "cfd"} and request["request"].get(
        "broker_context"
    )
    if (
        NATIVE_ASSET_TYPES.get(spec["asset_type"]) != spec["native_asset_type"]
        and not product_setup
    ):
        raise ValueError("Unsupported native asset route")
    broker_context = request["request"].get("broker_context")
    if broker_context:
        from .execution_scope import provider as execution_provider
        from .market_contracts import InstrumentView
        from .native_broker_setup import verify

        evidence, binding = verify(broker_context)
        expected_provider = (
            execution_provider(
                InstrumentView.model_validate(
                    {k: v for k, v in spec.items() if k != "native_research_ready"}
                )
            )
            if product_setup
            else {"fx": "oanda", "equity": "alpaca", "etf": "alpaca"}.get(spec["asset_type"])
        )
        if (
            request["request"].get("mode") != "broker_entry_assessment"
            or binding["instrument_id"] != spec["id"]
            or evidence["instrument"]["asset_type"] != spec["asset_type"]
            or binding["provider"] != expected_provider
            or spec["asset_type"] not in {"fx", "equity", "etf", "commodity", "cfd"}
            or any(
                request["request"].get(key)
                for key in (
                    "qualification_context",
                    "autonomous_context",
                    "scanner_context",
                    "chat_context",
                    "portfolio_research_context",
                )
            )
        ):
            raise ValueError("Broker setup does not match the original native research route")
    import tradingagents
    from langchain_core.callbacks import BaseCallbackHandler
    from tradingagents.default_config import DEFAULT_CONFIG

    if tradingagents.__version__ != "0.5.2":
        raise ValueError("Unsupported native runtime version")
    from sqlalchemy.orm import Session

    from .database import engine

    with Session(engine()) as db:
        profiles, _ = load_profiles(db)
    profile = profiles.get(request["profile"])
    if (
        not profile
        or not profile.configured()
        or profile.fingerprint() != request["request"]["profile_fingerprint"]
    ):
        raise ValueError("Connection changed or is unconfigured")
    if request["request"]["trade_date"] != datetime.now(UTC).date().isoformat():
        raise ValueError("Queued daily run has expired")
    key_env = KEY_ENV.get(profile.provider)
    if key_env:
        os.environ[key_env] = profile.api_key.get_secret_value()
    # Disable outbound tracing regardless of the surrounding environment.
    os.environ["LANGSMITH_TRACING"] = "false"
    os.environ["LANGCHAIN_TRACING_V2"] = "false"
    config = deepcopy(DEFAULT_CONFIG)
    config.update(request["request"]["settings"])
    config.update(
        {
            "llm_provider": profile.provider,
            "quick_think_llm": profile.quick_model,
            "deep_think_llm": profile.deep_model,
            "backend_url": profile.base_url or None,
            "results_dir": str(directory / "native-reports"),
            "data_cache_dir": str(directory / "cache"),
            "memory_log_path": str(directory / "memory.md"),
            "checkpoint_enabled": True,
        }
    )
    if product_setup:
        from tradingagents.llm_clients import build_llm_kwargs, create_llm_client

        from .native_broker_setup import assess as assess_broker

        config["llm_max_retries"] = 0
        kwargs = build_llm_kwargs(config)
        deep = create_llm_client(
            provider=profile.provider,
            model=profile.deep_model,
            base_url=profile.base_url or None,
            **kwargs,
        ).get_llm()
        assessment = assess_broker(deep, broker_context, {}, "SETUP_REVIEW")
        return {
            "runtime": "Tauric exact OANDA product setup / TradingAgents LLM client",
            "version": tradingagents.__version__,
            "upstream_revision": UPSTREAM_REVISION,
            "request": request["request"],
            "effective_config": {
                "llm_provider": profile.provider,
                "deep_think_llm": profile.deep_model,
                "llm_max_retries": 0,
                "model_invocations": assessment["model_invocations"],
            },
            "reports": {"final_trade_decision": assessment["rationale"]},
            "native_rating": "SETUP_REVIEW",
            "broker_setup_assessment": assessment,
            "execution_authority": False,
            "governance": None,
            "data_context": "Separate exact-product assessment of frozen broker/strategy evidence. No daily graph report, futures substitution, strategy qualification or order authority.",
        }
    if spec["asset_type"] == "fx":
        from tradingagents.llm_clients import build_llm_kwargs, create_llm_client

        from .native_fx import evidence, research

        with Session(engine()) as db:
            source = evidence(db, spec, request["request"].get("forex_context", {}))
        config["llm_max_retries"] = 0
        kwargs = build_llm_kwargs(config)
        quick = create_llm_client(
            provider=profile.provider,
            model=profile.quick_model,
            base_url=profile.base_url or None,
            **kwargs,
        ).get_llm()
        deep = create_llm_client(
            provider=profile.provider,
            model=profile.deep_model,
            base_url=profile.base_url or None,
            **kwargs,
        ).get_llm()

        def fx_progress(reports):
            temporary = directory / "progress.tmp"
            temporary.write_text(json.dumps(reports))
            temporary.replace(directory / "progress.json")

        state = research(quick, deep, source, fx_progress)
        public_settings = {
            "llm_provider": profile.provider,
            "quick_think_llm": profile.quick_model,
            "deep_think_llm": profile.deep_model,
            "llm_max_retries": 0,
            "model_kwargs": kwargs,
            "forex_model_invocations": 3 if source.get("macro_context") else 2,
        }
        result = artifact(
            state, "REVIEW", request["request"], public_settings, tradingagents.__version__
        )
        result["runtime"] = "Tauric Forex daily extension / TradingAgents LLM clients"
        result["adapter_version"] = source["binding"]["adapter_version"]
        result["forex_evidence"] = source
        if not source.get("macro_context"):
            result["data_context"] = (
                "Legacy Forex daily price-only extension using frozen closed OANDA bid/ask observations. Policy news and calendar observations were not supplied. Not intraday or point-in-time replay; no trading authority."
            )
        context = request["request"].get("qualification_context")
        if context:
            from types import SimpleNamespace

            from .native_strategy_assessment import assess

            result["strategy_assessment"] = assess(
                SimpleNamespace(deep_thinking_llm=deep), context, result["reports"]
            )
            result["effective_config"]["forex_model_invocations"] += 1
        broker_context = request["request"].get("broker_context")
        if broker_context:
            from .native_broker_setup import assess as assess_broker

            result["broker_setup_assessment"] = assess_broker(
                deep, broker_context, result["reports"], result["native_rating"]
            )
            result["effective_config"]["forex_model_invocations"] += result[
                "broker_setup_assessment"
            ]["model_invocations"]
        return result
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    completed = set()

    class Progress(BaseCallbackHandler):
        def on_chain_end(self, outputs, **kwargs):
            if not isinstance(outputs, dict):
                return
            temporary = directory / "progress.tmp"
            # Parallel analyst callbacks serialize writes under a process-local lock.
            with progress_lock:
                for key in REPORT_FIELDS:
                    if outputs.get(key):
                        completed.add(key)
                temporary.write_text(json.dumps(sorted(completed)))
                temporary.replace(directory / "progress.json")

    import threading

    progress_lock = threading.Lock()
    graph = TradingAgentsGraph(
        selected_analysts=request["request"]["analysts"],
        debug=False,
        config=config,
        callbacks=[Progress()],
    )
    state, rating = graph.propagate(
        spec["native_symbol"],
        request["request"]["trade_date"],
        asset_type=spec["native_asset_type"],
    )
    # Whitelist public reports. Raw messages, provider reasoning traces and tokens are not exposed.
    public_settings = {
        key: value
        for key, value in config.items()
        if key not in {"results_dir", "data_cache_dir", "memory_log_path"}
    }
    result = artifact(state, rating, request["request"], public_settings, tradingagents.__version__)
    context = request["request"].get("qualification_context")
    if context:
        from .native_strategy_assessment import assess

        result["strategy_assessment"] = assess(graph, context, result["reports"])
        result["data_context"] = (
            "Exact frozen strategy evidence reviewed by the native graph's deep model through the explicit strategy assessment extension. Original reports and instrument rating remain current daily background; they are not historical or intraday strategy approval. No trading authority."
        )
    broker_context = request["request"].get("broker_context")
    if broker_context:
        from .native_broker_setup import assess as assess_broker

        result["broker_setup_assessment"] = assess_broker(
            graph.deep_thinking_llm, broker_context, result["reports"], result["native_rating"]
        )
        result["effective_config"]["broker_setup_model_invocations"] = result[
            "broker_setup_assessment"
        ]["model_invocations"]
    return result


def main():
    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text())
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = run(request, request_path.parent)
        print(json.dumps(result, allow_nan=False))
    except Exception:
        # SDK exceptions can include endpoint credentials or request data. Do not emit them.
        print(
            json.dumps(
                {
                    "error": "Native analysis failed. Check model access, provider availability and runtime configuration."
                }
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
