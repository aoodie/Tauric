"""Exact demo-entry review boundaries with a synthetic model; no provider calls."""

import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tauric import native_broker_setup
from tauric.models import now


def context(asset="equity", **changes):
    evidence = {
        "request": {
            "request_id": str(uuid4()),
            "strategy_version_id": str(uuid4()),
            "instrument_id": "oanda:fx:EUR_USD" if asset == "fx" else "alpaca:equity:AAPL",
            "provider": "oanda" if asset == "fx" else "alpaca",
            "quantity": "2",
        },
        "instrument": {"asset_type": asset},
        "expires_at": (now() + timedelta(minutes=5)).isoformat(),
        "entry_evidence_ready": True,
        **changes,
    }
    return {
        "adapter_version": native_broker_setup.ADAPTER_VERSION,
        "evidence_checksum": native_broker_setup.checksum(evidence),
        "assessment_input": evidence,
    }


class Model:
    def __init__(self, decision="approve", **changes):
        self.calls = []
        self.decision = decision
        self.changes = changes

    def with_structured_output(self, schema):
        def invoke(messages):
            self.calls.append(messages)
            if self.decision == "exception":
                raise RuntimeError("test-secret-never-persist")
            binding = json.loads(messages[1][1])["required_binding"]
            return {
                **binding,
                "decision": self.decision,
                "rationale": "Synthetic setup assessment, not market or performance evidence.",
                "limitations": ["No real model call; separate bound governance is required."],
                **self.changes,
            }

        return SimpleNamespace(invoke=invoke)


@pytest.mark.parametrize("asset,rating", [("equity", "BUY"), ("etf", "BUY"), ("fx", "REVIEW")])
@pytest.mark.parametrize("decision", ["approve", "reject", "incomplete"])
def test_separate_verdict_preserves_reports_rating_and_exact_binding(asset, rating, decision):
    frozen = context(asset)
    reports = {"final_trade_decision": "Original daily report", "market_report": "Original"}
    before = copy.deepcopy((frozen, reports))
    model = Model(decision)
    result = native_broker_setup.assess(model, frozen, reports, rating)
    _, expected = native_broker_setup.verify(frozen)
    assert all(result[key] == value for key, value in expected.items())
    assert result["decision"] == decision and result["model_invocations"] == 1
    assert result["execution_authority"] is False and len(model.calls) == 1
    assert (frozen, reports) == before
    sent = json.loads(model.calls[0][1][1])
    assert sent["original_native_rating"] == rating and sent["original_daily_reports"] == reports


@pytest.mark.parametrize(
    "changes",
    [
        {"setup_id": str(uuid4())},
        {"strategy_version_id": str(uuid4())},
        {"instrument_id": "alpaca:equity:MSFT"},
        {"provider": "oanda"},
        {"quantity": "3"},
        {"side": "sell"},
        {"evidence_checksum": "b" * 64},
        {"execution_authority": True},
        {"limitations": []},
        {"decision": "buy"},
    ],
)
def test_model_cannot_change_binding_or_grant_authority(changes):
    model = Model(**changes)
    result = native_broker_setup.assess(model, context(), {}, "BUY")
    assert result["decision"] == "incomplete" and len(model.calls) == 1
    assert result["execution_authority"] is False


@pytest.mark.parametrize("reason", ["expired", "not_ready", "oversized", "hold", "sell"])
def test_ineligible_evidence_never_calls_model(reason):
    frozen = context(
        **(
            {"expires_at": (now() - timedelta(seconds=1)).isoformat()}
            if reason == "expired"
            else {}
        ),
        **({"entry_evidence_ready": False} if reason == "not_ready" else {}),
    )
    model = Model()
    reports = {"market_report": "é" * 200_000} if reason == "oversized" else {}
    rating = reason.upper() if reason in {"hold", "sell"} else "BUY"
    result = native_broker_setup.assess(model, frozen, reports, rating)
    assert result["decision"] in {"reject", "incomplete"}
    assert model.calls == [] and result["model_invocations"] == 0


def test_provider_failure_never_retries_or_persists_secret():
    model = Model("exception")
    result = native_broker_setup.assess(model, context(), {}, "BUY")
    assert len(model.calls) == 1 and result["decision"] == "incomplete"
    assert "test-secret" not in json.dumps(result)


def test_corrupt_frozen_evidence_is_rejected_before_model():
    frozen = context()
    frozen["assessment_input"]["request"]["quantity"] = "999"
    model = Model()
    with pytest.raises(ValueError, match="integrity"):
        native_broker_setup.assess(model, frozen, {}, "BUY")
    assert model.calls == []


@pytest.mark.parametrize("change", ["instrument", "asset", "provider", "mode", "mixed"])
def test_native_runner_rejects_changed_research_route_before_sdk_import(tmp_path, change):
    from tauric.native_runner import run

    frozen = context()
    if change == "instrument":
        frozen["assessment_input"]["request"]["instrument_id"] = "alpaca:equity:MSFT"
    elif change == "asset":
        frozen["assessment_input"]["instrument"]["asset_type"] = "fx"
    elif change == "provider":
        frozen["assessment_input"]["request"]["provider"] = "oanda"
    frozen["evidence_checksum"] = native_broker_setup.checksum(frozen["assessment_input"])
    request = {
        "mode": "current_daily_research" if change == "mode" else "broker_entry_assessment",
        "broker_context": frozen,
        "instrument": {
            "id": "alpaca:equity:AAPL",
            "asset_type": "equity",
            "native_asset_type": "stock",
        },
    }
    if change == "mixed":
        request["qualification_context"] = {"fixture": True}
    with pytest.raises(ValueError, match="research route"):
        run({"request": request}, tmp_path)


@pytest.mark.parametrize("asset", ["commodity", "cfd"])
def test_exact_product_assessment_has_no_daily_buy_requirement(asset):
    frozen = context(asset)
    frozen["assessment_input"]["request"].update(
        provider="oanda", instrument_id="oanda:" + asset + ":XAU_USD"
    )
    frozen["evidence_checksum"] = native_broker_setup.checksum(frozen["assessment_input"])
    model = Model()
    result = native_broker_setup.assess(model, frozen, {}, "SETUP_REVIEW")
    assert result["decision"] == "approve" and result["execution_authority"] is False
    assert len(model.calls) == 1
    assert "financing" in str(model.calls[0]).lower()
