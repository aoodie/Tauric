"""Calendar vintages, shared request recovery and honest catalog coverage."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from tauric import intelligence_catalog, provider_requests, scanner_context, scanner_universe
from tauric.intelligence_models import IntelligenceReceipt
from tauric.models import Owner, now


def save_calendar(db, owner, stamp, payload):
    row = IntelligenceReceipt(
        owner_id=owner.id,
        source_key="scanner_calendar:USD",
        created_at=stamp,
        payload=payload,
        checksum=intelligence_catalog.digest(payload),
    )
    db.add(row)
    db.flush()
    return row


def test_provider_budget_is_shared_and_survives_new_worker(database, monkeypatch):
    stamp = now()
    monkeypatch.setattr(provider_requests, "now", lambda: stamp)
    first = provider_requests.Budget(database[0], "alpaca_stocks")
    for _ in range(20):
        first.acquire()
    with Session(database[0]) as db:
        assert provider_requests.remaining(db, "alpaca_stocks") == 0
    restarted = provider_requests.Budget(database[0], "alpaca_stocks")
    with pytest.raises(HTTPException) as rejected:
        restarted.acquire()
    assert rejected.value.status_code == 429
    stamp += timedelta(seconds=1)
    restarted.acquire()
    restarted.backoff(10)
    stamp += timedelta(seconds=9)
    with pytest.raises(HTTPException):
        provider_requests.Budget(database[0], "alpaca_stocks").acquire()
    # The other read-only provider has an independent budget.
    provider_requests.Budget(database[0], "oanda_v20").acquire()
    stamp += timedelta(seconds=2)
    provider_requests.Budget(database[0], "alpaca_stocks").acquire()


def calendar(stamp, **changes):
    return {
        "status": "available",
        "known_at": stamp.isoformat(),
        "coverage_start": (stamp - timedelta(days=1)).isoformat(),
        "coverage_end": (stamp + timedelta(days=1)).isoformat(),
        "items_omitted": 0,
        "items": [],
        **changes,
    }


def test_calendar_asof_does_not_use_later_receipts_or_guess_missing_times(database):
    stamp = now()
    spec = SimpleNamespace(asset_type="equity", base_currency="ABC", quote_currency="USD")
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        save_calendar(
            db, owner, stamp - timedelta(minutes=2), calendar(stamp - timedelta(minutes=2))
        )
        assert scanner_context.evidence(db, owner, spec, stamp)["status"] == "clean"
        save_calendar(
            db,
            owner,
            stamp + timedelta(minutes=1),
            calendar(
                stamp + timedelta(minutes=1),
                items=[{"id": "release", "time": (stamp + timedelta(minutes=10)).isoformat()}],
            ),
        )
        assert scanner_context.evidence(db, owner, spec, stamp)["status"] == "clean"
        assert (
            scanner_context.evidence(db, owner, spec, stamp + timedelta(minutes=2))["status"]
            == "event"
        )
        save_calendar(
            db,
            owner,
            stamp + timedelta(minutes=3),
            calendar(stamp + timedelta(minutes=3), items=[{"id": "undated", "time": None}]),
        )
        assert (
            scanner_context.evidence(db, owner, spec, stamp + timedelta(minutes=4))["status"]
            == "unknown"
        )
        assert (
            scanner_context.evidence(db, owner, spec, stamp + timedelta(hours=2))["status"]
            == "unknown"
        )


def test_catalog_retries_without_double_counting_then_advances_without_false_completion():
    def metrics(attempt):
        return {
            "catalog_selected": ["A", "B"],
            "universe_count": 2,
            "universe_offset": 0,
            "next_universe_offset": 0,
            "catalog_slice_attempt": attempt,
            "catalog_prior_covered": 0,
            "catalog_prior_failed": 0,
        }

    results = [
        {"instrument_id": "A", "interval": "15m", "status": "filtered"},
        {"instrument_id": "B", "interval": "15m", "status": "unavailable"},
    ]
    first = metrics(1)
    scanner_universe.finish(first, results, ["15m"])
    assert first["catalog_covered_count"] == 0 and first["retry_after"] > 0
    assert first["catalog_pass_complete"] is False and first["deferred_count"] == 2
    exhausted = metrics(3)
    scanner_universe.finish(exhausted, results, ["15m"])
    assert exhausted["next_universe_offset"] == 0 and exhausted["recovery_exhausted_count"] == 1
    assert exhausted["catalog_covered_count"] == 1 and exhausted["catalog_failed_count"] == 1
    assert exhausted["catalog_pass_complete"] is False
    complete = metrics(1)
    scanner_universe.finish(
        complete,
        [
            {"instrument_id": symbol, "interval": interval, "status": "filtered"}
            for symbol in ("A", "B")
            for interval in ("15m", "1h")
        ],
        ["15m", "1h"],
    )
    assert complete["catalog_pass_complete"] is True and complete["catalog_covered_count"] == 2


def test_catalog_progress_resumes_from_persisted_receipts_after_worker_restart(
    signed_in, database, monkeypatch
):
    from uuid import uuid4

    from test_independent_scanners import standalone

    from tauric import paper
    from tauric.instrument_discovery import DiscoveredInstrument
    from tauric.models import ScannerProfileVersion, ScannerRun
    from tauric.scanner_contracts import ScannerParameters

    revision = 1
    calls = []
    monkeypatch.setattr(scanner_universe, "stored_connection", lambda *_: (None, revision, None))

    def catalog(*_):
        calls.append(revision)
        return [
            DiscoveredInstrument(
                symbol="ZZ" + chr(65 + i),
                name="Fixture listing",
                exchange="TEST",
                category="us_equity",
                available=True,
            )
            for i in range(7)
        ], now().isoformat()

    monkeypatch.setattr(scanner_universe.instrument_discovery, "catalog", catalog)
    profile, _ = standalone(
        signed_in, universe=[], catalog={"provider": "alpaca", "instruments_per_cycle": 3}
    )
    seen = []
    for expected_offset in (0, 3, 6):
        # A new session represents a restarted process; progress comes from immutable runs.
        with Session(database[0]) as db:
            owner = db.scalar(select(Owner))
            saved = db.get(ScannerProfileVersion, profile["id"])
            params = ScannerParameters.model_validate(saved.payload["parameters"])
            identities, metrics = scanner_universe.resolve(db, owner, saved, params)
            assert metrics["universe_offset"] == expected_offset
            seen.extend(identities)
            scanner_universe.finish(
                metrics,
                [
                    {"instrument_id": identity, "interval": "15m", "status": "filtered"}
                    for identity in identities
                ],
                ["15m"],
            )
            payload = {"metrics": metrics}
            db.add(
                ScannerRun(
                    owner_id=owner.id,
                    profile_version_id=saved.id,
                    request_id=str(uuid4()),
                    payload=payload,
                    checksum=paper.checksum(payload),
                )
            )
            db.commit()
    assert len(set(seen)) == 7 and len(calls) == 1
    assert metrics["catalog_pass_complete"] is True and metrics["catalog_covered_count"] == 7
    revision = 2
    with Session(database[0]) as db:
        owner = db.scalar(select(Owner))
        saved = db.get(ScannerProfileVersion, profile["id"])
        _, metrics = scanner_universe.resolve(db, owner, saved, params)
        assert metrics["connection_revision"] == 2
    assert len(calls) == 2
