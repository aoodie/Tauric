"""The market view uses current downstream eligibility, never mover ranking."""

from uuid import uuid4

from test_independent_scanners import revise, standalone
from test_scanner import BTC, scan
from test_scanner import ready_model as ready_model
from test_scanner import scanner_feed as scanner_feed


def candidates(client):
    response = client.get("/api/v1/intelligence?market=crypto")
    assert response.status_code == 200, response.text
    return response.json()["candidates"]


def test_shortlist_rechecks_ignore_and_changed_profile(signed_in, scanner_feed):
    profile, request = standalone(signed_in)
    scan(signed_in, profile)
    selected = candidates(signed_in)
    assert len(selected) == 1 and selected[0]["instrument_id"] == BTC
    assert selected[0]["execution_authority"] is False
    assert (
        signed_in.post(
            "/api/v1/scanners/candidates/" + selected[0]["id"] + "/actions",
            json={
                "request_id": str(uuid4()),
                "action": "ignore",
                "reason": "Exclude this research setup",
            },
        ).status_code
        == 200
    )
    assert candidates(signed_in) == []
    revise(signed_in, profile, request, parameters={**profile["parameters"], "enabled": False})
    assert candidates(signed_in) == []


def test_low_ranked_evidence_is_not_a_research_candidate(signed_in, scanner_feed):
    scanner_feed["rows"][-2][2:6] = ["111", "99", "110", "20"]
    profile, _ = standalone(signed_in, ranking={"minimum_score": 1.0})
    result, _ = scan(signed_in, profile)
    assert result["potential_candidates"] and not result["published_candidates"]
    assert candidates(signed_in) == []


def test_processing_candidate_is_suppressed(signed_in, scanner_feed, ready_model):
    profile, _ = standalone(signed_in)
    scan(signed_in, profile)
    candidate = candidates(signed_in)[0]
    response = signed_in.post(
        "/api/v1/scanners/candidates/" + candidate["id"] + "/analysis",
        json={
            "request_id": str(uuid4()),
            "profile_version_id": profile["id"],
            "reason": "Fixture research admission",
        },
    )
    assert response.status_code == 200, response.text
    assert candidates(signed_in) == []
