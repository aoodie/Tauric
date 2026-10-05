import copy
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_research import bars, import_request, run_request

from tauric import paper, replay_engine, research, strategies, strategy_rules
from tauric.models import Owner, PaperAccount, StrategyVersion, TradingRecord
from tauric.research_contracts import ReplayCreate, ReplayParameters, ResearchBar
from tauric.strategy_contracts import StrategyParameters, StrategyVersionView

RULES = {
    "engine": "closed_bar_breakout_v1",
    "interval": "1m",
    "entry_lookback": 2,
    "minimum_volume_ratio": "2",
    "exit_lookback": 2,
    "take_profit_r": "2",
    "signal_ttl_seconds": 300,
}


def version(**changes):
    return StrategyVersionView(
        id=str(uuid4()),
        strategy_id=str(uuid4()),
        name="Pinned breakout experiment",
        revision=1,
        parameters=StrategyParameters(
            **{
                "label": "v1",
                "description": "Synthetic breakout replay verification, not investment evidence.",
                "quantity_step": "1",
                "capital_limit": "1000",
                "risk_percent": "100",
                "rules": RULES,
                **changes,
            }
        ),
        checksum="unit-test-only",
        reason="Verify shared closed-candle semantics",
        created_at=datetime(2025, 1, 1, tzinfo=UTC),
    )


def parameters(saved, **changes):
    return ReplayParameters(
        **{
            "engine": "closed_bar_breakout_v1",
            "strategy_version_id": saved.id,
            "starting_balance": "1000",
            "allocation_percent": "100",
            "risk_percent": "100",
            "quantity_step": "0.00001",
            "fee_bps": "100",
            "slippage_bps": "100",
            **changes,
        }
    )


def candles():
    result = bars((10, 10, 13, 14, 14, 14, 14))
    result[2]["volume"] = "30"
    return result


def simulate(evidence=None, saved=None, **changes):
    saved = saved or version()
    return replay_engine.simulate(evidence or candles(), parameters(saved, **changes), saved)


def test_entry_matches_shared_rule_evidence_and_pinned_stop():
    evidence = candles()
    saved = version()
    result = simulate(evidence, saved)
    expected = strategy_rules.evaluate_closed_bars(
        [ResearchBar.model_validate(bar) for bar in evidence[:3]], saved.parameters.rules
    )
    assert result.signals[0]["evidence"] == expected
    entry = result.fills[0]
    assert entry["signal_bar_index"] == 2
    assert entry["time"] == evidence[3]["open_time"]
    assert entry["price"] == "14.14" and entry["stop_price"] == "9"
    assert entry["quantity"] == "70" and entry["cause"] == "breakout_next_open"
    assert Decimal(entry["fee"]) == Decimal("9.898")
    assert result.trades[0]["exit_reason"] == "end_of_dataset_close"
    assert Decimal(result.final_equity) == 1000 + Decimal(result.metrics.net_pnl)


@pytest.mark.parametrize(
    ("changes", "trigger"),
    [
        ({"high": "30", "low": "8"}, "stop"),
        ({"high": "30", "low": "13"}, "take_profit"),
        ({"open": "14", "high": "15", "low": "10", "close": "11"}, "channel"),
    ],
)
def test_exit_uses_closed_bar_then_next_open_not_trigger(changes, trigger):
    evidence = candles()
    evidence[4].update(changes)
    evidence[5].update(open="20", high="21")
    result = simulate(evidence)
    entry, exit = result.fills
    assert result.signals[1]["exit_trigger"] == trigger
    assert result.signals[1]["bar_index"] == 4
    assert exit["cause"] == f"{trigger}_next_open"
    assert exit["time"] == evidence[5]["open_time"]
    assert Decimal(exit["price"]) == Decimal("19.8")
    assert Decimal(result.metrics.net_pnl) == Decimal("372.442")
    assert Decimal(result.metrics.total_fees) == Decimal(entry["fee"]) + Decimal(exit["fee"])


def test_opening_gap_does_not_invent_an_intrabar_protective_order():
    evidence = candles()
    evidence[4].update(open="8", low="7", high="15")
    evidence[5].update(open="6", low="5", high="15")
    result = simulate(evidence)
    assert result.signals[1]["exit_trigger"] == "stop"
    assert result.fills[1]["time"] == evidence[5]["open_time"]
    assert result.fills[1]["observed_price"] == "6"


def test_no_volume_no_breakout_no_trade_and_final_signal_cannot_fill():
    evidence = candles()
    evidence[2]["volume"] = "10"
    result = simulate(evidence)
    assert not result.fills and result.metrics.profit_factor is None
    for bar in evidence:
        bar["volume"] = "0"
    assert simulate(evidence).fills == []
    result = simulate(candles()[:3])
    assert result.signals[0]["execution"] == "no_next_bar" and result.fills == []


def test_future_data_cannot_change_earlier_evidence_or_entry():
    evidence = candles()
    changed = copy.deepcopy(evidence)
    for bar in changed[5:]:
        bar.update(open="500", high="510", low="490", close="500", volume="1000")
    saved = version()
    left, right = [simulate(value, saved) for value in (evidence, changed)]
    assert left.fills[0] == right.fills[0]
    assert [signal for signal in left.signals if signal["bar_index"] < 5] == [
        signal for signal in right.signals if signal["bar_index"] < 5
    ]


def test_saved_step_capital_and_capital_based_risk_cannot_be_relaxed():
    saved = version(capital_limit="250", quantity_step="5")
    result = simulate(saved=saved)
    entry = result.fills[0]
    assert entry["quantity"] == "15"
    assert Decimal(entry["quantity"]) * Decimal(entry["price"]) + Decimal(entry["fee"]) <= 250
    saved = version(capital_limit="100", risk_percent="1")
    result = simulate(saved=saved)
    assert result.skipped_entries == 1 and result.fills == []
    assert simulate(risk_percent="0.01").fills == []


def test_entry_at_or_below_saved_stop_is_skipped():
    evidence = candles()
    evidence[3].update(open="8", low="7", high="15")
    result = simulate(evidence)
    assert result.skipped_entries == 1 and result.fills == []
    assert result.final_equity == "1000"


@pytest.mark.parametrize(
    "values",
    [
        {"engine": "closed_bar_breakout_v1"},
        {"strategy_version_id": str(uuid4())},
        {"engine": "unregistered_rules"},
    ],
)
def test_replay_contract_requires_supported_engine_and_version(values):
    with pytest.raises(ValidationError):
        ReplayParameters(**values)


def register(client, **changes):
    saved = version(**changes)
    response = client.post(
        "/api/v1/strategies",
        json={
            "request_id": str(uuid4()),
            "name": saved.name,
            "parameters": saved.parameters.model_dump(mode="json"),
            "reason": saved.reason,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def prepare(client, **changes):
    saved = register(client, **changes)
    response = client.post("/api/v1/research/datasets/import", json=import_request(candles()))
    assert response.status_code == 201, response.text
    request = run_request(response.json()["id"])
    request["parameters"].update(engine="closed_bar_breakout_v1", strategy_version_id=saved["id"])
    return saved, request


def test_saved_version_persistence_export_idempotency_and_no_trading_records(signed_in, database):
    saved, request = prepare(signed_in)
    response = signed_in.post("/api/v1/research/runs", json=request)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["strategy_version"] == saved
    assert run["engine_version"] == replay_engine.BREAKOUT_ENGINE_VERSION
    assert signed_in.post("/api/v1/research/runs", json=request).json()["id"] == run["id"]
    assert signed_in.get("/api/v1/research").json()["runs"][0] == run
    export = signed_in.get(f"/api/v1/research/runs/{run['id']}").json()["data"]
    assert paper.checksum(export["payload"]) == export["checksum"]
    assert paper.checksum(export["payload"]["engine_sources"]) == run["engine_checksum"]
    assert "tauric.strategy_rules" in export["payload"]["engine_sources"]
    with Session(database[0]) as db:
        assert list(db.scalars(select(PaperAccount))) == []
        assert list(db.scalars(select(TradingRecord))) == []
    revised = signed_in.post(
        f"/api/v1/strategies/{saved['strategy_id']}/versions",
        json={
            "request_id": str(uuid4()),
            "expected_revision": 1,
            "parameters": {**saved["parameters"], "label": "v2", "rules": {**RULES, "take_profit_r": "3"}},
            "reason": "Verify old replay remains pinned",
        },
    )
    assert revised.status_code == 201, revised.text
    request["parameters"]["strategy_version_id"] = revised.json()["id"]
    assert signed_in.post("/api/v1/research/runs", json=request).status_code == 409
    assert signed_in.get("/api/v1/research").json()["runs"][0]["strategy_version"] == saved


@pytest.mark.parametrize("change", ["manual", "interval", "missing", "insufficient"])
def test_incompatible_or_unavailable_saved_rules_fail_closed(signed_in, change):
    saved, request = prepare(signed_in, **({"rules": None} if change == "manual" else {}))
    if change == "interval":
        revised = signed_in.post(
            f"/api/v1/strategies/{saved['strategy_id']}/versions",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 1,
                "parameters": {**saved["parameters"], "rules": {**RULES, "interval": "15m"}},
                "reason": "Verify interval mismatch",
            },
        )
        request["parameters"]["strategy_version_id"] = revised.json()["id"]
    elif change == "missing":
        request["parameters"]["strategy_version_id"] = str(uuid4())
    elif change == "insufficient":
        saved = register(signed_in, rules={**RULES, "entry_lookback": 10})
        request["parameters"]["strategy_version_id"] = saved["id"]
    response = signed_in.post("/api/v1/research/runs", json=request)
    assert response.status_code == (404 if change == "missing" else 422), response.text
    assert signed_in.get("/api/v1/research").json()["runs"] == []


def test_other_owner_strategy_and_tampered_version_are_not_replayable(signed_in, database):
    saved, request = prepare(signed_in)
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        with pytest.raises(HTTPException) as error:
            strategies.version(db, SimpleNamespace(id=str(uuid4())), saved["id"])
        assert error.value.status_code == 404
        stranger = SimpleNamespace(id=str(uuid4()))
        stranger_version = version()
        from tauric.models import identifier

        row = StrategyVersion(
            id=identifier(),
            owner_id=owner.id,
            strategy_id=saved["strategy_id"],
            revision=2,
            name=saved["name"],
            request_id=str(uuid4()),
            payload={"parameters": stranger_version.parameters.model_dump()},
            checksum="invalid",
        )
        db.add(row)
        db.flush()
        tampered = ReplayCreate.model_validate({
            **request,
            "parameters": {**request["parameters"], "strategy_version_id": row.id},
        })
        with pytest.raises(HTTPException) as error:
            research.run(db, owner, tampered)
        assert error.value.status_code == 409
        db.rollback()
        assert stranger.id != owner.id


def test_legacy_sma_request_retries_remain_compatible(signed_in, database):
    dataset = signed_in.post("/api/v1/research/datasets/import", json=import_request()).json()
    request = run_request(dataset["id"])
    request["parameters"].pop("strategy_version_id", None)
    response = signed_in.post("/api/v1/research/runs", json=request)
    assert response.status_code == 201, response.text
    assert response.json()["strategy_version"] is None
    assert signed_in.post("/api/v1/research/runs", json=request).json()["id"] == response.json()["id"]
    with Session(database[1]) as db:
        assert db.scalar(text("SELECT count(*) FROM research_records WHERE kind='run'")) == 1
