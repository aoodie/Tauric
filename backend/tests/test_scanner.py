"""Deterministic evidence, admission budgets, identity and expiry on disposable storage."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_strategies import registered

from tauric import analysis, candidate_research, market, scanner
from tauric.model_profiles import ModelProfile
from tauric.models import AnalysisJob, Owner, ScannerAnalysis, ScannerCandidate, now
from tauric.scanner_contracts import CandidateEscalate

BTC = "binance:spot:BTCUSDT"
ETH = "binance:spot:ETHUSDT"


@pytest.fixture
def scanner_feed(monkeypatch):
    timestamp = int(now().timestamp() * 1000)
    start = timestamp // 900000 * 900000
    rows = [
        [start + n * 900000, "100", "103", "99", "101", "10", start + (n + 1) * 900000 - 1]
        for n in [-4, -3, -2, -1, 0]
    ]
    rows[-2][2:6] = ["111", "99", "110", "50"]
    rows[-1][2:6] = ["1000", "99", "999", "1000"]
    state = {"rows": rows, "time": timestamp, "status": 200, "calls": 0}
    original = httpx.Client

    def handle(req):
        state["calls"] += 1
        return (
            httpx.Response(200, json={"serverTime": state["time"]})
            if req.url.path.endswith("time")
            else httpx.Response(state["status"], json=state["rows"])
        )

    monkeypatch.setattr(
        market.httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(handle), **kw)
    )
    return state


def body(version, **changes):
    p = {
        "enabled": True,
        "universe": [BTC],
        "interval": "15m",
        "detector_id": "closed_bar_breakout_v1",
        "lookback": 3,
        "minimum_volume_ratio": "2",
        "candidate_ttl_seconds": 600,
        "max_analyses_per_day": 1,
        "model_profile": "local",
        **changes,
    }
    return {
        "request_id": str(uuid4()),
        "strategy_version_id": version["id"],
        "expected_revision": 0,
        "parameters": p,
        "reason": "Disposable scanner test configuration",
    }


def configured(client, **changes):
    v, _ = registered(client)
    req = body(v, **changes)
    r = client.put("/api/v1/scanners/profiles", json=req)
    assert r.status_code == 200, r.text
    return r.json(), req


def scan(client, profile):
    req = {"request_id": str(uuid4()), "profile_version_id": profile["id"]}
    r = client.post("/api/v1/scanners/runs", json=req)
    assert r.status_code == 200, r.text
    return r.json(), req


def candidate(client, profile):
    run, _ = scan(client, profile)
    candidate_id = run["results"][0]["candidate_id"]
    return client.get("/api/v1/scanners/candidates/" + candidate_id).json()


@pytest.fixture
def ready_model(monkeypatch, database):
    model = ModelProfile(
        provider="openai_compatible",
        base_url="https://fixture.invalid/v1",
        quick_model="fixture-quick",
        deep_model="fixture-deep",
        api_key="fixture-only-key",
    )
    monkeypatch.setattr(candidate_research, "load_profiles", lambda db: ({"local": model}, None))
    monkeypatch.setattr(analysis, "load_profiles", lambda db: ({"local": model}, None))
    with Session(database[0]) as db:
        analysis.heartbeat(db, "0.5.2")
    return model


def escalate(client, c, profile, **changes):
    req = {
        "request_id": str(uuid4()),
        "profile_version_id": profile["id"],
        "reason": "Request daily research on disposable candidate",
        **changes,
    }
    return client.post(f"/api/v1/scanners/candidates/{c['id']}/analysis", json=req), req


def test_scanner_authentication_and_csrf(client):
    assert client.get("/api/v1/scanners").status_code == 401
    client.post(
        "/api/v1/session", json={"username": "test-owner", "password": "Test-only-password-2026"}
    )
    assert client.put("/api/v1/scanners/profiles", json={}).status_code == 403
    assert client.post("/api/v1/scanners/runs", json={}).status_code == 403


def test_profile_identity_conflicts_history_and_validation(signed_in, scanner_feed):
    p, req = configured(signed_in)
    assert signed_in.put("/api/v1/scanners/profiles", json=req).json()["id"] == p["id"]
    assert (
        signed_in.put(
            "/api/v1/scanners/profiles", json={**req, "reason": "Different inputs"}
        ).status_code
        == 409
    )
    assert (
        signed_in.put(
            "/api/v1/scanners/profiles", json={**req, "request_id": str(uuid4())}
        ).status_code
        == 409
    )
    changed = {
        **req,
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "parameters": {**req["parameters"], "enabled": False},
    }
    r = signed_in.put("/api/v1/scanners/profiles", json=changed)
    assert r.status_code == 200, r.text
    history = signed_in.get(f"/api/v1/scanners/profiles/{p['strategy_version_id']}/history").json()
    assert [v["revision"] for v in history] == [2, 1]
    assert history[1]["parameters"]["enabled"]
    assert (
        signed_in.post(
            "/api/v1/scanners/runs",
            json={"request_id": str(uuid4()), "profile_version_id": p["id"]},
        ).status_code
        == 409
    )
    for key, value in [
        ("minimum_volume_ratio", "NaN"),
        ("minimum_volume_ratio", 2.0),
        ("minimum_volume_ratio", "0"),
        ("lookback", True),
        ("universe", [BTC, BTC]),
        ("detector_id", "untrusted_script"),
        ("interval", "tick"),
    ]:
        assert (
            signed_in.put(
                "/api/v1/scanners/profiles",
                json={
                    **changed,
                    "request_id": str(uuid4()),
                    "expected_revision": 2,
                    "parameters": {**req["parameters"], key: value},
                },
            ).status_code
            == 422
        )
    assert (
        signed_in.put(
            "/api/v1/scanners/profiles",
            json={
                **changed,
                "request_id": str(uuid4()),
                "expected_revision": 2,
                "parameters": {**req["parameters"], "universe": ["not:a:provider"]},
            },
        ).status_code
        == 404
    )


def test_closed_bars_and_evidence_are_reused_without_llm(signed_in, scanner_feed, database):
    p, _ = configured(signed_in)
    run, req = scan(signed_in, p)
    assert signed_in.post("/api/v1/scanners/runs", json=req).json()["id"] == run["id"]
    c = candidate(signed_in, p)
    assert c["features"]["close"] == "110" and c["features"]["volume_ratio"] == "5.00000000"
    assert c["status"] == "candidate" and not c["execution_authority"]
    assert scanner_feed["calls"] == 2
    state = signed_in.get("/api/v1/scanners").json()
    assert len(state["candidates"]) == 1 and len(state["runs"]) == 2
    evidence = signed_in.get(f"/api/v1/resources/scanner_candidate/{c['id']}").json()["data"][
        "original_evidence"
    ]
    assert len(evidence["evidence"]["bars"]) == 4
    assert all(v["closed"] for v in evidence["evidence"]["bars"])
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


def test_matching_strategies_share_context_but_keep_version_links(signed_in, scanner_feed):
    p, _ = configured(signed_in)
    first = candidate(signed_in, p)
    other, _ = registered(signed_in)
    req = body(other)
    second_profile = signed_in.put("/api/v1/scanners/profiles", json=req).json()
    second = candidate(signed_in, second_profile)
    assert first["id"] == second["id"]
    assert len(second["matches"]) == 2
    assert {v["strategy_version_id"] for v in second["matches"]} == {
        p["strategy_version_id"],
        other["id"],
    }
    different = signed_in.put(
        "/api/v1/scanners/profiles",
        json={
            **req,
            "request_id": str(uuid4()),
            "expected_revision": 1,
            "parameters": {**req["parameters"], "lookback": 2},
        },
    ).json()
    assert candidate(signed_in, different)["id"] != first["id"]


@pytest.mark.parametrize(
    "fault",
    ["forming_only", "missing_volume", "gap", "short_window", "outdated", "provider_outage"],
)
def test_unavailable_evidence_cannot_pass(signed_in, scanner_feed, fault):
    p, _ = configured(signed_in)
    if fault == "forming_only":
        scanner_feed["rows"][-2][2:6] = ["103", "99", "101", "10"]
    if fault == "missing_volume":
        for row in scanner_feed["rows"][:-2]:
            row[5] = "0"
    if fault == "gap":
        scanner_feed["rows"].pop(1)
    if fault == "short_window":
        scanner_feed["rows"] = scanner_feed["rows"][-3:]
    if fault == "outdated":
        for row in scanner_feed["rows"]:
            row[0] -= 9000000
            row[6] -= 9000000
    if fault == "provider_outage":
        scanner_feed["status"] = 500
    result, _ = scan(signed_in, p)
    assert result["results"][0]["status"] in ["filtered", "unavailable"]
    assert signed_in.get("/api/v1/scanners").json()["candidates"] == []


def test_watch_ignore_expiry_and_superseded_profiles(signed_in, scanner_feed, monkeypatch):
    p, req = configured(signed_in)
    c = candidate(signed_in, p)
    action = {"request_id": str(uuid4()), "action": "watch", "reason": "Keep candidate on watch"}
    path = f"/api/v1/scanners/candidates/{c['id']}/actions"
    assert signed_in.post(path, json=action).json()["status"] == "watch"
    assert signed_in.post(path, json=action).json()["status"] == "watch"
    assert signed_in.post(path, json={**action, "action": "ignore"}).status_code == 409
    assert (
        signed_in.post(
            path, json={**action, "request_id": str(uuid4()), "action": "ignore"}
        ).json()["status"]
        == "ignored"
    )
    assert escalate(signed_in, c, p)[0].status_code == 409
    signed_in.post(path, json={**action, "request_id": str(uuid4())})
    fixed = now() + timedelta(seconds=601)
    monkeypatch.setattr(scanner, "now", lambda: fixed)
    assert signed_in.get(f"/api/v1/scanners/candidates/{c['id']}").json()["status"] == "expired"
    assert candidate(signed_in, p)["id"] == c["id"]
    assert escalate(signed_in, c, p)[0].status_code == 409


def test_research_reuse_budgets_and_unchanged_native_graph(
    signed_in, scanner_feed, ready_model, database
):
    p, _ = configured(signed_in, universe=[BTC, ETH])
    run, _ = scan(signed_in, p)
    cs = [
        signed_in.get("/api/v1/scanners/candidates/" + v["candidate_id"]).json()
        for v in run["results"]
    ]
    result, req = escalate(signed_in, cs[0], p)
    assert result.status_code == 200, result.text
    job = result.json()
    assert job["request"]["analysts"] == ["market", "social", "news", "fundamentals"]
    assert job["request"]["scanner_context"]["features_consumed_by_native_graph"] is False
    assert escalate(signed_in, cs[0], p, **req)[0].json()["id"] == job["id"]
    reused, _ = escalate(signed_in, cs[0], p)
    assert reused.json()["id"] == job["id"]
    assert escalate(signed_in, cs[1], p)[0].status_code == 429
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(ScannerAnalysis)
                .where(ScannerAnalysis.charged.is_(True))
            )
            == 1
        )
        row = db.get(AnalysisJob, job["id"])
        scanner.validate_admission(db, row)
        row.status = "failed"
        db.commit()
    failed, _ = escalate(signed_in, cs[0], p)
    assert failed.json()["status"] == "failed"


def test_budget_survives_edits_and_queue_admission_invalidates(
    signed_in, scanner_feed, ready_model, database
):
    p, req = configured(signed_in)
    c = candidate(signed_in, p)
    job = escalate(signed_in, c, p)[0].json()
    revised = signed_in.put(
        "/api/v1/scanners/profiles",
        json={
            **req,
            "request_id": str(uuid4()),
            "expected_revision": 1,
            "parameters": {**req["parameters"], "minimum_volume_ratio": "3"},
        },
    ).json()
    assert revised["charged_analyses_today"] == 1
    with Session(database[0]) as db, pytest.raises(HTTPException):
        scanner.validate_admission(db, db.get(AnalysisJob, job["id"]))
    new = candidate(signed_in, revised)
    assert new["id"] != c["id"]
    blocked = escalate(signed_in, new, revised)[0]
    assert blocked.status_code == 409 and "PROCESSING" in blocked.text
    with Session(database[0]) as db:
        db.get(AnalysisJob, job["id"]).status = "failed"
        db.commit()
    reused = escalate(signed_in, new, revised)[0]
    assert reused.status_code == 200 and reused.json()["id"] == job["id"]
    assert signed_in.get("/api/v1/scanners").json()["profiles"][0]["charged_analyses_today"] == 1


def test_profile_zero_allowance_blocks_new_analysis(signed_in, scanner_feed, ready_model):
    p, _ = configured(signed_in, max_analyses_per_day=0)
    assert escalate(signed_in, candidate(signed_in, p), p)[0].status_code == 429


def test_missing_model_or_worker_does_not_reserve_budget(signed_in, scanner_feed, database):
    p, _ = configured(signed_in)
    c = candidate(signed_in, p)
    assert escalate(signed_in, c, p)[0].status_code == 422
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(ScannerAnalysis)) == 0


def test_concurrent_escalation_cannot_duplicate_paid_work(
    signed_in, scanner_feed, ready_model, database
):
    p, _ = configured(signed_in)
    c = candidate(signed_in, p)
    with Session(database[0]) as db:
        owner_id = db.scalar(select(Owner.id))

    def submit(_):
        with Session(database[0]) as db:
            try:
                return scanner.escalate(
                    db,
                    db.get(Owner, owner_id),
                    c["id"],
                    CandidateEscalate(
                        request_id=uuid4(),
                        profile_version_id=p["id"],
                        reason="Concurrent research test",
                    ),
                ).id
            except HTTPException as e:
                assert e.status_code == 429
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(submit, range(2)))
    assert len(set(v for v in ids if v)) == 1
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1


def test_scanner_sql_history_and_owner_access(signed_in, scanner_feed, database):
    p, _ = configured(signed_in)
    c = candidate(signed_in, p)
    assert signed_in.get("/api/v1/scanners/candidates/" + str(uuid4())).status_code == 404
    assert signed_in.get("/api/v1/resources/scanner_profile/" + p["id"]).status_code == 200
    with Session(database[0]) as db:
        alien = type("Alien", (), {"id": str(uuid4())})()
        with pytest.raises(HTTPException):
            scanner.owned(db, ScannerCandidate, alien, c["id"])
    for table in [
        "scanner_profile_versions",
        "scanner_runs",
        "scanner_candidates",
        "scanner_matches",
        "scanner_events",
        "scanner_analyses",
    ]:
        with database[0].begin() as db, pytest.raises(DBAPIError):
            db.execute(text(f"DELETE FROM {table}"))


def test_expired_queued_candidate_fails_before_any_model_process(
    signed_in, scanner_feed, ready_model, database, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from tauric import worker

    p, _ = configured(signed_in)
    c = candidate(signed_in, p)
    job = escalate(signed_in, c, p)[0].json()
    with Session(database[0]) as db:
        assert analysis.claim(db) == job["id"]
    fixed = now() + timedelta(seconds=601)
    monkeypatch.setattr(scanner, "now", lambda: fixed)
    monkeypatch.setattr(
        worker, "runtime_config", lambda: SimpleNamespace(runtime_directory=str(tmp_path))
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("Expired scanner research must never start a model process")

    monkeypatch.setattr(worker.subprocess, "Popen", forbidden)
    worker.process(job["id"], "0.5.2")
    with Session(database[0]) as db:
        row = db.get(AnalysisJob, job["id"])
        assert row.status == "failed" and "No model request" in row.error


def test_equivalent_strategy_matches_reuse_one_job(signed_in, scanner_feed, ready_model, database):
    p, _ = configured(signed_in)
    c = candidate(signed_in, p)
    other, _ = registered(signed_in)
    other_profile = signed_in.put("/api/v1/scanners/profiles", json=body(other)).json()
    same = candidate(signed_in, other_profile)
    first = escalate(signed_in, c, p)[0].json()
    second = escalate(signed_in, same, other_profile)[0].json()
    assert first["id"] == second["id"]
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 1
        assert (
            db.scalar(
                select(func.count())
                .select_from(ScannerAnalysis)
                .where(ScannerAnalysis.charged.is_(True))
            )
            == 1
        )


def test_connection_rotation_invalidates_queued_scanner_job(
    signed_in, scanner_feed, ready_model, database
):
    connection = {
        "provider": "openai_compatible",
        "base_url": ready_model.base_url,
        "quick_model": ready_model.quick_model,
        "deep_model": ready_model.deep_model,
        "key_action": "replace",
        "api_key": "test-only-credential-one",
        "expected_revision": 0,
    }
    assert signed_in.put("/api/v1/model-connections/local", json=connection).status_code == 200
    p, _ = configured(signed_in)
    c = candidate(signed_in, p)
    job = escalate(signed_in, c, p)[0].json()
    assert job["request"]["scanner_context"]["connection_revision"] == 1
    assert (
        signed_in.put(
            "/api/v1/model-connections/local",
            json={**connection, "expected_revision": 1, "api_key": "test-only-credential-two"},
        ).status_code
        == 200
    )
    with Session(database[0]) as db, pytest.raises(HTTPException):
        scanner.validate_admission(db, db.get(AnalysisJob, job["id"]))
