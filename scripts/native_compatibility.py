"""Compile both native model routes offline; never submit an AI request."""

import json
import os
import socket
import sys
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "backend"))
os.environ["OPENAI_API_KEY"] = "offline-compatibility-fixture"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"

import tradingagents
from tauric.analysis import UPSTREAM_REVISION
from tauric.model_profiles import KEY_ENV
from tauric.native_fx import research
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.llm_clients import create_llm_client
from tradingagents.llm_clients.api_key_env import PROVIDER_API_KEY_ENV

expected = {
    "Market Analyst",
    "Sentiment Analyst",
    "News Analyst",
    "Fundamentals Analyst",
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
    "Trader",
    "Aggressive Analyst",
    "Neutral Analyst",
    "Conservative Analyst",
    "Portfolio Manager",
}
assert tradingagents.__version__ == "0.5.2"
assert all(PROVIDER_API_KEY_ENV[key] == value for key, value in KEY_ENV.items())
results = []
with (
    TemporaryDirectory() as temporary,
    patch.object(
        socket.socket,
        "connect",
        side_effect=AssertionError("Offline check attempted network access"),
    ),
):
    for provider, quick, deep, endpoint in (
        ("openai", "gpt-4.1-mini", "gpt-4.1", None),
        ("openai_compatible", "offline-quick", "offline-deep", "http://127.0.0.1:9/v1"),
    ):
        config = deepcopy(DEFAULT_CONFIG)
        config.update(
            {
                "llm_provider": provider,
                "quick_think_llm": quick,
                "deep_think_llm": deep,
                "backend_url": endpoint,
                "results_dir": temporary,
                "data_cache_dir": temporary,
                "memory_log_path": str(Path(temporary) / "memory.md"),
                "checkpoint_enabled": True,
            }
        )
        for asset, ticker, analysts in (
            ("crypto", "BTC-USD", ["market", "social", "news", "fundamentals"]),
            ("stock", "AAPL", ["market", "social", "news", "fundamentals"]),
            ("stock", "SPY", ["market", "social", "news"]),
        ):
            graph = TradingAgentsGraph(selected_analysts=analysts, config=config)
            nodes = expected if ticker != "SPY" else expected - {"Fundamentals Analyst"}
            assert nodes <= set(graph.workflow.nodes)
            if ticker == "SPY":
                assert "Fundamentals Analyst" not in graph.workflow.nodes
            with patch(
                "tradingagents.graph.trading_graph.resolve_instrument_identity",
                return_value={"name": ticker},
            ):
                context = graph.resolve_instrument_context(ticker, asset)
            assert ticker in context
            results.append(
                f"{provider}: {ticker} native {asset} graph compiled offline"
            )
        quick_llm = create_llm_client(
            provider=provider, model=quick, base_url=endpoint, max_retries=0
        ).get_llm()
        deep_llm = create_llm_client(
            provider=provider, model=deep, base_url=endpoint, max_retries=0
        ).get_llm()
        with patch.object(
            type(quick_llm),
            "invoke",
            return_value=SimpleNamespace(
                content="Offline Forex compatibility report", tool_calls=[]
            ),
        ):
            reports = research(
                quick_llm,
                deep_llm,
                {
                    "pair": "EUR/USD",
                    "base_currency": "EUR",
                    "quote_currency": "USD",
                    "macro_context": {"fixture": "offline compatibility only"},
                },
            )
        assert "REVIEW" in reports["final_trade_decision"]
        results.append(
            f"{provider}: Forex extension native clients and price/policy/risk reports verified offline"
        )
result = {
    "upstream_revision": UPSTREAM_REVISION,
    "version": tradingagents.__version__,
    "passed": results,
    "model_calls": 0,
    "connected_analysis_verified": False,
}
(root / "artifacts/native-compatibility.json").write_text(json.dumps(result, indent=2))
print(
    "Native compatibility passed for cloud and external-local routes. No model calls made."
)
