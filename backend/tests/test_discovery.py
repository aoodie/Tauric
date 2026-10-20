"""History reuse, causal strategies and bounded research orchestration."""

import copy
import math
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tauric import (
    discovery,
    discovery_ai,
    discovery_signals,
    history_store,
    market,
    quant_worker,
)
from tauric.discovery_contracts import DiscoveryParameters, ResearchStrategy
from tauric.discovery_models import DiscoveryRecord
from tauric.market_calendars import default_context, periods
from tauric.models import Owner, now
from tauric.quant_models import QuantRecord

FX = "oanda:fx:EUR_USD"


def candles(spec, interval, start, end):
    result = []
    for left, right in periods(default_context(spec), interval, start, end):
        if right > end:
            continue
        price = 1.1 + 0.002 * math.sin(left.timestamp() / 10000)
        bar = {
            "open_time": left.isoformat(),
            "close_time": right.isoformat(),
            "open": f"{price:.8f}",
            "high": f"{price + 0.0005:.8f}",
            "low": f"{price - 0.0005:.8f}",
            "close": f"{price + 0.0001:.8f}",
            "volume": "100",
            "closed": True,
        }
        for name, offset in (("bid", -0.00005), ("ask", 0.00005)):
            bar[name] = {
                k: f"{float(bar[k]) + offset:.8f}" for k in ("open", "high", "low", "close")
            }
        result.append(bar)
    return result


@pytest.fixture
def provider(monkeypatch):
    calls = []
    connection = SimpleNamespace(oanda=SimpleNamespace(environment="practice", account_id="test"))
    monkeypatch.setattr(history_store, "connections", lambda db: connection)

    def collect(spec, interval, *, config, start, end):
        calls.append((interval, start, end))
        return (
            candles(spec, interval, start, end),
            now(),
            now(),
            default_context(spec).model_dump(mode="json"),
        )

    monkeypatch.setattr(market.PROVIDERS["oanda_v20"], "collect", collect)
    return calls


def load(client, database, start, end, interval="1h"):
    response = client.post(
        "/api/v1/research/discovery/history",
        json={
            "request_id": str(uuid4()),
            "instrument_id": FX,
            "interval": interval,
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
    )
    assert response.status_code == 201, response.text
    for _ in range(12):
        with Session(database[0]) as db:
            history_store.process_one(db)
            row = db.get(DiscoveryRecord, response.json()["id"])
            if row.payload["status"] in {"completed", "failed"}:
                assert row.payload["status"] == "completed", row.payload
                return row.id
    pytest.fail("History loader did not complete its bounded pages")


def settings(client, **values):
    p = {**DiscoveryParameters().model_dump(mode="json"), **values}
    state = client.get("/api/v1/research/discovery").json()
    response = client.put(
        "/api/v1/research/discovery/settings",
        json={"request_id": str(uuid4()), "expected_revision": state["revision"], "parameters": p},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_persistent_history_reuse_incremental_and_offline_reads(
    signed_in, database, provider, monkeypatch
):
    start, end = datetime(2025, 1, 6, tzinfo=UTC), datetime(2025, 3, 28, tzinfo=UTC)
    load(signed_in, database, start, end)
    initial = len(provider)
    assert initial >= 1
    load(signed_in, database, start, end)
    assert len(provider) == initial
    later = end + timedelta(days=4)
    load(signed_in, database, start, later)
    assert len(provider) > initial
    assert all(left >= provider[initial - 1][2] for _, left, _ in provider[initial:])
    monkeypatch.setattr(
        market.PROVIDERS["oanda_v20"],
        "collect",
        lambda *a, **k: pytest.fail("Saved chart must not call OANDA"),
    )
    query = {
        "instrument_id": FX,
        "interval": "1h",
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    response = signed_in.get("/api/v1/research/discovery/history", params=query)
    assert response.status_code == 200, response.text
    assert response.json()["network_calls"] == 0 and response.json()["total_bars"] > 1000
    assert len(response.json()["candles"]) <= 751
    assert (
        signed_in.get(
            "/api/v1/research/discovery/history", params={**query, "interval": "1m"}
        ).status_code
        == 422
    )
    body = {
        "request_id": str(uuid4()),
        "name": "Reusable historical slice",
        **query,
        "reason": "Research from saved prices",
    }
    first = signed_in.post("/api/v1/research/lab/datasets/history", json=body)
    assert first.status_code == 201, first.text
    with Session(database[0]) as db:
        from tauric.research_models import ResearchRecord

        assert db.get(ResearchRecord, first.json()["id"]).payload["provenance"]["saved_history"]
    body["request_id"] = str(uuid4())
    assert signed_in.post("/api/v1/research/lab/datasets/history", json=body).status_code == 201


def test_partial_higher_timeframe_tail_is_not_falsely_cached(signed_in, database, provider):
    start = datetime(2025, 3, 10, 1, tzinfo=UTC)
    partial = datetime(2025, 3, 12, 22, tzinfo=UTC)
    load(signed_in, database, start, partial, "4h")
    previous_calls = len(provider)
    load(signed_in, database, start, partial + timedelta(hours=4), "4h")
    assert len(provider) == previous_calls + 1
    with Session(database[0]) as db:
        owner = db.scalars(select(Owner)).one()
        spec = market.instrument(db, FX)
        left, right = history_store.bounds(spec, "4h", start, partial + timedelta(hours=4))
        saved = history_store.read(db, owner, FX, "4h", left, right)
        expected = candles(spec, "4h", left, right)
        assert [datetime.fromisoformat(b["open_time"]) for b in saved["candles"]] == [
            datetime.fromisoformat(b["open_time"]) for b in expected
        ]


def test_research_evidence_and_owner_boundary(signed_in, database, provider):
    load(signed_in, database, datetime(2025, 1, 6, tzinfo=UTC), datetime(2025, 1, 10, tzinfo=UTC))
    settings(signed_in)
    record = signed_in.get("/api/v1/research/discovery").json()["records"][0]
    with Session(database[1]) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE research_history_chunks SET bars=0"))
            db.commit()
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(
                text(
                    "UPDATE research_discovery_records SET payload=jsonb_set(payload::jsonb,'{revision}','99')::json WHERE kind='settings'"
                )
            )
            db.commit()
    signed_in.delete("/api/v1/session")
    assert signed_in.get("/api/v1/research/discovery/records/" + record["id"]).status_code == 401


def test_causal_higher_timeframe_and_mixture_exposure(database):
    with Session(database[0]) as db:
        spec = market.instrument(db, FX)
    start, end = datetime(2025, 1, 6, tzinfo=UTC), datetime(2025, 2, 28, tzinfo=UTC)
    bars, higher = candles(spec, "1h", start, end), candles(spec, "4h", start, end)
    definition = {
        "mode": "blend",
        "context_interval": "4h",
        "components": [
            {"family": "trend", "weight": "0.4"},
            {"family": "mean_reversion", "weight": "0.6"},
        ],
    }
    original = discovery_signals.plan(bars, definition, higher)
    assert all(0 <= p["exposure"] <= 1 for p in original.values())
    at = len(bars) // 2
    changed = copy.deepcopy(higher)
    for b in changed:
        if datetime.fromisoformat(b["close_time"]) > datetime.fromisoformat(bars[at]["close_time"]):
            b["close"] = "9"
    future = discovery_signals.plan(bars, definition, changed)
    assert [original[i] for i in range(at + 1)] == [future[i] for i in range(at + 1)]
    definition["mode"] = "regime_switch"
    switching = discovery_signals.plan(bars, definition, higher)
    assert all(p["exposure"] in (0, 1) for p in switching.values())
    with pytest.raises(ValueError):
        ResearchStrategy.model_validate(
            {
                "mode": "blend",
                "components": [
                    {"family": "trend", "weight": "1"},
                    {"family": "breakout", "weight": "1"},
                ],
            }
        )


def test_discovery_followup_restart_caps_and_recorded_rejection(signed_in, database, provider):
    from tauric import agent_tasks
    from tauric.agent_contracts import JobReference

    end = now().replace(minute=0, second=0, microsecond=0)
    for interval in ("1h", "4h"):
        load(signed_in, database, end - timedelta(days=180), end, interval)
    settings(signed_in, daily_hypothesis_limit=2, daily_experiment_limit=6)
    request = {"request_id": str(uuid4())}
    response = signed_in.post("/api/v1/research/discovery/cycles", json=request)
    assert response.status_code == 201, response.text
    assert (
        signed_in.post("/api/v1/research/discovery/cycles", json=request).json()["id"]
        == response.json()["id"]
    )
    calls = len(provider)
    ref = JobReference(kind="discovery", id=response.json()["id"])
    with Session(database[0]) as db:
        queued = agent_tasks.reference(db, db.scalars(select(Owner)).one(), ref)
        assert queued["status"] == "queued" and queued["proofs"] == []
    for _ in range(30):
        # Each iteration is a fresh process-like session: no in-memory scheduler state is required.
        with Session(database[0]) as db:
            discovery.process_one(db)
            claimed = quant_worker.claim(db, "lean")
            if claimed:
                job_id, token, _ = claimed
                quant_worker.finish(
                    db,
                    job_id,
                    token,
                    result={
                        "engine": "lean",
                        "authority": "research_only",
                        "return_percent": -1,
                        "max_drawdown_percent": 2,
                        "fills": [],
                        "equity_curve": [],
                        "currency": "USD",
                    },
                )
            row = db.get(DiscoveryRecord, response.json()["id"])
            if row.payload["status"] == "completed":
                break
    assert row.payload["status"] == "completed", row.payload
    assert row.payload["summary"]["rejected"] == 2
    assert len(provider) == calls
    state = signed_in.get("/api/v1/research/discovery").json()
    trials = [r for r in state["records"] if r["kind"] == "trial"]
    assert len(trials) == 2 and all(
        t["payload"]["analysis"]["requires_fresh_final_holdout"] for t in trials
    )
    assert (
        signed_in.post(
            "/api/v1/research/discovery/cycles", json={"request_id": str(uuid4())}
        ).status_code
        == 429
    )
    with Session(database[0]) as db:
        completed = agent_tasks.reference(db, db.scalars(select(Owner)).one(), ref)
        assert completed["status"] == "succeeded"
        assert completed["proofs"] == ["job_success", "research_report"]
        assert db.scalar(text("SELECT count(*) FROM broker_order_intents")) == 0
        assert db.scalar(text("SELECT count(*) FROM broker_policy_versions")) == 0


def test_pause_cancel_and_current_ceiling(signed_in, database, provider):
    settings(signed_in, daily_experiment_limit=1)
    response = signed_in.post(
        "/api/v1/research/discovery/cycles", json={"request_id": str(uuid4())}
    )
    identity = response.json()["id"]
    assert signed_in.post(f"/api/v1/research/discovery/records/{identity}/pause").status_code == 200
    with Session(database[0]) as db:
        assert not discovery.process_one(db)
        assert db.scalar(select(QuantRecord.id)) is None
    assert (
        signed_in.post(f"/api/v1/research/discovery/records/{identity}/resume").status_code == 200
    )
    assert (
        signed_in.post(f"/api/v1/research/discovery/records/{identity}/cancel").status_code == 200
    )
    state = signed_in.get("/api/v1/research/discovery").json()
    assert all(
        r["payload"]["status"] == "cancelled"
        for r in state["records"]
        if r["kind"] in {"campaign", "trial"}
    )


def test_ai_budget_prevents_unpriced_or_unfunded_calls(signed_in, database, monkeypatch):
    p = DiscoveryParameters().model_dump(mode="json")
    p["ai_enabled"] = True
    response = signed_in.put(
        "/api/v1/research/discovery/settings",
        json={"request_id": str(uuid4()), "expected_revision": 0, "parameters": p},
    )
    assert response.status_code == 422
    profile = SimpleNamespace(
        configured=lambda: True,
        provider="openai_compatible",
        quick_model="test",
        fingerprint=lambda: "test",
    )
    monkeypatch.setattr(discovery_ai, "load_profiles", lambda db: ({"cloud": profile}, None))
    settings(
        signed_in,
        ai_enabled=True,
        daily_ai_budget_usd="0.001",
        ai_input_usd_per_million="100",
        ai_output_usd_per_million="100",
    )
    response = signed_in.post(
        "/api/v1/research/discovery/cycles", json={"request_id": str(uuid4())}
    )
    monkeypatch.setattr(
        discovery_ai.httpx,
        "Client",
        lambda **k: pytest.fail("No AI request may exceed the saved reservation ceiling"),
    )
    with Session(database[0]) as db:
        assert not discovery.process_one(db)
        row = db.get(DiscoveryRecord, response.json()["id"])
        assert "budget" in row.payload["error"].lower()
        assert (
            db.scalar(select(DiscoveryRecord.id).where(DiscoveryRecord.kind == "ai_usage")) is None
        )


def test_ai_ideas_are_frozen_and_reservation_is_reconciled_once(signed_in, database, monkeypatch):
    import json
    from decimal import Decimal

    from pydantic import SecretStr

    calls = []
    profile = SimpleNamespace(
        configured=lambda: True,
        provider="openai_compatible",
        quick_model="test-model",
        fingerprint=lambda: "test-fingerprint",
        base_url="https://example.invalid/v1",
        api_key=SecretStr(""),
    )
    monkeypatch.setattr(discovery_ai, "load_profiles", lambda db: ({"cloud": profile}, None))
    settings(
        signed_in,
        ai_enabled=True,
        daily_ai_budget_usd="1",
        ai_input_usd_per_million="1",
        ai_output_usd_per_million="1",
    )
    payload = {
        "usage": {"prompt_tokens": 1000, "completion_tokens": 100},
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "ideas": [
                                {
                                    "instrument_id": FX,
                                    "interval": "1h",
                                    "hypothesis": "A supported trend hypothesis with explicit failure conditions",
                                    "definition": {
                                        "mode": "single",
                                        "components": [{"family": "trend", "weight": "1"}],
                                    },
                                }
                            ]
                        }
                    )
                }
            }
        ],
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield json.dumps(payload).encode()

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url, **kwargs):
            calls.append(kwargs["json"])
            return Response()

    monkeypatch.setattr(discovery_ai.httpx, "Client", Client)
    response = signed_in.post(
        "/api/v1/research/discovery/cycles", json={"request_id": str(uuid4())}
    )
    with Session(database[0]) as db:
        discovery.process_one(db)
    with Session(database[0]) as db:
        owner = db.scalars(select(Owner)).one()
        campaign = db.get(DiscoveryRecord, response.json()["id"])
        assert campaign.payload["ai_ideas"]["ideas"]
        trials = list(db.scalars(select(DiscoveryRecord).where(DiscoveryRecord.kind == "trial")))
        assert len(trials) == 1 and trials[0].payload["input"]["origin"].startswith("ai:")
        assert discovery_ai.charged(db, owner) == Decimal("0.0011")
        original = discovery_ai.generate(
            db,
            owner,
            campaign,
            DiscoveryParameters.model_validate(campaign.payload["input"]["parameters"]),
        )
        assert original["ideas"] and len(calls) == 1


def test_walk_forward_and_final_confirmation_do_not_reuse_seen_windows(
    signed_in, database, provider
):
    end = now().replace(minute=0, second=0, microsecond=0)
    load(signed_in, database, end - timedelta(days=180), end)
    settings(
        signed_in,
        intervals=["1h"],
        families=["trend"],
        qlib=False,
        multi_timeframe=False,
        mixtures=False,
        daily_hypothesis_limit=2,
        daily_experiment_limit=40,
        minimum_closed_trades=1,
    )
    campaigns = []
    for cycle_number in range(2):
        if cycle_number == 1:
            settings(
                signed_in,
                intervals=["1h"],
                families=["trend"],
                qlib=False,
                multi_timeframe=False,
                mixtures=False,
                daily_hypothesis_limit=4,
                daily_experiment_limit=40,
                minimum_closed_trades=1,
            )
        response = signed_in.post(
            "/api/v1/research/discovery/cycles", json={"request_id": str(uuid4())}
        )
        assert response.status_code == 201, response.text
        campaigns.append(response.json()["id"])
        for _ in range(80):
            with Session(database[0]) as db:
                discovery.process_one(db)
                claimed = quant_worker.claim(db, "lean")
                if claimed:
                    key, token, job = claimed
                    valid = [
                        b["close_time"]
                        for b in job["candles"]
                        if datetime.fromisoformat(job["request"]["validation_end"])
                        < datetime.fromisoformat(b["close_time"])
                        <= datetime.fromisoformat(job["request"]["test_end"])
                    ]
                    quant_worker.finish(
                        db,
                        key,
                        token,
                        result={
                            "engine": "lean",
                            "authority": "research_only",
                            "return_percent": 2,
                            "max_drawdown_percent": 1,
                            "currency": "USD",
                            "fills": [
                                {"time": valid[0], "quantity": 10},
                                {"time": valid[-1], "quantity": -10},
                            ],
                            "equity_curve": [
                                {"time": valid[0], "equity": 10000},
                                {"time": valid[-1], "equity": 10200},
                            ],
                        },
                    )
                campaign = db.get(DiscoveryRecord, campaigns[-1])
                if campaign.payload["status"] == "completed":
                    break
        assert campaign.payload["status"] == "completed", campaign.payload
    with Session(database[0]) as db:
        first = db.get(DiscoveryRecord, campaigns[0])
        second = db.get(DiscoveryRecord, campaigns[1])
        confirmed = db.get(DiscoveryRecord, first.payload["confirmation_id"])
        reused = db.get(DiscoveryRecord, second.payload["confirmation_id"])
        assert confirmed.payload["status"] == "screen_passed"
        assert confirmed.payload["analysis"]["requires_fresh_final_holdout"] is False
        assert reused.payload["status"] == "rejected" and not reused.payload["jobs"]
        assert "uninspected" in reused.payload["error"]
        baseline = discovery.resolve_job(
            db, db.scalars(select(Owner)).one(), confirmed.payload["jobs"]["baseline"]
        )
        assert datetime.fromisoformat(
            baseline.payload["request"]["evaluation_start"]
        ) == datetime.fromisoformat(confirmed.payload["input"]["holdout_start"])


def test_uncertain_ai_request_retains_reservation_without_retry(signed_in, database, monkeypatch):
    from fastapi import HTTPException
    from pydantic import SecretStr

    profile = SimpleNamespace(
        configured=lambda: True,
        provider="openai_compatible",
        quick_model="test-model",
        fingerprint=lambda: "test-fingerprint",
        base_url="https://example.invalid/v1",
        api_key=SecretStr(""),
    )
    monkeypatch.setattr(discovery_ai, "load_profiles", lambda db: ({"cloud": profile}, None))
    settings(
        signed_in,
        ai_enabled=True,
        daily_ai_budget_usd="1",
        ai_input_usd_per_million="1",
        ai_output_usd_per_million="1",
    )
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, *args, **kwargs):
            calls.append(1)
            raise discovery_ai.httpx.ReadTimeout("Uncertain fixture response")

    monkeypatch.setattr(discovery_ai.httpx, "Client", Client)
    started = signed_in.post(
        "/api/v1/research/discovery/cycles", json={"request_id": str(uuid4())}
    ).json()
    with Session(database[0]) as db:
        discovery.process_one(db)
    # A fresh coordinator cannot turn an uncertain billed request into another call.
    with Session(database[0]) as db:
        owner = db.scalars(select(Owner)).one()
        campaign = db.get(DiscoveryRecord, started["id"])
        reservation = discovery_ai.charged(db, owner)
        assert reservation > 0
        with pytest.raises(HTTPException, match="409"):
            discovery_ai.generate(
                db, owner, campaign, discovery.parameters(discovery.head(db, owner))
            )
        assert discovery_ai.charged(db, owner) == reservation
        assert db.scalar(select(DiscoveryRecord.id).where(DiscoveryRecord.kind == "trial")) is None
    assert len(calls) == 1


def test_automatic_rotation_explores_valid_distinct_parameters():
    p = DiscoveryParameters()
    definitions = []
    for number in range(32):
        interval, definition = discovery.template(p, number)
        assert interval in p.intervals
        if definition:
            validated = ResearchStrategy.model_validate(definition)
            if validated.context_interval:
                assert (
                    market.INTERVAL_SECONDS[validated.context_interval]
                    > market.INTERVAL_SECONDS[interval]
                )
            definitions.append(validated)
    assert {d.mode for d in definitions} == {"single", "blend", "regime_switch"}
    assert len({c.slow_window for d in definitions for c in d.components}) >= 3
    assert {c.family for d in definitions for c in d.components} == set(p.families)
