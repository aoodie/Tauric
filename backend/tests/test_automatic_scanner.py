"""Multiple closed-bar setups and durable, bounded downstream automatic admission."""

from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_independent_scanners import revise, standalone
from test_scanner import ETH, scan
from test_scanner import ready_model as ready_model
from test_scanner import scanner_feed as scanner_feed

from tauric import candidate_research, scanner
from tauric import candidate_research_worker as coordinator
from tauric.models import AnalysisJob, Owner, ScannerAnalysis, ScannerEvent

FAMILIES = ["closed_bar_breakout_v1", "closed_bar_breakdown_v1", "trend_pullback_v1"]


def candles(feed, closes, last):
    start = feed["rows"][-1][0]
    feed["rows"] = [
        [
            start + i * 900000,
            str(close),
            str(close + 2),
            str(close - 2),
            str(close),
            "10",
            start + (i + 1) * 900000 - 1,
        ]
        for i, close in zip(range(-6, 0), [closes[0], *closes, last], strict=True)
    ]
    feed["rows"][-1] = [
        start - 900000,
        "100",
        str(max(100, last) + 1),
        str(min(100, last) - 1),
        str(last),
        "50",
        start - 1,
    ]
    feed["rows"].append([start, "100", "1000", "1", "999", "1000", start + 900000 - 1])


@pytest.mark.parametrize(
    "closes,last,detector,direction",
    [
        ([101, 101, 101, 101], 90, "closed_bar_breakdown_v1", "short"),
        ([100, 102, 106, 101], 103, "trend_pullback_v1", "long"),
        ([106, 104, 100, 105], 102, "trend_pullback_v1", "short"),
    ],
)
def test_multiple_setup_families_use_closed_bars(
    signed_in, scanner_feed, closes, last, detector, direction
):
    candles(scanner_feed, closes, last)
    profile, _ = standalone(signed_in, detector_ids=FAMILIES, lookback=4)
    result, _ = scan(signed_in, profile)
    assert scanner_feed["calls"] == 2  # One provider time/candle fetch for every family.
    assert result["metrics"]["strategy_evaluation_count"] == 3
    item = result["published_candidates"][0]
    assert item["strategy_id"] == detector and item["direction"] == direction
    detail = signed_in.get("/api/v1/scanners/candidates/" + item["candidate_id"]).json()
    assert detail["detector_id"] == detector and detail["direction"] == direction
    assert detail["execution_authority"] is False


def test_multi_setup_ranking_shares_one_cycle_limit(signed_in, scanner_feed):
    candles(scanner_feed, [100, 102, 106, 101], 110)
    profile, _ = standalone(
        signed_in, detector_ids=FAMILIES, lookback=4, ranking={"max_candidates_per_cycle": 1}
    )
    result, _ = scan(signed_in, profile)
    assert len(result["potential_candidates"]) == 2
    assert len(result["published_candidates"]) == 1
    assert result["metrics"]["ranking_rejections"] == 1
    assert len({c["dedupe_key"] for c in result["potential_candidates"]}) == 2


@pytest.mark.parametrize("asset", ["stock", "fx"])
@pytest.mark.parametrize(
    "detector,closes,last,direction",
    [
        ("closed_bar_breakdown_v1", [101, 101, 101, 101], 90, "short"),
        ("trend_pullback_v1", [100, 102, 106, 101], 103, "long"),
        ("trend_pullback_v1", [106, 104, 100, 105], 102, "short"),
    ],
)
def test_new_families_preserve_stock_and_fx_evidence(
    monkeypatch, asset, detector, closes, last, direction
):
    from datetime import UTC, datetime

    from test_market_adapters import bar, spec
    from test_stock_execution import feed as stock_feed

    from tauric import scanner_discovery, stock_execution
    from tauric.market_calendars import default_context, periods
    from tauric.market_contracts import MarketView
    from tauric.scanner_contracts import ScannerParameters

    if asset == "stock":
        value, stamp = stock_feed()
        monkeypatch.setattr(stock_execution, "now", lambda: stamp)
    else:
        stamp = datetime(2026, 7, 6, 15, 0, 5, tzinfo=UTC)
        instrument = spec("oanda:fx:EUR_USD")
        context = default_context(instrument)
        value = MarketView(
            instrument=instrument,
            interval="15m",
            snapshot_id="fx-fixture",
            candles=[
                bar(op, end, sides=True)
                for op, end in periods(context, "15m", stamp - timedelta(hours=2), stamp)
                if end <= stamp
            ],
            state="fresh",
            received_at=stamp,
            provider_time=stamp,
            source="oanda_v20",
            checksum="fixture",
            data_context=context,
            session_state="open",
        )
    for candle, price in zip(value.candles[-5:], [*closes, last], strict=True):
        candle.open = candle.close = str(price)
        candle.high, candle.low = str(price + 1), str(price - 1)
        candle.volume = "10"
        if asset == "fx":
            from tauric.market_contracts import PriceOHLC

            candle.bid = PriceOHLC(
                open=str(price - 0.1),
                close=str(price - 0.1),
                high=str(price + 0.9),
                low=str(price - 1.1),
            )
            candle.ask = PriceOHLC(
                open=str(price + 0.1),
                close=str(price + 0.1),
                high=str(price + 1.1),
                low=str(price - 0.9),
            )
    value.candles[-1].volume = "50"
    parameters = ScannerParameters(
        enabled=True,
        universe=[value.instrument.id],
        interval="15m",
        detector_id=detector,
        lookback=4,
        minimum_volume_ratio="2",
        candidate_ttl_seconds=300,
        max_analyses_per_day=1,
        model_profile="local",
    )
    found, failure = scanner_discovery.detect(value, parameters)
    assert failure is None and found["direction"] == direction
    assert found["evidence"]["volume_type"] == value.instrument.volume_type
    assert found["evidence"]["data_context"]["calendar_id"] == value.data_context.calendar_id
    assert len(found["evidence"]["bars"]) == 5
    if asset == "stock":
        assert found["evidence"]["data_context"]["feed"] == "iex"
    else:
        assert "Provider tick activity" in found["reasons"][1]


def test_coordinator_consumes_only_ranked_publications(
    signed_in, scanner_feed, ready_model, database
):
    candles(scanner_feed, [100, 102, 106, 101], 110)
    profile, _ = standalone(
        signed_in,
        detector_ids=FAMILIES,
        lookback=4,
        ranking={"max_candidates_per_cycle": 1},
        automatic_research_enabled=True,
    )
    result, _ = scan(signed_in, profile)
    assert len(result["potential_candidates"]) == 2
    assert work(database)
    assert not work(database)
    jobs = rows(database, AnalysisJob)
    assert len(jobs) == 1
    assert (
        jobs[0].request["scanner_context"]["candidate_id"]
        == result["published_candidates"][0]["candidate_id"]
    )


def work(database):
    with Session(database[0]) as db:
        return coordinator.process_one(db)


def rows(database, model):
    with Session(database[0]) as db:
        return list(db.scalars(select(model)))


def test_automatic_admission_is_separate_opt_in_and_restart_safe(
    signed_in, scanner_feed, ready_model, database
):
    profile, request = standalone(signed_in, automatic_enabled=True)
    scan(signed_in, profile)
    assert not work(database)
    profile = revise(
        signed_in,
        profile,
        request,
        parameters={**profile["parameters"], "automatic_research_enabled": True},
    )
    scan(signed_in, profile)
    # A genuinely new instrument publication is required for the new revision.
    profile = revise(
        signed_in, profile, request, parameters={**profile["parameters"], "universe": [ETH]}
    )
    scan(signed_in, profile)
    assert not rows(database, AnalysisJob)
    assert work(database)
    assert not work(database)
    admissions = rows(database, ScannerAnalysis)
    jobs = rows(database, AnalysisJob)
    assert len(jobs) == len(admissions) == 1
    assert admissions[0].charged and admissions[0].payload["automatic"]
    assert jobs[0].request["scanner_context"]["automatic"] is True
    with Session(database[0]) as db:
        candidate_research.validate_admission(db, db.get(AnalysisJob, jobs[0].id))
    assert any(
        e.payload["action"] == "automatic_research_admitted" for e in rows(database, ScannerEvent)
    )


def test_daily_allowance_defers_without_blocking_other_profiles(
    signed_in, scanner_feed, ready_model, database, monkeypatch
):
    first, _ = standalone(signed_in, automatic_research_enabled=True, max_analyses_per_day=0)
    second, _ = standalone(
        signed_in, name="Other asset", universe=[ETH], automatic_research_enabled=True
    )
    scan(signed_in, first)
    scan(signed_in, second)
    assert work(database)  # Deferred until the next UTC day, no job or charge.
    assert work(database)  # Other profile still gets its turn.
    assert not work(database)
    assert len(rows(database, AnalysisJob)) == 1
    assert rows(database, AnalysisJob)[0].instrument_id == ETH
    events = [
        e
        for e in rows(database, ScannerEvent)
        if e.payload["action"] == "automatic_research_deferred"
    ]
    assert events[0].payload["details"]["reason_code"] == "DAILY_ALLOWANCE"


def test_automatic_research_disabled_before_queue_start(
    signed_in, scanner_feed, ready_model, database
):
    profile, request = standalone(signed_in, automatic_research_enabled=True)
    scan(signed_in, profile)
    assert work(database)
    revise(
        signed_in,
        profile,
        request,
        parameters={**profile["parameters"], "automatic_research_enabled": False},
    )
    from fastapi import HTTPException

    with Session(database[0]) as db, pytest.raises(HTTPException):
        candidate_research.validate_admission(
            db, db.get(AnalysisJob, rows(database, AnalysisJob)[0].id)
        )


def test_saved_heartbeat_exposes_coordinator_readiness(signed_in, database):
    assert not signed_in.get("/api/v1/scanners").json()["automatic_research_ready"]
    with Session(database[0]) as db:
        coordinator.heartbeat(db)
    assert signed_in.get("/api/v1/scanners").json()["automatic_research_ready"]


def test_automatic_admission_respects_shared_budget(signed_in, scanner_feed, ready_model, database):
    from tauric import admission_budget
    from tauric.portfolio_research_contracts import SharedAdmissionBudgetSave

    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        admission_budget.save(
            db,
            owner,
            SharedAdmissionBudgetSave(
                request_id=uuid4(),
                expected_revision=0,
                daily_job_limit=0,
                reason="Zero shared allowance in disposable automatic admission test",
            ),
        )
    profile, _ = standalone(signed_in, automatic_research_enabled=True)
    scan(signed_in, profile)
    assert work(database)
    assert not rows(database, AnalysisJob) and not rows(database, ScannerAnalysis)
    assert not work(database)


def test_ignored_publication_cannot_automatically_admit(
    signed_in, scanner_feed, ready_model, database
):
    profile, _ = standalone(signed_in, automatic_research_enabled=True)
    result, _ = scan(signed_in, profile)
    cid = result["published_candidates"][0]["candidate_id"]
    response = signed_in.post(
        f"/api/v1/scanners/candidates/{cid}/actions",
        json={
            "request_id": str(uuid4()),
            "action": "ignore",
            "reason": "Ignore before automatic admission",
        },
    )
    assert response.status_code == 200
    assert work(database)
    assert not rows(database, AnalysisJob)


def test_failed_native_work_is_reused_without_another_paid_job(
    signed_in, scanner_feed, ready_model, database, monkeypatch
):
    from datetime import UTC, datetime

    from tauric import market

    # Keep both fetches inside one candle and UTC date, even near a real boundary.
    stamp = datetime.fromtimestamp(scanner_feed["rows"][-1][0] / 1000, UTC) + timedelta(minutes=1)
    scanner_feed["time"] = int(stamp.timestamp() * 1000)
    monkeypatch.setattr(market, "now", lambda: stamp)
    monkeypatch.setattr(scanner, "now", lambda: stamp)
    first, _ = standalone(signed_in, automatic_research_enabled=True)
    scan(signed_in, first)
    assert work(database)
    from tauric import analysis

    with Session(database[0]) as db:
        job = db.scalar(select(AnalysisJob))
        analysis.finish(db, job.id, error="Disposable failed native work; no retry")
    # Distinct detector parameters produce distinct evidence on the same candle,
    # but share daily research. Suppression still deduplicates the original family.
    second, _ = standalone(
        signed_in,
        name="Other detector",
        detector_id="trend_pullback_v1",
        lookback=4,
        automatic_research_enabled=True,
    )
    candles(scanner_feed, [100, 102, 106, 101], 103)
    stamp += timedelta(seconds=35)
    scanner_feed["time"] += 35000
    result, _ = scan(signed_in, second)
    assert result["published_candidates"], result
    assert work(database)
    assert len(rows(database, AnalysisJob)) == 1
    links = rows(database, ScannerAnalysis)
    assert len(links) == 2 and sum(link.charged for link in links) == 1
    assert sum(link.payload["reused"] for link in links) == 1
