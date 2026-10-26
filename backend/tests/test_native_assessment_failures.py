import json
from types import SimpleNamespace

import pytest

from tauric import native_runner, native_strategy_assessment, worker


def context():
    return {
        "assessment_input": {"strategy": {"id": "exact"}, "intended_mode": "research_comparison"},
        "evidence_checksum": "frozen",
    }


def test_provider_outage_is_a_failure_without_a_synthetic_verdict_or_retry():
    calls = []

    class LLM:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            calls.append(messages)
            raise TimeoutError("sensitive-provider-credential")

    with pytest.raises(native_strategy_assessment.StrategyAssessmentError) as error:
        native_strategy_assessment.assess(SimpleNamespace(deep_thinking_llm=LLM()), context(), {})
    assert error.value.category == "strategy_assessment_provider"
    assert "sensitive-provider-credential" not in str(error.value)
    assert len(calls) == 1


def test_valid_incomplete_model_decision_remains_a_verdict():
    class LLM:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            return {
                "strategy_version_id": "exact", "evidence_checksum": "frozen",
                "intended_mode": "research_comparison", "decision": "incomplete",
                "rationale": "Insufficient evidence", "limitations": ["Missing execution evidence"],
            }

    result = native_strategy_assessment.assess(SimpleNamespace(deep_thinking_llm=LLM()), context(), {})
    assert result["decision"] == "incomplete" and result["execution_authority"] is False


def test_oversized_assessment_is_refused_before_model_invocation():
    with pytest.raises(native_strategy_assessment.StrategyAssessmentError) as error:
        native_strategy_assessment.assess(SimpleNamespace(), context(), {"report": "x" * 200_001})
    assert error.value.category == "strategy_assessment_input"


def test_runner_preserves_safe_failure_category_and_nonzero_exit(tmp_path, monkeypatch, capsys):
    request = tmp_path / "request.json"
    request.write_text("{}")
    monkeypatch.setattr(native_runner.sys, "argv", ["native_runner", str(request)])

    def fail(*args):
        raise native_strategy_assessment.StrategyAssessmentError("strategy_assessment_provider")

    monkeypatch.setattr(native_runner, "run", fail)
    with pytest.raises(SystemExit) as error:
        native_runner.main()
    assert error.value.code == 1
    result = json.loads(capsys.readouterr().out)
    payload, failure = worker.decode_output(json.dumps(result).encode(), 1)
    assert payload is None
    assert failure == native_strategy_assessment.FAILURE_MESSAGES["strategy_assessment_provider"]


@pytest.mark.parametrize("category", ["unknown", {"secret": "credential"}, None])
def test_worker_never_publishes_untrusted_child_error_text(category):
    raw = json.dumps({"error": "sensitive-provider-credential", "error_category": category}).encode()
    assert worker.decode_output(raw, 1) == (None, None)
