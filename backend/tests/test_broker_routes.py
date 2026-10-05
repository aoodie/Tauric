from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from test_broker_review import complete
from test_broker_review import configured as configured
from test_broker_review import lifecycle as lifecycle
from test_broker_review import ready as ready
from test_broker_review import setup as setup
from test_broker_setup import finish as native_finish

from tauric import analysis, broker_positions, broker_review, broker_routes
from tauric.broker_models import BrokerOrderIntent, BrokerRoute, BrokerRouteWorkflow
from tauric.models import AnalysisJob, Owner


@pytest.fixture
def route(signed_in, ready, database, monkeypatch):
    monkeypatch.setattr(broker_routes, "now", lambda: ready[2])
    monkeypatch.setattr(broker_positions, "now", lambda: ready[2])
    import test_broker_positions

    monkeypatch.setattr(test_broker_positions, "now", lambda: ready[2])
    body = ready[0]["request"]["broker_context"]["assessment_input"]["request"]
    request = dict(
        request_id=str(uuid4()),
        expected_revision=0,
        enabled=True,
        provider="alpaca",
        instrument_id=body["instrument_id"],
        strategy_version_id=body["strategy_version_id"],
        risk_percent="1",
        native_profile="cloud",
        review_profile="cloud",
        cadence_seconds=30,
        reason="Enable the offline calculated-risk demo route",
    )
    route_id = str(uuid4())
    result = signed_in.put(f"/api/v1/brokers/routes/{route_id}", json=request)
    assert result.status_code == 200, result.text
    return route_id, request


def discover(database):
    with Session(database[0]) as db:
        assert broker_routes.discover_one(db)
        return db.scalar(select(BrokerRouteWorkflow))


def due(database, *, flow=True, route=False):
    with Session(database[0]) as db:
        if flow:
            row = db.scalar(select(BrokerRouteWorkflow))
            row.next_due_at = broker_routes.now()
        if route:
            row = db.scalar(select(BrokerRoute))
            row.next_due_at = broker_routes.now()
        db.commit()


def native(database, flow):
    with Session(database[0]) as db:
        job = analysis.view(db, db.get(AnalysisJob, flow.assessment_job_id)).model_dump(mode="json")
    native_finish(database, job)
    return job


def advance(database):
    due(database)
    with Session(database[0]) as db:
        assert broker_routes.advance_one(db)


def test_automatic_signal_to_bound_review_to_one_risk_sized_fill(signed_in, route, ready, database):
    flow = discover(database)
    assert flow.payload["sizing"]["quantity"] == "33"
    assert flow.payload["sizing"]["estimated_stop_loss_home"] == "99.33"
    assert ready[1].submissions == []
    job = native(database, flow)
    assert (
        job["request"]["broker_context"]["assessment_input"]["route_context"]["workflow_id"]
        == flow.id
    )
    advance(database)
    review = signed_in.get("/api/v1/brokers/reviews").json()[0]
    assert review["submit_on_allow"]
    complete(signed_in, database, ready, review)
    with Session(database[0]) as db:
        assert broker_review.deliver_one(db)
    intent = signed_in.get("/api/v1/brokers").json()["orders"][0]
    assert signed_in.post(f"/api/v1/brokers/orders/{intent['id']}/position").status_code == 200
    advance(database)
    state = signed_in.get("/api/v1/brokers/routes").json()
    assert state["workflows"][0]["status"] == "tracking"
    intent_id = state["workflows"][0]["intent_id"]
    position = signed_in.post(f"/api/v1/brokers/orders/{intent_id}/position").json()
    assert position["entry_quantity"] == "33" and position["broker_holding_quantity"] == "40"
    due(database, flow=False, route=True)
    discover(database)
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(BrokerRouteWorkflow)) == 1
        assert db.scalar(select(func.count()).select_from(BrokerOrderIntent)) == 1
    assert len(ready[1].submissions) == 1
    response = signed_in.post(
        f"/api/v1/brokers/orders/{intent_id}/close",
        json={
            "request_id": str(uuid4()),
            "quantity": "33",
            "reason": "Close this offline automatically sized demo entry",
        },
    )
    assert response.status_code == 200, response.text
    advance(database)
    assert signed_in.get("/api/v1/brokers/routes").json()["workflows"][0]["status"] == "closed"
    assert ready[1].positions()[0].quantity == "7"


@pytest.mark.parametrize("stage", ["native", "review", "delivery"])
def test_pause_stops_pending_models_and_submission(signed_in, route, ready, database, stage):
    flow = discover(database)
    if stage != "native":
        native(database, flow)
        advance(database)
        review = signed_in.get("/api/v1/brokers/reviews").json()[0]
        if stage == "delivery":
            complete(signed_in, database, ready, review)
    route_id, request = route
    paused = {
        **request,
        "request_id": str(uuid4()),
        "expected_revision": 1,
        "enabled": False,
        "reason": "Pause the offline route before a paid model or broker effect",
    }
    assert signed_in.put(f"/api/v1/brokers/routes/{route_id}", json=paused).status_code == 200
    if stage == "native":
        with Session(database[0]) as db:
            with pytest.raises(Exception, match="paused"):
                from tauric import broker_setup

                broker_setup.validate_admission(db, db.get(AnalysisJob, flow.assessment_job_id))
    elif stage == "review":
        with Session(database[0]) as db:
            claimed = broker_review.claim(db)
        response = signed_in.get(
            f"/internal/openclaw/broker-jobs/{review['id']}/context",
            headers={"Authorization": "Bearer " + ready[3]["platform_token"]},
        )
        assert response.status_code == 409
        with Session(database[0]) as db:
            assert broker_review.finish(db, *claimed, error="Route paused before model admission")
        assert signed_in.get("/api/v1/brokers/reviews").json()[0]["status"] == "failed"
    else:
        with Session(database[0]) as db:
            assert broker_review.deliver_one(db)
            assert db.scalar(select(BrokerOrderIntent)).status == "rejected"
    advance(database)
    assert ready[1].submissions == []


def test_route_save_recovery_and_signal_dedupe_across_revision(signed_in, route, ready, database):
    route_id, request = route
    assert signed_in.put(f"/api/v1/brokers/routes/{route_id}", json=request).json()["revision"] == 1
    assert (
        signed_in.put(
            f"/api/v1/brokers/routes/{route_id}", json={**request, "risk_percent": "2"}
        ).status_code
        == 409
    )
    discover(database)
    changed = {**request, "request_id": str(uuid4()), "expected_revision": 1, "risk_percent": "2"}
    assert signed_in.put(f"/api/v1/brokers/routes/{route_id}", json=changed).status_code == 200
    discover(database)
    state = signed_in.get("/api/v1/brokers/routes").json()
    assert (
        len(state["workflows"]) == 1
        and state["routes"][0]["last_event"]["action"] == "already_seen"
    )
    assert ready[1].submissions == []


@pytest.mark.parametrize("outcome", ["reject", "incomplete"])
def test_native_refusal_does_not_enqueue_openclaw(signed_in, route, ready, database, outcome):
    flow = discover(database)
    with Session(database[0]) as db:
        job = analysis.view(db, db.get(AnalysisJob, flow.assessment_job_id)).model_dump(mode="json")
    native_finish(database, job, decision=outcome)
    advance(database)
    assert signed_in.get("/api/v1/brokers/routes").json()["workflows"][0]["status"] == "blocked"
    assert signed_in.get("/api/v1/brokers/reviews").json() == [] and ready[1].submissions == []


def test_route_history_is_immutable_and_links_are_owned(signed_in, route, ready, database):
    flow = discover(database)
    history = signed_in.get(f"/api/v1/brokers/routes/{route[0]}/history")
    assert history.status_code == 200 and history.json()[0]["workflow_id"] == flow.id
    with database[1].begin() as connection:
        with pytest.raises(DBAPIError):
            connection.execute(text("UPDATE broker_route_events SET payload='{}'::json"))
    with Session(database[0]) as db:
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE broker_route_workflows SET status='closed'"))
            db.commit()
    assert ready[1].submissions == []


@pytest.mark.parametrize("change", ["quote", "equity"])
def test_changed_stop_risk_rejects_the_exact_order_without_resizing(
    signed_in, route, ready, database, monkeypatch, change
):
    flow = discover(database)
    native(database, flow)
    advance(database)
    review = signed_in.get("/api/v1/brokers/reviews").json()[0]
    complete(signed_in, database, ready, review)
    broker = ready[1]
    if change == "quote":
        quote = broker.quote
        monkeypatch.setattr(
            broker, "quote", lambda symbol: quote(symbol).model_copy(update={"ask": "12.04"})
        )
    else:
        account = broker.account
        monkeypatch.setattr(
            broker, "account", lambda: account().model_copy(update={"equity": "9000"})
        )
    with Session(database[0]) as db:
        assert broker_review.deliver_one(db)
        intent = db.scalar(select(BrokerOrderIntent))
        assert intent.status == "rejected"
        assert intent.payload["order"]["quantity"] == "33"
    assert broker.submissions == []


def test_channel_exit_uses_a_complete_postfill_bar_and_preserves_external_holdings(
    signed_in, route, ready, database, monkeypatch
):
    from copy import deepcopy

    from tauric import market

    flow = discover(database)
    native(database, flow)
    advance(database)
    review = signed_in.get("/api/v1/brokers/reviews").json()[0]
    complete(signed_in, database, ready, review)
    with Session(database[0]) as db:
        assert broker_review.deliver_one(db)
        entry_id = db.scalar(select(BrokerOrderIntent.id))
    assert signed_in.post(f"/api/v1/brokers/orders/{entry_id}/position").status_code == 200
    advance(database)
    with Session(database[0]) as db:
        entry = db.get(BrokerOrderIntent, entry_id)
        position = broker_positions.latest(db, entry)
    feed = deepcopy(ready[4])
    from test_market_adapters import bar, dt

    from tauric.market_calendars import periods

    stamp = ready[2] + timedelta(minutes=75)
    feed.candles = [
        type(feed.candles[0]).model_validate(bar(op, close))
        for op, close in periods(feed.data_context, "15m", dt("2026-07-06T10:15"), stamp)
        if close <= stamp
    ]
    for candle in feed.candles:
        candle.open, candle.high, candle.low, candle.close = "10", "11", "9", "10"
    latest = feed.candles[-1]
    latest.low, latest.close = "8", "8"
    feed.provider_time, feed.received_at = stamp, stamp
    monkeypatch.setattr(market, "refresh", lambda *args, **kwargs: feed)
    # Calendar/freshness validation uses the production stock evaluator; fixture
    # coverage is explicit and shifted to include the new post-fill observations.
    from tauric import stock_execution

    monkeypatch.setattr(stock_execution, "now", lambda: stamp)
    monkeypatch.setattr(broker_routes, "now", lambda: stamp)
    monkeypatch.setattr(broker_positions, "now", lambda: stamp)
    quote = ready[1].quote
    monkeypatch.setattr(
        ready[1], "quote", lambda symbol: quote(symbol).model_copy(update={"observed_at": stamp})
    )
    # The actual broker snapshot must also be recent before an automatic exit.
    with Session(database[0]) as db:
        entry = db.get(BrokerOrderIntent, entry_id)
        fresh = position.model_copy(update={"observed_at": stamp})
        broker_positions.save_position(
            db,
            db.get(Owner, entry.owner_id),
            entry_id,
            fresh,
        )
    advance(database)
    position = signed_in.get("/api/v1/brokers/journal").json()["positions"][0]
    assert position["remaining_quantity"] == "0"
    assert ready[1].positions()[0].quantity == "7"
    assert len(ready[1].submissions) == 2


def test_competing_signal_checks_queue_one_native_assessment(signed_in, route, ready, database):
    from concurrent.futures import ThreadPoolExecutor

    def check():
        with Session(database[0]) as db:
            return broker_routes.discover_one(db)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: check(), range(3)))
    assert sum(results) == 1
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(BrokerRouteWorkflow)) == 1
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 2
    assert ready[1].submissions == []


def test_oanda_route_sizes_gbp_stop_risk_and_delivers_one_exact_practice_order(
    signed_in, database, configured, monkeypatch, tmp_path
):
    from test_broker_review import fx_ready
    from test_native_broker_setup import Model

    from tauric import native_broker_setup, native_fx
    from tauric.broker_contracts import BrokerOrderView
    from tauric.native_runner import artifact

    ready = fx_ready(signed_in, database, configured, monkeypatch, tmp_path)
    old, broker, stamp, _, _ = ready
    monkeypatch.setattr(broker_routes, "now", lambda: stamp)
    quote = broker.quote
    monkeypatch.setattr(
        broker,
        "quote",
        lambda symbol: quote(symbol).model_copy(update={"quote_to_home_position": "0.75"}),
    )
    request = old["request"]["broker_context"]["assessment_input"]["request"]
    body = dict(
        request_id=str(uuid4()),
        expected_revision=0,
        enabled=True,
        provider="oanda",
        instrument_id=request["instrument_id"],
        strategy_version_id=request["strategy_version_id"],
        risk_percent="1",
        native_profile="cloud",
        review_profile="cloud",
        cadence_seconds=30,
        reason="Offline GBP risk sizing on an automatic practice route",
    )
    response = signed_in.put(f"/api/v1/brokers/routes/{uuid4()}", json=body)
    assert response.status_code == 200, response.text
    flow = discover(database)
    assert flow.payload["sizing"]["quantity"] == "6497"
    assert flow.payload["sizing"]["account_currency"] == "GBP"
    with Session(database[0]) as db:
        active = db.get(AnalysisJob, analysis.claim(db))
        assert active.id == flow.assessment_job_id
        payload = artifact(
            {"final_trade_decision": "Original synthetic FX REVIEW report"},
            "REVIEW",
            active.request,
            active.request["settings"],
            "0.5.2",
        )
        payload["runtime"] = "Tauric Forex daily extension / TradingAgents LLM clients"
        payload["adapter_version"] = native_fx.ADAPTER_VERSION
        payload["forex_evidence"] = native_fx.evidence(
            db, active.request["instrument"], active.request["forex_context"]
        )
        payload["broker_setup_assessment"] = native_broker_setup.assess(
            Model(), active.request["broker_context"], payload["reports"], "REVIEW"
        )
        assert analysis.finish(db, active.id, payload=payload)
    advance(database)
    review = signed_in.get("/api/v1/brokers/reviews").json()[0]
    finished = complete(signed_in, database, ready, review)
    assert finished["decision"]["approved_order"]["quantity"] == "6497"

    def pending(order):
        broker.before_mutation()
        broker.submissions.append(order.client_order_id)
        return BrokerOrderView(
            provider="oanda",
            broker_order_id="offline-auto-fx-42",
            client_order_id=order.client_order_id,
            symbol="EUR_USD",
            side="buy",
            requested_quantity=order.quantity,
            filled_quantity="0",
            average_fill_price=None,
            status="pending",
            broker_status="PENDING",
            observed_at=stamp,
        )

    monkeypatch.setattr(broker, "submit", pending)
    with Session(database[0]) as db:
        assert broker_review.deliver_one(db)
        entry = db.scalar(select(BrokerOrderIntent))
        assert entry.payload["order"]["quantity"] == "6497" and entry.status == "pending"
        assert not broker_review.deliver_one(db)
    assert len(broker.submissions) == 1 and broker.positions()[0].quantity == "3"
