"""Discovery boundaries, normalized evidence and suppression before model admission."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_broker_execution import broker as broker
from test_broker_execution import create, enable
from test_capital import allocation
from test_governance import ready as ready
from test_paper import action, planned
from test_paper import setup as setup
from test_scanner import BTC, ETH, body, candidate, configured, escalate, scan
from test_scanner import ready_model as ready_model
from test_scanner import scanner_feed as scanner_feed
from test_stock_paper import stock_clock as stock_clock
from test_stock_paper import stocks as stocks
from test_strategies import deploy, registered

from tauric import candidate_pipeline as pipeline
from tauric import candidate_research, scanner, scanner_worker
from tauric.models import AnalysisJob, now
from tauric.scanner_contracts import CandidateRanking, PotentialCandidate, ScannerParameters
from tauric.scanner_discovery import setup_score


def potential(**changes):
    return PotentialCandidate(
        **{
            "candidate_id": "candidate-1",
            "created_at": now(),
            "symbol": "BTCUSDT",
            "instrument_id": BTC,
            "asset_class": "crypto_spot",
            "strategy_id": "strategy-1",
            "strategy_version": "1",
            "direction": "long",
            "timeframe": "15m",
            "setup_score": 0.8,
            "features": {},
            "market_data_timestamp": now(),
            "scanner_id": "scanner-1",
            "scanner_version": "2",
            "profile_version_id": "profile-1",
            "dedupe_key": "setup-1",
            "trace_id": "trace-1",
            **changes,
        }
    )


def test_deterministic_bounded_score():
    assert setup_score({"breakout_percent": "0.2", "volume_ratio": "2"}, "2") == 0.675
    assert setup_score({"breakout_percent": "20", "volume_ratio": "100"}, "2") == 1


@pytest.mark.parametrize(
    "attribute,value,reason",
    [
        ("published", {"setup-1"}, "DUPLICATE"),
        ("processing", {("strategy-1", BTC)}, "PROCESSING"),
        ("open_positions", {("strategy-1", BTC)}, "OPEN_POSITION"),
        ("pending_orders", {("strategy-1", BTC)}, "PENDING_ORDER"),
        ("last_published", {("strategy-1", BTC): now()}, "COOLDOWN"),
    ],
)
def test_gate_reason_codes(attribute, value, reason):
    assert (
        pipeline.gate(potential(), pipeline.GateState(**{attribute: value}), now(), 600) == reason
    )


def test_new_candidate_passes_and_gate_is_strategy_scoped():
    assert (
        pipeline.gate(potential(), pipeline.GateState(open_positions={("other", BTC)}), now())
        is None
    )
    assert (
        pipeline.gate(potential(), pipeline.GateState(open_positions={("*", BTC)}), now())
        == "OPEN_POSITION"
    )


def test_rank_quality_minimum_and_deterministic_ties():
    stamp = now()
    a = potential(candidate_id="a", dedupe_key="a", market_data_timestamp=stamp)
    b = potential(candidate_id="b", dedupe_key="b", instrument_id=ETH, market_data_timestamp=stamp)
    weak = potential(candidate_id="weak", dedupe_key="weak", setup_score=0.6)
    best = potential(candidate_id="best", dedupe_key="best", setup_score=0.9)
    config = CandidateRanking(max_candidates_per_cycle=2)
    selected, rejected = pipeline.rank([weak, b, a, best], config)
    assert [c.candidate_id for c in selected] == ["best", "a"]
    assert rejected == {"weak": "LOW_SCORE", "b": "CYCLE_LIMIT"}
    assert pipeline.rank([a, best, weak, b], config) == (selected, rejected)


def test_rank_asset_and_strategy_limits():
    stamp = now()
    candidates = [
        potential(
            candidate_id=str(i),
            dedupe_key=str(i),
            setup_score=1 - i * 0.05,
            market_data_timestamp=stamp,
            strategy_id="first" if i < 2 else "second",
            asset_class="equity" if i == 3 else "crypto_spot",
        )
        for i in range(4)
    ]
    selected, rejected = pipeline.rank(
        candidates,
        CandidateRanking(
            max_candidates_per_cycle=10,
            max_candidates_per_asset_class=1,
            max_candidates_per_strategy=1,
        ),
    )
    assert [c.candidate_id for c in selected] == ["0", "3"]
    assert rejected["1"] == "ASSET_LIMIT" and rejected["2"] == "ASSET_LIMIT"
    selected, rejected = pipeline.rank(
        candidates,
        CandidateRanking(
            max_candidates_per_cycle=10,
            max_candidates_per_asset_class=10,
            max_candidates_per_strategy=1,
        ),
    )
    assert [c.candidate_id for c in selected] == ["0", "2"]
    assert rejected["1"] == "STRATEGY_LIMIT"


def test_normalized_publication_metrics_dedupe_and_no_llm(
    signed_in, scanner_feed, database, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Discovery must never load model connections")

    monkeypatch.setattr(candidate_research, "load_profiles", forbidden)
    p, _ = configured(signed_in)
    first, request = scan(signed_in, p)
    item = PotentialCandidate.model_validate(first["published_candidates"][0])
    assert item.instrument_id == BTC and item.symbol == "BTC/USDT"
    assert (
        item.direction == "long"
        and item.setup_score == 1
        and item.trace_id == request["request_id"]
    )
    assert item.strategy_id == p["strategy_version_id"] and item.profile_version_id == p["id"]
    assert first["metrics"]["universe_count"] == first["metrics"]["candidate_count"] == 1
    assert (
        first["metrics"]["prefilter_pass_count"]
        == first["metrics"]["strategy_detection_count"]
        == 1
    )
    assert (
        first["metrics"]["published_candidate_count"] == 1
        and first["metrics"]["scan_duration"] >= 0
    )
    second, _ = scan(signed_in, p)
    assert second["published_candidates"] == []
    assert second["results"][0]["gate_reason_code"] == "DUPLICATE"
    assert second["metrics"]["candidate_gate_rejections"] == 1
    view = signed_in.get(f"/api/v1/scanners/candidates/{item.candidate_id}").json()
    assert view["potential_candidates"][0]["dedupe_key"] == item.dedupe_key
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


@pytest.mark.parametrize(
    "setting,reason",
    [
        ({"disabled_instruments": [BTC]}, "DISABLED_INSTRUMENT"),
        ({"maximum_price": "105"}, "PRICE_RANGE"),
        ({"minimum_volume": "60"}, "LOW_VOLUME"),
        ({"maximum_spread_bps": 2}, "SPREAD_UNAVAILABLE"),
    ],
)
def test_cheap_prefilters(signed_in, scanner_feed, setting, reason):
    p, _ = configured(signed_in, **setting)
    run, _ = scan(signed_in, p)
    assert run["results"][0]["reason_code"] == reason and not run["published_candidates"]
    if reason == "DISABLED_INSTRUMENT":
        assert scanner_feed["calls"] == 0


def test_stale_provider_clock_rejected(signed_in, scanner_feed):
    p, _ = configured(signed_in, max_data_age_seconds=10)
    scanner_feed["time"] -= 30_000
    # Keep the bars aligned with the older provider time.
    for row in scanner_feed["rows"]:
        row[0] -= 30_000
        row[6] -= 30_000
    run, _ = scan(signed_in, p)
    assert run["results"][0]["reason_code"] == "STALE_DATA"


def test_no_setup_and_strategy_specific_low_score(signed_in, scanner_feed):
    p, _ = configured(signed_in)
    scanner_feed["rows"][-2][2:6] = ["103", "99", "101", "10"]
    run, _ = scan(signed_in, p)
    assert run["results"][0]["reason_code"] == "NO_SETUP"
    v, _ = registered(signed_in)
    request = body(
        v, universe=[ETH], strategy_scores={v["id"]: {"minimum_score": 0.9, "priority": 10}}
    )
    other = signed_in.put("/api/v1/scanners/profiles", json=request).json()
    scanner_feed["rows"][-2][2:6] = ["111", "99", "110", "20"]
    run, _ = scan(signed_in, other)
    assert run["results"][0]["reason_code"] == "LOW_SCORE"
    assert run["results"][0]["setup_score"] == 0.875


def test_ranking_blocks_direct_research_bypass(signed_in, scanner_feed, ready_model):
    p, _ = configured(signed_in, universe=[BTC, ETH], ranking={"max_candidates_per_cycle": 1})
    run, _ = scan(signed_in, p)
    assert len(run["published_candidates"]) == 1
    rejected = run["results"][1]
    assert rejected["ranking_reason_code"] == "CYCLE_LIMIT"
    c = signed_in.get("/api/v1/scanners/candidates/" + rejected["candidate_id"]).json()
    response, _ = escalate(signed_in, c, p)
    assert response.status_code == 409 and "CYCLE_LIMIT" in response.text


def test_cooldown_shared_between_profiles(signed_in, scanner_feed, monkeypatch):
    p, _ = configured(signed_in, cooldown_seconds=3600)
    scan(signed_in, p)
    from tauric import market

    stamp = now() + timedelta(seconds=900)
    monkeypatch.setattr(scanner, "now", lambda: stamp)
    monkeypatch.setattr(market, "now", lambda: stamp)
    scanner_feed["time"] += 900_000
    for row in scanner_feed["rows"]:
        row[0] += 900_000
        row[6] += 900_000
    request = body(
        {"id": p["strategy_version_id"]}, minimum_volume_ratio="3", cooldown_seconds=3600
    )
    request["name"] = "Second same-strategy profile"
    second = signed_in.put("/api/v1/scanners/profiles", json=request).json()
    run, _ = scan(signed_in, second)
    assert run["results"][0]["gate_reason_code"] == "COOLDOWN"


def test_paper_open_and_pending_states_are_downstream(
    signed_in, setup, scanner_feed, ready_model, database
):
    v, _ = registered(signed_in)
    root, _ = allocation(signed_in, setup[0])
    dep = deploy(signed_in, setup[3], v, root).json()
    p = signed_in.put("/api/v1/scanners/profiles", json=body(v)).json()
    c = candidate(signed_in, p)
    _, _, pending = planned(signed_in, dep)
    assert pending.status_code == 201
    run, _ = scan(signed_in, p)
    assert run["results"][0]["status"] == "matched"  # Discovery is independent of orders.
    assert run["results"][0]["gate_reason_code"] == "PENDING_ORDER"
    fill = signed_in.post(
        f"/api/v1/paper/plans/{pending.json()['id']}/execute", json=action()
    ).json()
    run, _ = scan(signed_in, p)
    assert run["results"][0]["gate_reason_code"] == "OPEN_POSITION"
    response, _ = escalate(signed_in, c, p)
    assert response.status_code == 409 and "OPEN_POSITION" in response.text
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0
    signed_in.post(f"/api/v1/paper/positions/{fill['id']}/close", json=action())
    response, _ = escalate(signed_in, c, p)
    assert response.status_code == 200, response.text


def test_broker_pending_and_filled_state_without_broker_io(
    signed_in, broker, stocks, stock_clock, database, monkeypatch
):
    enable(signed_in)
    _, intent = create(signed_in)
    profile = signed_in.put(
        "/api/v1/scanners/profiles", json=body(stocks[2], universe=["alpaca:equity:AAPL"])
    ).json()

    def forbidden(*args, **kwargs):
        raise AssertionError("Candidate Gate cannot perform broker I/O")

    from tauric import broker_execution

    monkeypatch.setattr(broker_execution, "adapter", forbidden)
    run, _ = scan(signed_in, profile)
    assert run["results"][0]["gate_reason_code"] == "PENDING_ORDER"
    # Lifecycle cursors are updated by the existing broker worker, not the gate.
    from tauric.broker_models import BrokerOrderIntent, BrokerPositionCursor

    with Session(database[0]) as db:
        row = db.get(BrokerOrderIntent, intent["id"])
        row.status = "filled"
        db.add(BrokerPositionCursor(owner_id=row.owner_id, entry_intent_id=row.id, status="open"))
        db.commit()
    run, _ = scan(signed_in, profile)
    assert run["results"][0]["gate_reason_code"] == "OPEN_POSITION"


def test_cadence_and_restart_persistence(signed_in, scanner_feed, database, monkeypatch):
    p, _ = configured(signed_in, automatic_enabled=True)
    with Session(database[0]) as db:
        assert scanner_worker.process_one(db)
    with Session(database[0]) as db:
        assert not scanner_worker.process_one(db)
    stamp = now() + timedelta(seconds=11)
    monkeypatch.setattr(scanner, "now", lambda: stamp)
    with Session(database[0]) as db:
        assert scanner_worker.process_one(db)
    state = signed_in.get("/api/v1/scanners").json()
    assert state["scan_mode"] == "scheduled"
    assert state["runs"][0]["results"][0]["reason_code"] == "CANDLE_UNCHANGED"
    params = ScannerParameters.model_validate(p["parameters"])
    assert not scanner_worker.due(params, stamp, stamp + timedelta(seconds=29))
    assert scanner_worker.due(params, stamp, stamp + timedelta(seconds=30))
    assert scanner_worker.due(params, stamp, stamp + timedelta(seconds=10), watching=True)


def test_session_prefilter_rejects_before_detection(signed_in, scanner_feed, monkeypatch):
    from tauric import market

    original = market.refresh

    def closed_session(*args, **kwargs):
        return original(*args, **kwargs).model_copy(update={"session_state": "closed"})

    monkeypatch.setattr(market, "refresh", closed_session)
    p, _ = configured(signed_in, require_active_session=True)
    run, _ = scan(signed_in, p)
    assert run["results"][0]["reason_code"] == "SESSION_INACTIVE"
    assert run["metrics"]["strategy_detection_count"] == 0


def test_queued_admission_rechecks_new_position(
    signed_in, setup, scanner_feed, ready_model, database
):
    from fastapi import HTTPException

    v, _ = registered(signed_in)
    root, _ = allocation(signed_in, setup[0])
    dep = deploy(signed_in, setup[3], v, root).json()
    p = signed_in.put("/api/v1/scanners/profiles", json=body(v)).json()
    c = candidate(signed_in, p)
    job = escalate(signed_in, c, p)[0].json()
    from test_paper import buy

    buy(signed_in, dep)
    with Session(database[0]) as db, pytest.raises(HTTPException, match="OPEN_POSITION"):
        candidate_research.validate_admission(db, db.get(AnalysisJob, job["id"]))


def test_many_instrument_cycle_has_no_model_work(signed_in, scanner_feed, database, monkeypatch):
    import json
    from pathlib import Path

    from tauric.instrument_catalog import INSTRUMENTS
    from tauric.models import Instrument

    keys = []
    with Session(database[1]) as db:
        for i in range(100):
            definition = {
                **INSTRUMENTS[0],
                "id": f"binance:spot:FIX{i}USDT",
                "symbol": f"FIX{i}/USDT",
                "provider_symbol": f"FIX{i}USDT",
            }
            keys.append(definition["id"])
            db.add(Instrument(id=definition["id"], definition=definition))
        db.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("A scan cycle must not invoke a model")

    monkeypatch.setattr(candidate_research, "load_profiles", forbidden)
    p, _ = configured(signed_in, universe=keys)
    result, _ = scan(signed_in, p)
    assert result["metrics"]["universe_count"] == 100
    assert result["metrics"]["candidate_count"] == 100
    assert result["metrics"]["published_candidate_count"] == 2
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0
    output = Path(__file__).resolve().parents[2] / "artifacts/scanner-pipeline-performance.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "fixture": "100 registered synthetic crypto instruments; offline HTTP transport and disposable PostgreSQL",
                "metrics": result["metrics"],
                "model_calls": 0,
            },
            indent=2,
        )
        + "\n"
    )


def test_same_candle_setup_deduplicates_different_filter_profiles(signed_in, scanner_feed):
    first, _ = configured(signed_in)
    initial, _ = scan(signed_in, first)
    request = body({"id": first["strategy_version_id"]}, minimum_volume_ratio="3")
    request["name"] = "Another activity threshold"
    second = signed_in.put("/api/v1/scanners/profiles", json=request).json()
    result, _ = scan(signed_in, second)
    assert result["results"][0]["candidate_id"] != initial["results"][0]["candidate_id"]
    assert result["results"][0]["gate_reason_code"] == "DUPLICATE"
    assert result["published_candidates"] == []


def test_ranking_deduplicates_and_breaks_complete_ties_by_identity():
    first = potential(candidate_id="a")
    second = first.model_copy(update={"candidate_id": "b"})
    config = CandidateRanking()
    selected, rejected = pipeline.rank([second, first], config)
    assert [c.candidate_id for c in selected] == ["a"]
    assert rejected == {"b": "DUPLICATE"}
    assert pipeline.rank([first, second], config) == (selected, rejected)


def test_duplicate_identity_survives_old_publication_and_new_filter_evidence(
    signed_in, scanner_feed, database
):
    from tauric import paper
    from tauric.models import Owner, ScannerEvent

    scanner_feed["rows"][-2][2:6] = ["111", "99", "110", "20"]
    first, _ = configured(signed_in, ranking={"minimum_score": 1})
    result, _ = scan(signed_in, first)
    item = result["potential_candidates"][0]
    assert result["published_candidates"] == []
    payload = {
        "action": "published",
        "reason": "Historic fixture publication",
        "details": {"candidate": item},
    }
    with Session(database[0]) as db:
        db.add(
            ScannerEvent(
                owner_id=db.scalar(select(Owner)).id,
                candidate_id=item["candidate_id"],
                request_id="old-publication",
                payload=payload,
                checksum=paper.checksum(payload),
                created_at=now() - timedelta(days=2),
            )
        )
        db.commit()
    request = body({"id": first["strategy_version_id"]}, minimum_volume_ratio="1")
    request["name"] = "Different volume filter on same setup"
    second = signed_in.put("/api/v1/scanners/profiles", json=request).json()
    result, _ = scan(signed_in, second)
    assert result["results"][0]["gate_reason_code"] == "DUPLICATE"
