"""Idempotent starter routes for the owner's authorized OANDA/Alpaca demos.

Run inside the verified API container. This creates configuration only; the
independent workers require fresh closed-bar/native/OpenClaw evidence to trade.
The preset has not been strategy-qualified or demonstrated profitable.
"""

import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select, text
from sqlalchemy.orm import Session
from tauric import broker_routes, model_profiles, strategies
from tauric.broker_contracts import BrokerRouteChange
from tauric.database import engine
from tauric.models import Owner
from tauric.strategy_contracts import StrategyCreate

with Session(engine()) as db:
    assert (
        db.scalar(text("SELECT version_num FROM alembic_version"))
        == "0028_broker_routes"
    )
    owner = db.scalar(select(Owner))
    profiles, _ = model_profiles.load_profiles(db)
    profile = (
        "local" if profiles.get("local") and profiles["local"].configured() else "cloud"
    )
    assert profiles[profile].configured()
    saved = strategies.save(
        db,
        owner,
        StrategyCreate(
            request_id=uuid5(NAMESPACE_URL, "tauric-demo-breakout-v1:" + owner.id),
            name="Demo closed-bar breakout (unqualified)",
            parameters={
                "label": "Demo starter · 15-minute long breakout",
                "description": "A transparent demo starter, not a qualified or proven profitable strategy. Enter only after a complete 15-minute candle exceeds the prior 20 highs and activity exceeds 1.5 times its prior mean. The initial stop is the prior-window low; the target is 2R; the exit channel uses 10 complete bars. Broker routes calculate quantity separately from account equity and stop distance, with native and OpenClaw vetoes. These paper-only capital/risk parameters do not impose broker account caps.",
                "quantity_step": "1",
                "capital_limit": "100000",
                "risk_percent": "1",
                "rules": {
                    "engine": "closed_bar_breakout_v1",
                    "interval": "15m",
                    "entry_lookback": 20,
                    "minimum_volume_ratio": "1.5",
                    "exit_lookback": 10,
                    "take_profit_r": "2",
                    "signal_ttl_seconds": 900,
                },
            },
            reason="Add a clearly labelled demo starter for the owner-requested OANDA/Alpaca autotrading workflow; no profitability qualification is claimed",
        ),
    )
    routes = []
    for provider, instrument in (
        ("oanda", "oanda:fx:EUR_USD"),
        ("alpaca", "alpaca:equity:AAPL"),
    ):
        result = broker_routes.save(
            db,
            owner,
            uuid5(NAMESPACE_URL, "tauric-demo-route-v1:" + owner.id + ":" + provider),
            BrokerRouteChange(
                request_id=uuid5(
                    NAMESPACE_URL,
                    "tauric-demo-route-enable-v1:" + owner.id + ":" + provider,
                ),
                expected_revision=0,
                enabled=True,
                provider=provider,
                instrument_id=instrument,
                strategy_version_id=saved.id,
                risk_percent="1",
                native_profile=profile,
                review_profile=profile,
                cadence_seconds=30,
                reason="Enable the owner-authorized practice/paper autotrading route with automatically calculated stop-risk quantity; adjustable initial risk is 1% and no additional owner exposure/position/daily-loss caps apply",
            ),
        )
        routes.append(result.model_dump(mode="json"))
    print(
        json.dumps(
            {
                "strategy_id": saved.id,
                "strategy_name": saved.name,
                "strategy_qualified": False,
                "configured_routes": routes,
                "direct_model_calls": 0,
                "direct_broker_mutations": 0,
            }
        )
    )
