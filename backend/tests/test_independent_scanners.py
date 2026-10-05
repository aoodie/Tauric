"""Strategy-free discovery, independent histories and bounded research admission."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_scanner import BTC, ETH, candidate, escalate, scan
from test_scanner import ready_model as ready_model
from test_scanner import scanner_feed as scanner_feed
from test_strategies import registered

from tauric import scanner
from tauric.models import AnalysisJob, ScannerProfileVersion, StrategyVersion
from tauric.scanner_contracts import ScannerSave


def standalone(client, name="Breakout watch", **changes):
    request = {
        "request_id": str(uuid4()),
        "name": name,
        "expected_revision": 0,
        "parameters": {
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
        },
        "reason": "Create standalone discovery scanner",
    }
    result = client.put("/api/v1/scanners/profiles", json=request)
    assert result.status_code == 200, result.text
    return result.json(), request


def revise(client, profile, request, **changes):
    result = client.put(
        "/api/v1/scanners/profiles",
        json={
            **request,
            "profile_id": profile["profile_id"],
            "request_id": str(uuid4()),
            "expected_revision": profile["revision"],
            **changes,
        },
    )
    assert result.status_code == 200, result.text
    return result.json()


def test_strategy_free_discovery_identity_and_resources(signed_in, scanner_feed, database):
    profile, request = standalone(signed_in)
    assert profile["strategy_version_id"] is None
    assert profile["strategy_name"] is None and profile["strategy_label"] is None
    assert profile["name"] == "Breakout watch"
    assert signed_in.put("/api/v1/scanners/profiles", json=request).json()["id"] == profile["id"]
    conflict = signed_in.put("/api/v1/scanners/profiles", json={**request, "name": "Changed"})
    assert conflict.status_code == 409
    found = candidate(signed_in, profile)
    assert found["status"] == "candidate" and found["execution_authority"] is False
    assert found["matches"][0]["profile_name"] == profile["name"]
    assert found["matches"][0]["strategy_version_id"] is None
    resource = signed_in.get(f"/api/v1/resources/scanner_profile/{profile['id']}")
    assert (
        resource.status_code == 200 and resource.json()["title"] == "Breakout watch scanner profile"
    )
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(StrategyVersion)) == 0
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


def test_standalone_name_required_and_strategy_link_validated(signed_in):
    profile, request = standalone(signed_in)
    for name in (None, "", "   "):
        response = signed_in.put(
            "/api/v1/scanners/profiles", json={**request, "request_id": str(uuid4()), "name": name}
        )
        assert response.status_code == 422
    response = signed_in.put(
        "/api/v1/scanners/profiles",
        json={**request, "request_id": str(uuid4()), "strategy_version_id": str(uuid4())},
    )
    assert response.status_code == 404
    assert (
        signed_in.get(f"/api/v1/scanners/profiles/{profile['profile_id']}/history").status_code
        == 200
    )


def test_named_scanners_can_share_an_optional_strategy_link(signed_in):
    profile, req = standalone(signed_in)
    version, _ = registered(signed_in)
    linked = revise(signed_in, profile, req, strategy_version_id=version["id"])
    response = signed_in.put(
        "/api/v1/scanners/profiles",
        json={
            **req,
            "request_id": str(uuid4()),
            "name": "Other linked watch",
            "strategy_version_id": version["id"],
        },
    )
    assert response.status_code == 200, response.text
    other = response.json()
    assert other["profile_id"] != linked["profile_id"]
    assert other["strategy_version_id"] == linked["strategy_version_id"]
    assert len(signed_in.get("/api/v1/scanners").json()["profiles"]) == 2


def test_independent_profiles_share_evidence_and_keep_separate_history(signed_in, scanner_feed):
    first, req = standalone(signed_in, "First watch")
    second, _ = standalone(signed_in, "Second watch")
    a = candidate(signed_in, first)
    b = candidate(signed_in, second)
    assert a["id"] == b["id"] and len(b["matches"]) == 2
    disabled = revise(
        signed_in,
        first,
        req,
        name="Renamed watch",
        parameters={**req["parameters"], "enabled": False},
    )
    assert disabled["profile_id"] == first["profile_id"] and disabled["revision"] == 2
    state = signed_in.get("/api/v1/scanners").json()
    assert {p["name"] for p in state["profiles"]} == {"Renamed watch", "Second watch"}
    matches = state["candidates"][0]["matches"]
    assert [m["eligible"] for m in matches] == [False, True]
    assert scan(signed_in, second)[0]["results"][0]["candidate_id"] == a["id"]
    history = signed_in.get(f"/api/v1/scanners/profiles/{first['profile_id']}/history").json()
    assert [p["name"] for p in history] == ["Renamed watch", "First watch"]
    assert (
        len(signed_in.get(f"/api/v1/scanners/profiles/{second['profile_id']}/history").json()) == 1
    )
    assert (
        signed_in.put(
            "/api/v1/scanners/profiles",
            json={
                **req,
                "request_id": str(uuid4()),
                "profile_id": first["profile_id"],
                "expected_revision": 1,
            },
        ).status_code
        == 409
    )


def test_standalone_research_allowance_survives_link_and_unlink(
    signed_in, scanner_feed, ready_model, database
):
    profile, req = standalone(signed_in, universe=[BTC, ETH])
    run, _ = scan(signed_in, profile)
    cs = [
        signed_in.get(f"/api/v1/scanners/candidates/{r['candidate_id']}").json()
        for r in run["results"]
    ]
    result, _ = escalate(signed_in, cs[0], profile)
    assert result.status_code == 200, result.text
    job = result.json()
    assert job["request"]["scanner_context"]["strategy_version_id"] is None
    assert job["request"]["scanner_context"]["profile_id"] == profile["profile_id"]
    with Session(database[0]) as db:
        scanner.validate_admission(db, db.get(AnalysisJob, job["id"]))
    reused, _ = escalate(signed_in, cs[0], profile)
    assert reused.json()["id"] == job["id"]
    assert escalate(signed_in, cs[1], profile)[0].status_code == 429
    version, _ = registered(signed_in)
    linked = revise(signed_in, profile, req, strategy_version_id=version["id"])
    assert linked["charged_analyses_today"] == 1 and linked["strategy_version_id"] == version["id"]
    with Session(database[0]) as db, pytest.raises(HTTPException, match="expired|changed"):
        scanner.validate_admission(db, db.get(AnalysisJob, job["id"]))
    unlinked = revise(signed_in, linked, req, strategy_version_id=None)
    assert unlinked["charged_analyses_today"] == 1 and unlinked["strategy_version_id"] is None
    new_run, _ = scan(signed_in, unlinked)
    eth = signed_in.get(
        f"/api/v1/scanners/candidates/{new_run['results'][1]['candidate_id']}"
    ).json()
    assert escalate(signed_in, eth, unlinked)[0].status_code == 429
    # Another profile has its own allowance; edits and link changes cannot refill this one.
    other, _ = standalone(signed_in, "Independent allowance", universe=[ETH])
    assert escalate(signed_in, candidate(signed_in, other), other)[0].status_code == 200


def test_standalone_profile_ownership_and_sql_guards(signed_in, database):
    profile, req = standalone(signed_in)
    alien = SimpleNamespace(id=str(uuid4()))
    # The immutable reference guard rejects taking over another owner's identity.
    row = {
        "id": str(uuid4()),
        "owner_id": alien.id,
        "profile_id": profile["profile_id"],
        "strategy_version_id": None,
        "revision": 2,
        "request_id": str(uuid4()),
        "payload": {},
        "checksum": "a" * 64,
    }
    with (
        database[0].begin() as db,
        pytest.raises(DBAPIError, match="Scanner profile owner must match"),
    ):
        db.execute(ScannerProfileVersion.__table__.insert().values(**row))
    with database[0].begin() as db, pytest.raises(DBAPIError):
        db.execute(text("UPDATE scanner_profile_versions SET profile_id=:id"), {"id": str(uuid4())})
    with Session(database[0]) as db:
        original = db.get(ScannerProfileVersion, profile["id"])
        with pytest.raises(HTTPException):
            scanner.owned(db, ScannerProfileVersion, alien, original.id)
        with pytest.raises(HTTPException) as error:
            scanner.save(
                db,
                alien,
                ScannerSave(
                    **{
                        **req,
                        "profile_id": profile["profile_id"],
                        "request_id": str(uuid4()),
                        "expected_revision": 1,
                    }
                ),
            )
        assert error.value.status_code == 404
