"""Chronology, session semantics and the research queue's authority boundary."""

import copy
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import market, paper, quant_features, quant_research, quant_worker
from tauric.market_calendars import default_context
from tauric.quant_contracts import ExperimentCreate, SessionReportCreate, SessionWindow
from tauric.quant_models import QuantRecord
from tauric.research_models import ResearchRecord

FX = "oanda:fx:EUR_USD"
START = datetime(2025, 1, 6, tzinfo=UTC)


def bars(count=360):
    result = []
    for i in range(count):
        price = 1.1 + 0.001 * math.sin(i / 7)
        bar = {
            "open_time": (START + timedelta(minutes=i)).isoformat(),
            "close_time": (START + timedelta(minutes=i + 1)).isoformat(),
            "open": f"{price:.8f}",
            "high": f"{price + 0.0002:.8f}",
            "low": f"{price - 0.0002:.8f}",
            "close": f"{price + 0.00003:.8f}",
            "volume": "100",
            "closed": True,
        }
        for side, offset in (("bid", -0.00005), ("ask", 0.00005)):
            bar[side] = {
                k: f"{float(bar[k]) + offset:.8f}" for k in ("open", "high", "low", "close")
            }
        result.append(bar)
    return result


def experiment(dataset_id=None, **values):
    return ExperimentCreate(
        request_id=uuid4(),
        dataset_id=dataset_id or uuid4(),
        name="FX held-out study",
        hypothesis="Past features may explain next returns",
        reason="Owner requested research",
        engine="qlib",
        train_end=START + timedelta(minutes=160),
        validation_end=START + timedelta(minutes=250),
        test_end=START + timedelta(minutes=360),
        asset_costs={"asset_type": "fx", "spread_bps": "0", "financing_daily_bps": "0"},
        **values,
    )


@pytest.mark.parametrize("day,hour", [("2025-03-28", 8), ("2025-03-31", 7)])
def test_london_session_uses_local_dst(day, hour):
    window = SessionWindow(
        timezone="Europe/London", opening="08:00", closing="17:00", weekdays=list(range(5))
    )
    opening = datetime.fromisoformat(day).replace(hour=hour, tzinfo=UTC)
    assert quant_features.in_session(opening, opening + timedelta(hours=1), window)
    assert not quant_features.in_session(
        opening - timedelta(minutes=1), opening + timedelta(hours=1), window
    )


def test_overnight_session_belongs_to_opening_weekday_and_rejects_partial_bar():
    window = SessionWindow(timezone="UTC", opening="22:00", closing="02:00", weekdays=[4])
    midnight = datetime(2025, 1, 11, tzinfo=UTC)
    assert quant_features.in_session(midnight, midnight + timedelta(hours=1), window)
    assert not quant_features.in_session(
        midnight + timedelta(hours=1), midnight + timedelta(hours=3), window
    )
    assert not quant_features.in_session(
        midnight + timedelta(days=1), midnight + timedelta(days=1, hours=1), window
    )


def test_all_hours_accepts_provider_daily_bars_crossing_midnight_and_dst():
    opening = datetime(2025, 3, 9, 21, tzinfo=UTC)
    assert quant_features.in_session(opening, opening + timedelta(hours=23), SessionWindow())
    assert quant_features.in_session(opening, opening + timedelta(hours=25), SessionWindow())
    london = SessionWindow(timezone="Europe/London", opening="08:00", closing="17:00")
    assert not quant_features.in_session(opening, opening + timedelta(hours=24), london)


def test_purge_removes_targets_crossing_each_cutoff_and_future_prices_do_not_change_past_features():
    request = experiment()
    original = bars()
    features = quant_features.build(original, request)
    for name, end in (
        ("train", request.train_end),
        ("validation", request.validation_end),
        ("test", request.test_end),
    ):
        assert all(
            quant_features.stamp(r["target_time"]) <= end for r in features["partitions"][name]
        )
    changed = copy.deepcopy(original)
    changed[300]["close"] = "12"
    assert quant_features.vector(changed, 290, 12) == quant_features.vector(original, 290, 12)
    assert features["forecast"]["time"] == original[-1]["close_time"]
    for row in features["partitions"]["train"]:
        entry, exit = original[row["index"] + 1], original[row["index"] + request.horizon_bars]
        assert row["label"] == float(exit["bid"]["close"]) / float(entry["ask"]["open"]) - 1


def test_evidence_available_after_cutoff_is_rejected():
    with pytest.raises(ValidationError):
        SessionReportCreate(
            request_id=uuid4(),
            dataset_id=uuid4(),
            name="Historical session",
            hypothesis="A known policy event",
            reason="Owner requested research",
            start=START,
            as_of=START + timedelta(hours=2),
            evidence=[
                {
                    "title": "Policy event",
                    "source": "Official release",
                    "content": "Known only later",
                    "published_at": START + timedelta(hours=1),
                    "available_at": START + timedelta(hours=3),
                }
            ],
        )


def save_dataset(client, database):
    with Session(database[0]) as db:
        context = default_context(market.instrument(db, FX)).model_dump(mode="json")
    response = client.post(
        "/api/v1/research/datasets/import",
        json={
            "request_id": str(uuid4()),
            "name": "Frozen Forex history",
            "instrument_id": FX,
            "interval": "1m",
            "source": "Synthetic regression fixture",
            "reason": "Verify independent research",
            "data_context": context,
            "candles": bars(),
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_lab_report_cutoff_idempotency_ownership_and_checksum(signed_in, database):
    dataset = save_dataset(signed_in, database)
    body = {
        "request_id": str(uuid4()),
        "dataset_id": dataset,
        "name": "Historical session",
        "hypothesis": "Study the observed session",
        "reason": "Owner requested research",
        "mode": "forecast",
        "start": START.isoformat(),
        "as_of": (START + timedelta(hours=2)).isoformat(),
    }
    created = signed_in.post("/api/v1/research/lab/reports", json=body)
    assert created.status_code == 201, created.text
    report = created.json()
    assert report["payload"]["bars"] == 120
    assert report["payload"]["evidence_end"] == (START + timedelta(hours=2)).isoformat()
    assert report["payload"]["authority"] == "research_only"
    assert not report["payload"]["point_in_time_verified"]
    assert signed_in.post("/api/v1/research/lab/reports", json=body).json()["id"] == report["id"]
    assert (
        signed_in.post(
            "/api/v1/research/lab/reports", json={**body, "name": "Different request"}
        ).status_code
        == 409
    )
    with Session(database[0]) as db:
        with pytest.raises(HTTPException) as hidden:
            quant_research.owned(
                db, SimpleNamespace(id=str(uuid4())), report["id"], "session_report"
            )
        assert hidden.value.status_code == 404


def test_job_claim_cancel_and_late_worker_completion_cannot_replace_cancellation(
    signed_in, database
):
    dataset = save_dataset(signed_in, database)
    request = experiment(dataset).model_dump(mode="json")
    created = signed_in.post("/api/v1/research/lab/experiments", json=request)
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    with Session(database[0]) as db:
        claimed = quant_worker.claim(db, "qlib")
        assert claimed[0] == identity
        token = claimed[1]
        assert quant_worker.claim(db, "qlib") is None
    assert (
        signed_in.post(f"/api/v1/research/lab/experiments/{identity}/cancel").json()["payload"][
            "status"
        ]
        == "cancelled"
    )
    with Session(database[0]) as db:
        assert not quant_worker.finish(db, identity, token, result={"engine": "qlib"})
        row = db.get(QuantRecord, identity)
        assert row.payload["status"] == "cancelled" and paper.checksum(row.payload) == row.checksum
        assert db.scalar(select(ResearchRecord.id).where(ResearchRecord.kind == "run")) is None


def test_fx_requires_actual_quotes_and_does_not_charge_spread_twice(signed_in, database):
    dataset = save_dataset(signed_in, database)
    request = experiment(dataset).model_dump(mode="json")
    request["asset_costs"]["spread_bps"] = "2"
    assert signed_in.post("/api/v1/research/lab/experiments", json=request).status_code == 422
    with Session(database[1]) as db:
        db.execute(text("ALTER TABLE research_records DISABLE TRIGGER immutable_history"))
        row = db.get(ResearchRecord, dataset)
        payload = copy.deepcopy(row.payload)
        payload["data_context"]["price_side"] = "midpoint"
        for bar in payload["candles"]:
            bar.pop("bid")
            bar.pop("ask")
        row.payload, row.checksum = payload, paper.checksum(payload)
        db.commit()
    request["asset_costs"]["spread_bps"] = "0"
    assert signed_in.post("/api/v1/research/lab/experiments", json=request).status_code == 422


def test_historical_retrieval_is_bounded_read_only_and_recoverable(
    signed_in, database, monkeypatch
):
    with Session(database[0]) as db:
        context = default_context(market.instrument(db, FX)).model_dump(mode="json")
    calls = []

    def collect(spec, interval, **kwargs):
        calls.append(kwargs)
        return bars(), START + timedelta(minutes=360), datetime.now(UTC), context

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", collect)
    request = {
        "request_id": str(uuid4()),
        "name": "Historical range",
        "instrument_id": FX,
        "interval": "1m",
        "start": START.isoformat(),
        "end": (START + timedelta(minutes=360)).isoformat(),
        "reason": "Owner requested history",
    }
    result = signed_in.post("/api/v1/research/lab/datasets/history", json=request)
    assert result.status_code == 201, result.text
    assert result.json()["bar_count"] == 360
    assert (
        signed_in.post("/api/v1/research/lab/datasets/history", json=request).json()["id"]
        == result.json()["id"]
    )
    assert len(calls) == 1 and calls[0]["start"] == START
    request.update(request_id=str(uuid4()), end=(START + timedelta(days=40)).isoformat())
    assert signed_in.post("/api/v1/research/lab/datasets/history", json=request).status_code == 422
    assert len(calls) == 1


def test_database_freezes_job_inputs_and_terminal_results(signed_in, database):
    source = save_dataset(signed_in, database)
    created = signed_in.post(
        "/api/v1/research/lab/experiments", json=experiment(source).model_dump(mode="json")
    )
    assert created.status_code == 201, created.text
    identity = created.json()["id"]
    with Session(database[0]) as db:
        row = db.get(QuantRecord, identity)
        payload = copy.deepcopy(row.payload)
        payload["features"]["partitions"]["test"][0]["label"] = 999
        row.payload = payload
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()
        claimed = quant_worker.claim(db, "qlib")
        assert quant_worker.finish(
            db, identity, claimed[1], result={"engine": "qlib", "authority": "research_only"}
        )
        row = db.get(QuantRecord, identity)
        payload = copy.deepcopy(row.payload)
        payload["result"]["engine"] = "altered"
        row.payload = payload
        with pytest.raises(DBAPIError):
            db.commit()
