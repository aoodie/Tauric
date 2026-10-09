"""Trailing evidence, confirmed pivots, Decimal classification and offline scoring."""

import copy
from datetime import UTC, datetime, timedelta
from decimal import Decimal, getcontext
from types import SimpleNamespace

import pytest

from tauric import scanner_scoring
from tauric.market_contracts import Candle
from tauric.scanner_contracts import ScannerParameters, SetupAssessment


def feed(interval="15m", *, trend=False, descending=False):
    stamp = datetime(2026, 10, 9, 6, tzinfo=UTC)
    bars = []
    for index in range(300):
        close = (
            (Decimal(100) + Decimal(index) / 10 * (-1 if descending else 1))
            if trend
            else (
                Decimal(100) + (Decimal("0.01") if index % 2 else Decimal("-0.01"))
                if index >= 280
                else Decimal(100) + (Decimal(2) if index % 2 else Decimal(-2))
            )
        )
        low, high = (
            min(close, Decimal(100)) - Decimal("0.5"),
            max(close, Decimal(100)) + Decimal("0.5"),
        )
        if not trend and index == 260:
            high = Decimal(115)
        if not trend and index == 294:
            low = Decimal(99)
        if not trend and index == 299:
            close, high = Decimal(103), Decimal(104)
        step = scanner_scoring.SECONDS[interval]
        end = stamp - timedelta(seconds=(300 - index) * step)
        bars.append(
            Candle(
                open_time=end - timedelta(seconds=step),
                close_time=end,
                open="100",
                high=str(high),
                low=str(low),
                close=str(close),
                volume="300" if index == 299 else "100",
                closed=True,
            )
        )
    return SimpleNamespace(
        instrument=SimpleNamespace(
            id="fixture:BTC", volume_type="exchange_base", asset_type="crypto_spot"
        ),
        interval=interval,
        candles=bars,
        state="fresh",
        gaps=0,
        provider_time=stamp,
        received_at=stamp,
        snapshot_id="fixture-" + interval,
        checksum="a" * 64,
    )


def score(source=None, **kwargs):
    source = source or feed()
    contexts = kwargs.pop(
        "higher_feeds", {"1h": feed("1h", trend=True), "4h": feed("4h", trend=True)}
    )
    return scanner_scoring.calculate_setup_score(
        source,
        {"prior_high": "101"},
        None,
        higher_feeds=contexts,
        macro=kwargs.pop("macro", {"status": "not_applicable"}),
        **kwargs,
    )


def test_scoring_is_decimal_repeatable_and_rewards_closed_trend_alignment():
    aligned = score()
    opposite = score(
        higher_feeds={
            "1h": feed("1h", trend=True, descending=True),
            "4h": feed("4h", trend=True, descending=True),
        }
    )
    assert aligned["contributions"]["trend"] == "0.25000000"
    assert opposite["contributions"]["trend"] == "0.00000000"
    assert Decimal(aligned["score"]) > Decimal(opposite["score"])
    SetupAssessment.model_validate(aligned)
    old = getcontext().prec
    try:
        getcontext().prec = 6
        assert score() == aligned
    finally:
        getcontext().prec = old


def test_future_and_forming_bars_cannot_change_asof_score():
    original = feed()
    stamp = original.candles[-1].close_time
    baseline = score(original, stamp=stamp)
    changed = copy.deepcopy(original)
    future = original.candles[-1].model_copy(
        update={"close_time": stamp + timedelta(days=1), "close": "100000"}
    )
    changed.candles.extend([future, future.model_copy(update={"closed": False})])
    contexts = {"1h": feed("1h", trend=True), "4h": feed("4h", trend=True)}
    for context in contexts.values():
        context.candles.append(future)
    assert score(changed, higher_feeds=contexts, stamp=stamp) == baseline


def test_incomplete_context_or_unknown_calendar_never_gets_high_band():
    unknown = score(macro={"status": "unknown"})
    missing = score(higher_feeds={})
    assert unknown["macro_penalty"] == "0.20000000"
    assert "economic_calendar" in unknown["missing_evidence"]
    assert unknown["rating"] != "HIGH" and missing["rating"] != "HIGH"
    assert "higher_timeframe_trend" in missing["missing_evidence"]
    assert score(macro={"status": "event"})["macro_penalty"] == "0.20000000"


def test_pivot_requires_both_confirming_closed_bars():
    bars = feed().candles[-6:]
    for index, low in enumerate(("98", "97", "95", "96", "97", "98")):
        bars[index] = bars[index].model_copy(update={"low": low})
    assert Decimal("95") not in scanner_scoring.pivots(bars[:4], "bid")
    assert Decimal("95") in scanner_scoring.pivots(bars[:5], "bid")


def test_fifty_instruments_score_offline_without_provider_or_llm_calls(monkeypatch):
    import httpx

    def forbidden(*args, **kwargs):
        raise AssertionError("Scoring attempted external I/O")

    monkeypatch.setattr(httpx.Client, "request", forbidden)
    for _ in range(50):
        result = score()
        assert Decimal(0) <= Decimal(result["score"]) <= Decimal(1)
        assert (result["rating"] == "HIGH") == (Decimal(result["score"]) >= Decimal("0.82"))
        assert result["execution_authority"] is False


def test_legacy_parameters_and_distinct_timeframes():
    parameters = dict(
        enabled=True,
        universe=["fixture:BTC"],
        interval="15m",
        detector_id="closed_bar_breakout_v1",
        lookback=20,
        minimum_volume_ratio="1.5",
        candidate_ttl_seconds=900,
        max_analyses_per_day=0,
        model_profile="local",
    )
    legacy = ScannerParameters(**parameters)
    assert legacy.scoring_version == "legacy_v1" and legacy.scan_intervals == ["15m"]
    assert ScannerParameters(**{**parameters, "intervals": ["5m", "1h"]}).scan_intervals == [
        "5m",
        "1h",
    ]
    with pytest.raises(ValueError, match="timeframe"):
        ScannerParameters(**{**parameters, "intervals": ["5m", "5m"]})


def test_short_geometry_and_score_mirror_long_without_future_pivots():
    long = score()
    source = feed()
    contexts = {"1h": feed("1h", trend=True), "4h": feed("4h", trend=True)}
    for item in [source, *contexts.values()]:
        item.candles = [
            b.model_copy(
                update={
                    "open": str(200 - Decimal(b.open)),
                    "high": str(200 - Decimal(b.low)),
                    "low": str(200 - Decimal(b.high)),
                    "close": str(200 - Decimal(b.close)),
                }
            )
            for b in item.candles
        ]
    short = scanner_scoring.calculate_setup_score(
        source,
        {"prior_low": "99"},
        None,
        higher_feeds=contexts,
        macro={"status": "not_applicable"},
        direction="short",
    )
    for factor in ("pattern", "activity", "trend", "geometry"):
        assert short["contributions"][factor] == long["contributions"][factor]
    # Normalized Bollinger width divides by the local mean; reflecting prices
    # around a constant does not preserve that normalization exactly.
    assert short["rating"] == long["rating"] == "HIGH"
    assert short["geometry"]["risk_reward_ratio"] == long["geometry"]["risk_reward_ratio"]
    assert Decimal(short["geometry"]["suggested_stop"]) > Decimal(
        short["geometry"]["suggested_entry"]
    )


def test_exact_threshold_contract_never_rounds_incomplete_evidence_into_high():
    original = score()
    for value, band in (
        ("0.819999999999", "MODERATE"),
        ("0.82", "HIGH"),
        ("0.65", "MODERATE"),
        ("0.649999999999", "LOW"),
    ):
        SetupAssessment.model_validate(
            {**original, "score": value, "rating": band, "missing_evidence": []}
        )
    with pytest.raises(ValueError):
        SetupAssessment.model_validate({**original, "score": "0.819999999999", "rating": "HIGH"})
    with pytest.raises(ValueError):
        SetupAssessment.model_validate(
            {**original, "score": "0.82", "rating": "HIGH", "missing_evidence": ["calendar"]}
        )


@pytest.fixture
def multifactor_market(database, monkeypatch):
    from sqlalchemy.orm import Session

    from tauric import market, paper
    from tauric.market_contracts import InstrumentView, MarketView
    from tauric.models import Instrument, MarketSnapshot, now

    stamp = now()
    with Session(database[0]) as db:
        seed = market.instrument(db, "binance:spot:BTCUSDT")
        ids = []
        for i in range(50):
            spec = seed.model_copy(update={"id": f"fixture:crypto:{i}", "symbol": f"FIXTURE{i}"})
            db.add(
                Instrument(
                    id=spec.id,
                    definition=spec.model_dump(mode="json", exclude={"native_research_ready"}),
                )
            )
            ids.append(spec.id)
        db.commit()

    cache = {}

    def refresh(db, identity, interval, *, commit=False):
        key = identity, interval
        if key not in cache:
            source = feed(interval, trend=interval in {"1h", "4h"})
            step = scanner_scoring.SECONDS[interval]
            closed_at = datetime.fromtimestamp(int(stamp.timestamp()) // step * step, UTC)
            delta = closed_at - source.candles[-1].close_time
            bars = [
                b.model_copy(
                    update={"open_time": b.open_time + delta, "close_time": b.close_time + delta}
                )
                for b in source.candles
            ]
            payload = {"candles": [b.model_dump(mode="json") for b in bars]}
            snapshot = MarketSnapshot(
                instrument_id=identity,
                interval=interval,
                received_at=stamp,
                provider_time=stamp,
                data=payload,
                checksum=paper.checksum(payload),
            )
            db.add(snapshot)
            db.flush()
            spec = InstrumentView.model_validate(db.get(Instrument, identity).definition)
            cache[key] = MarketView(
                instrument=spec,
                interval=interval,
                snapshot_id=snapshot.id,
                candles=bars,
                state="fresh",
                received_at=stamp,
                provider_time=stamp,
                source=spec.provider,
                checksum=snapshot.checksum,
                gaps=0,
            )
        return cache[key]

    monkeypatch.setattr(market, "refresh", refresh)
    return ids


def test_complete_fifty_instrument_scanner_runs_offline(signed_in, multifactor_market, monkeypatch):
    import httpx
    from test_independent_scanners import standalone
    from test_scanner import scan

    original_request = httpx.Client.request

    def forbidden(client, *args, **kwargs):
        if client is signed_in:
            return original_request(client, *args, **kwargs)
        raise AssertionError("The deterministic scanner attempted external I/O")

    monkeypatch.setattr(httpx.Client, "request", forbidden)
    profile, _ = standalone(
        signed_in,
        "Fifty synthetic instruments",
        universe=multifactor_market,
        scoring_version="multifactor_v1",
        minimum_setup_score=0,
        minimum_volume_ratio="1.5",
        lookback=20,
        max_analyses_per_day=0,
    )
    result, _ = scan(signed_in, profile)
    assert result["metrics"]["universe_count"] == 50
    assert result["metrics"]["strategy_evaluation_count"] == 50
    assert signed_in.get("/api/v1/scanners").json()["model_calls_during_scan"] == 0
    assert len(result["potential_candidates"]) == 50
    assert all(
        c["assessment"]["execution_authority"] is False for c in result["potential_candidates"]
    )


def test_timeframes_keep_distinct_evidence_and_repeated_bars_reuse_candidates(
    signed_in, multifactor_market
):
    from test_independent_scanners import standalone
    from test_scanner import scan

    profile, _ = standalone(
        signed_in,
        "Two timeframe watch",
        universe=multifactor_market[:2],
        intervals=["5m", "15m"],
        scoring_version="multifactor_v1",
        minimum_setup_score=0,
        minimum_volume_ratio="1.5",
        lookback=20,
        max_analyses_per_day=0,
    )
    first, _ = scan(signed_in, profile)
    assert first["metrics"]["strategy_evaluation_count"] == 4
    initial = signed_in.get("/api/v1/scanners").json()["candidates"]
    assert len(initial) == 4 and {c["interval"] for c in initial} == {"5m", "15m"}
    scan(signed_in, profile)
    repeated = signed_in.get("/api/v1/scanners").json()["candidates"]
    assert {c["id"] for c in repeated} == {c["id"] for c in initial}
