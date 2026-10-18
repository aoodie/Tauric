"""Exact FX bid/ask sizing on offline OANDA facts, never connected account orders."""

import hashlib
import json
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_broker_execution import FixtureBroker
from test_market_adapters import FX, bar, dt, spec
from test_market_analysis import configured  # noqa: F401
from test_native_broker_setup import Model
from test_strategy_replay import register

from tauric import broker_execution, broker_setup, market, native_broker_setup, strategy_rules
from tauric.broker_adapters import DemoBroker
from tauric.broker_contracts import BrokerInstrumentView, BrokerPositionView, BrokerQuote
from tauric.market_calendars import default_context, periods
from tauric.market_contracts import DataQuality, MarketView
from tauric.models import AnalysisJob, MarketSnapshot, Owner

pytestmark = pytest.mark.usefixtures("configured")


class FxSetupBroker(FixtureBroker):
    validate = DemoBroker.validate

    def __init__(self, stamp):
        super().__init__()
        self.provider, self.stamp = "oanda", stamp

    def account(self):
        return super().account().model_copy(update={"currency": "GBP"})

    def instrument(self, name):
        return BrokerInstrumentView(
            symbol=name,
            tradable=True,
            quantity_step="1",
            minimum_quantity="1",
            maximum_quantity="1000000",
            price_step="0.00001",
            margin_rate="0.02",
        )

    def positions(self):
        return [
            BrokerPositionView(
                symbol="EUR_USD",
                side="long",
                quantity="3",
                average_entry_price="1.10",
                unrealized_pnl="1",
            )
        ]

    def quote(self, name):
        return BrokerQuote(
            symbol=name,
            bid="1.11000",
            ask="1.11015",
            observed_at=self.stamp,
            tradable=True,
            quote_to_home_positive="0.75",
            quote_to_home_negative="0.76",
        )


def fx_setup(signed_in, database, monkeypatch, failure=None):
    stamp = dt("2026-07-06T10:00") + timedelta(seconds=5)
    instrument = spec(FX)
    context = default_context(instrument)
    rows = [
        bar(op, close, "1.10", sides=True)
        for op, close in periods(context, "1m", dt("2026-07-06T09:57"), stamp)
        if close <= stamp
    ]
    for row in rows:
        row.update(high="1.101", low="1.09", close="1.10")
        for side, offset in (("bid", "-0.0001"), ("ask", "0.0001")):
            row[side] = {
                key: str(Decimal(row[key]) + Decimal(offset))
                for key in ("open", "high", "low", "close")
            }
    rows[-1].update(high="1.12", close="1.11", volume="50")
    for side, offset in (("bid", "-0.0001"), ("ask", "0.0001")):
        rows[-1][side] = {
            key: str(Decimal(rows[-1][key]) + Decimal(offset))
            for key in ("open", "high", "low", "close")
        }
    data = {
        "candles": rows,
        "source": "oanda_v20",
        "symbol": "EUR_USD",
        "volume_type": "provider_ticks",
        "availability": "observed_at_receipt",
        "data_context": context.model_dump(mode="json"),
        "quality": DataQuality().model_dump(mode="json"),
    }
    checksum = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    snapshot_id = str(uuid4())
    with Session(database[0]) as db:
        db.add(
            MarketSnapshot(
                id=snapshot_id,
                instrument_id=FX,
                interval="1m",
                received_at=stamp,
                provider_time=stamp,
                checksum=checksum,
                data=data,
            )
        )
        db.commit()
    feed = MarketView(
        instrument=instrument,
        interval="1m",
        snapshot_id=snapshot_id,
        checksum=checksum,
        candles=rows,
        state="fresh",
        source="oanda_v20",
        received_at=stamp,
        provider_time=stamp,
        data_context=context,
        quality=DataQuality(),
    )
    monkeypatch.setattr(market, "refresh", lambda *args, **kwargs: feed)
    for module in (broker_setup, native_broker_setup, strategy_rules):
        monkeypatch.setattr(module, "now", lambda: stamp)
    broker = FxSetupBroker(stamp - timedelta(seconds=90) if failure == "old_quote" else stamp)
    monkeypatch.setattr(broker_execution, "adapter", lambda *args, **kwargs: broker)
    assert (
        signed_in.put(
            "/api/v1/market-connections/oanda",
            json={
                "environment": "practice",
                "credential_action": "replace",
                "token": "fixture-token",
                "account_id": "101-001-123-001",
                "expected_revision": 0,
            },
        ).status_code
        == 200
    )
    assert (
        signed_in.put(
            "/api/v1/brokers/oanda/policy",
            json={
                "request_id": str(uuid4()),
                "expected_revision": 0,
                "enabled": True,
                "reason": "Offline demo setup authorization with no owner caps",
            },
        ).status_code
        == 200
    )
    saved = register(signed_in)
    body = {
        "request_id": str(uuid4()),
        "strategy_version_id": saved["id"],
        "instrument_id": FX,
        "provider": "alpaca" if failure == "wrong_provider" else "oanda",
        "quantity": "0.5"
        if failure == "quantity_step"
        else "1000001"
        if failure == "broker_maximum"
        else "2500",
        "profile": "local",
        "reason": "Offline exact FX setup review",
    }
    return body, broker, stamp, feed


@pytest.mark.parametrize(
    "failure", [None, "old_quote", "quantity_step", "broker_maximum", "wrong_provider"]
)
def test_fx_setup_preserves_currency_units_home_currency_and_native_review(
    signed_in, database, monkeypatch, failure
):
    body, broker, stamp, feed = fx_setup(signed_in, database, monkeypatch, failure)
    response = signed_in.post("/api/v1/brokers/setup-preview", json=body)
    if failure == "wrong_provider":
        assert response.status_code == 422
    else:
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview["entry_evidence_ready"] is (failure is None)
        if failure is None:
            evidence = preview["context"]["assessment_input"]
            assert (
                evidence["account"]["currency"] == "GBP"
                and evidence["existing_positions"][0]["quantity"] == "3"
            )
            assert evidence["policy"]["no_owner_limits"]
            planned = preview["planned_order"]
            assert (
                planned["quantity"],
                planned["stop_loss"],
                planned["take_profit"],
                planned["time_in_force"],
            ) == ("2500", "1.0899", "1.15065", "fok")
            result = native_broker_setup.assess(
                Model(),
                preview["context"],
                {"final_trade_decision": "Original synthetic pair research"},
                "REVIEW",
            )
            assert result["decision"] == "approve" and result["quantity"] == "2500"
            assert result["execution_authority"] is False
    assert broker.submissions == [] and broker.cancellations == []
    with Session(database[0]) as db:
        assert db.scalar(select(func.count()).select_from(AnalysisJob)) == 0


@pytest.mark.parametrize(
    "product,symbol,asset", [("METAL", "XAU_USD", "commodity"), ("CFD", "WTICO_USD", "cfd")]
)
@pytest.mark.parametrize("failure", [None, "classification", "missing_bar"])
def test_exact_product_capture_and_verified_assessment(
    signed_in, database, monkeypatch, product, symbol, asset, failure
):
    from tauric import analysis
    from tauric.models import Instrument

    body, broker, stamp, original = fx_setup(signed_in, database, monkeypatch)
    instrument = original.instrument.model_copy(
        update={
            "id": f"oanda:{asset}:{symbol}",
            "symbol": symbol.replace("_", "/"),
            "provider_symbol": symbol,
            "asset_type": asset,
            "base_currency": symbol.split("_")[0],
            "native_asset_type": "none",
            "provider_product_type": product,
            "classification_source": "provider_product",
            "calendar_id": "provider_observed",
        }
    )
    context = default_context(instrument)
    candles = original.candles if failure != "missing_bar" else original.candles[-2:]
    data = {
        "candles": [b.model_dump(mode="json") for b in candles],
        "source": "oanda_v20",
        "symbol": symbol,
        "volume_type": "provider_ticks",
        "availability": "observed_at_receipt",
        "data_context": context.model_dump(mode="json"),
        "quality": DataQuality().model_dump(mode="json"),
    }
    checksum = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    snapshot_id = str(uuid4())
    with Session(database[0]) as db:
        db.add(
            Instrument(
                id=instrument.id,
                definition=instrument.model_dump(mode="json", exclude={"native_research_ready"}),
            )
        )
        db.add(
            MarketSnapshot(
                id=snapshot_id,
                instrument_id=instrument.id,
                interval="1m",
                received_at=stamp,
                provider_time=stamp,
                checksum=checksum,
                data=data,
            )
        )
        analysis.heartbeat(db, "0.5.2")
    feed = original.model_copy(
        update={
            "instrument": instrument,
            "snapshot_id": snapshot_id,
            "checksum": checksum,
            "candles": candles,
            "data_context": context,
        }
    )
    monkeypatch.setattr(market, "refresh", lambda *args, **kwargs: feed)
    monkeypatch.setattr(analysis, "now", lambda: stamp)
    previous = broker.instrument
    broker.instrument = lambda name: previous(name).model_copy(
        update={"product_type": "CURRENCY" if failure == "classification" else product}
    )
    body["instrument_id"] = instrument.id
    response = signed_in.post("/api/v1/brokers/setup-preview", json=body)
    if failure == "classification":
        assert response.status_code == 409, response.text
        assert broker.submissions == [] and broker.cancellations == []
        return
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["entry_evidence_ready"] is (failure is None)
    queued = signed_in.post("/api/v1/brokers/setup-assessments", json=body)
    if failure:
        assert queued.status_code == 422, queued.text
    else:
        assert queued.status_code == 202, queued.text
        with Session(database[0]) as db:
            job = db.get(AnalysisJob, analysis.claim(db))
            assert job.id == queued.json()["id"]
            assessment = native_broker_setup.assess(
                Model(), job.request["broker_context"], {}, "SETUP_REVIEW"
            )
            payload = {
                "runtime": "Tauric exact OANDA product setup / TradingAgents LLM client",
                "version": "0.5.2",
                "upstream_revision": analysis.UPSTREAM_REVISION,
                "request": job.request,
                "reports": {"final_trade_decision": assessment["rationale"]},
                "native_rating": "SETUP_REVIEW",
                "broker_setup_assessment": assessment,
                "execution_authority": False,
                "governance": None,
            }
            assert analysis.finish(db, job.id, payload=payload)
            report = broker_setup.report(
                db,
                db.get(Owner, job.owner_id),
                job.id,
            )
            assert report.assessment.decision == "approve" and report.evidence_current
    assert broker.submissions == [] and broker.cancellations == []
