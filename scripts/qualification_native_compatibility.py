"""Compile the exact structured review against both pinned native routes, offline."""

import json
import os
import socket
from copy import deepcopy
from tempfile import TemporaryDirectory
from unittest.mock import patch

import tradingagents
from tauric.native_strategy_assessment import assess
from tauric.qualification_contracts import NativeStrategyVerdict
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

os.environ["OPENAI_API_KEY"] = "offline-qualification-fixture"
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
assert tradingagents.__version__ == "0.5.2"
checks = []
with (
    TemporaryDirectory() as directory,
    patch.object(
        socket.socket, "connect", side_effect=AssertionError("No network permitted")
    ),
):
    for provider, endpoint in (
        ("openai", None),
        ("openai_compatible", "http://127.0.0.1:9/v1"),
    ):
        config = deepcopy(DEFAULT_CONFIG)
        config.update(
            llm_provider=provider,
            quick_think_llm="gpt-4.1-mini",
            deep_think_llm="gpt-4.1",
            backend_url=endpoint,
            results_dir=directory,
            data_cache_dir=directory,
            memory_log_path=directory + "/memory.md",
        )
        graph = TradingAgentsGraph(config=config)
        # Construct the actual route's structured schema; invocation below uses a fixture.
        structured = graph.deep_thinking_llm.with_structured_output(
            NativeStrategyVerdict
        )
        assert structured is not None
        context = {
            "assessment_input": {
                "strategy": {"id": "exact-version"},
                "intended_mode": "research_comparison",
                "hypothesis": "Frozen hypothesis",
                "stresses": ["frozen stressed costs"],
            },
            "evidence_checksum": "frozen-evidence",
        }

        class OfflineModel:
            def __init__(self):
                self.calls = []

            def with_structured_output(self, schema):
                assert schema is NativeStrategyVerdict
                return self

            def invoke(self, messages):
                self.calls.append(messages)
                return NativeStrategyVerdict(
                    strategy_version_id="exact-version",
                    evidence_checksum="frozen-evidence",
                    intended_mode="research_comparison",
                    decision="suitable",
                    rationale="Offline schema compatibility fixture",
                    limitations=["No connected model assessment"],
                )

        offline = OfflineModel()
        with patch.object(graph, "deep_thinking_llm", offline):
            result = assess(
                graph, context, {"final_trade_decision": "Original report fixture"}
            )
        assert (
            result["decision"] == "suitable" and result["execution_authority"] is False
        )
        assert len(offline.calls) == 1
        evidence = json.loads(offline.calls[0][1][1])
        assert evidence["frozen_evidence"] == context["assessment_input"]
        assert (
            evidence["daily_background_reports"]["final_trade_decision"]
            == "Original report fixture"
        )
        checks.append(
            provider
            + ": native graph deep-model structured review schema and exact evidence extension verified offline"
        )
print(
    json.dumps(
        {
            "runtime": "TradingAgents 0.5.2",
            "checks": checks,
            "provider_model_calls": 0,
            "connected_model_verified": False,
        },
        indent=2,
    )
)
