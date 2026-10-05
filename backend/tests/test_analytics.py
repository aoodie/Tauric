import copy
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_paper import action, buy
from test_paper import setup as paper_setup

from tauric import paper
from tauric.models import MarketSnapshot, now


@pytest.fixture
def setup(signed_in, database):
    return paper_setup.__wrapped__(signed_in, database)


def mark(database, value):
    with Session(database[0]) as db:
        prior = db.scalar(select(MarketSnapshot).order_by(MarketSnapshot.received_at.desc()))
        payload = copy.deepcopy(prior.data)
        payload["candles"][-1].update(
            open=str(value), close=str(value), high=str(value + 1), low=str(value - 1)
        )
        db.add(
            MarketSnapshot(
                instrument_id=prior.instrument_id,
                interval="1m",
                received_at=now(),
                provider_time=now(),
                data=payload,
                checksum=paper.checksum(payload),
            )
        )
        db.commit()


def test_closed_outcomes_and_open_fees_have_separate_meanings(signed_in, setup):
    acct, dep, _, _ = setup
    entry = buy(signed_in, dep)
    response = signed_in.get("/api/v1/analytics")
    assert response.status_code == 200, response.text
    view = response.json()["accounts"][0]
    assert view["open_positions"] == 1 and view["metrics"]["closed_trades"] == 0
    assert view["metrics"]["net_pnl"] == "0" and view["metrics"]["win_rate_percent"] is None
    assert view["realized_balance"] == acct["starting_balance"]
    assert Decimal(view["metrics"]["total_fees"]) == Decimal(entry["payload"]["fee"])
    assert Decimal(view["cash"]) < Decimal(view["realized_balance"])


def test_two_strategies_same_instrument_reconcile_fees_outcomes_and_drawdown(
    signed_in, database, setup
):
    acct, dep, _, dep_request = setup
    other = signed_in.post(
        "/api/v1/paper/deployments",
        json={**dep_request, "request_id": str(uuid4()), "strategy": "Second attributed strategy"},
    ).json()
    first, second = buy(signed_in, dep), buy(signed_in, other)
    mark(database, 105)
    exit1 = signed_in.post(f"/api/v1/paper/positions/{first['id']}/close", json=action()).json()
    middle = signed_in.get("/api/v1/analytics").json()["accounts"][0]
    assert middle["open_positions"] == 1 and middle["metrics"]["wins"] == 1
    assert middle["metrics"]["profit_factor"] is None
    mark(database, 95)
    exit2 = signed_in.post(f"/api/v1/paper/positions/{second['id']}/close", json=action()).json()
    response = signed_in.get("/api/v1/analytics", params={"account_id": acct["id"]})
    assert response.status_code == 200, response.text
    result = response.json()["accounts"][0]
    winning, losing = (
        Decimal(exit1["payload"]["realized_pnl"]),
        Decimal(exit2["payload"]["realized_pnl"]),
    )
    assert winning > 0 > losing
    assert result["open_positions"] == 0 and result["metrics"]["closed_trades"] == 2
    assert Decimal(result["metrics"]["win_rate_percent"]) == 50
    assert Decimal(result["metrics"]["net_pnl"]) == winning + losing
    assert abs(Decimal(result["metrics"]["profit_factor"]) - winning / -losing) < Decimal("1e-25")
    assert Decimal(result["realized_max_drawdown_amount"]) == -losing
    expected_fees = sum(Decimal(row["payload"]["fee"]) for row in (first, second, exit1, exit2))
    assert Decimal(result["metrics"]["total_fees"]) == expected_fees
    groups = {s["deployment_id"]: s for s in result["strategies"]}
    assert (
        groups[dep["id"]]["metrics"]["wins"] == 1 and groups[other["id"]]["metrics"]["losses"] == 1
    )
    assert result["trades"][0]["entry_fill_id"] == first["id"]
    assert result["trades"][1]["entry_fill_id"] == second["id"]
    paper_state = signed_in.get("/api/v1/paper").json()
    assert result["cash"] == paper_state["accounts"][0]["cash"]
    assert result["metrics"]["net_pnl"] == paper_state["accounts"][0]["realized_pnl"]
    assert (
        signed_in.get("/api/v1/analytics", params={"account_id": str(uuid4())}).status_code == 404
    )


def test_no_account_means_empty_analytics(signed_in):
    result = signed_in.get("/api/v1/analytics")
    assert result.status_code == 200
    assert result.json()["accounts"] == []
