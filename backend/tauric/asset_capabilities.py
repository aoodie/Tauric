"""Declared integration scope is distinct from a verified working route."""

from sqlalchemy import select

from .data_providers import provider_status
from .instrument_catalog import INSTRUMENTS
from .models import Instrument
from .native_scope import NATIVE_ASSET_TYPES


def research_capabilities(db=None):
    providers = {r.id: r.configured for r in provider_status(db)}
    instruments = (
        [row.definition for row in db.scalars(select(Instrument))]
        if db is not None
        else INSTRUMENTS
    )
    rows = [
        ("crypto_spot", "Crypto spot", "Binance", True),
        ("fx", "FX", "OANDA / Interactive Brokers", False),
        ("equity", "Stocks, including small caps", "Alpaca / Interactive Brokers", False),
        ("etf", "ETFs", "Alpaca / Interactive Brokers", False),
        ("crypto_futures", "Crypto futures", "Binance", False),
        ("exchange_futures", "Exchange futures", "Interactive Brokers", False),
    ]
    configured = {"crypto_spot": True, "fx": providers.get("oanda_v20", False),
                  "equity": providers.get("alpaca_stocks", False),
                  "etf": providers.get("alpaca_stocks", False)}
    return [
        {
            "asset_class": key,
            "label": label,
            "planned_broker_routes": broker,
            "data_ready": configured.get(key, False),  # Admission eligibility, not a connected-feed claim.
            "data_configured": configured.get(key, False),
            "data_connection_status": "verified_per_request" if configured.get(key) else "unconfigured",
            "current_feed_freshness": "not_observed_by_capability_catalog",
            "data_implemented": ready or key in ("fx", "equity", "etf"),
            "screening_ready": bool(configured.get(key)) and (ready or key in ("fx", "equity", "etf")),
            "screening_implemented": ready or key in ("fx", "equity", "etf"),
            "replay_ready": ready or key in ("fx", "equity", "etf"),
            "native_research_ready": key in NATIVE_ASSET_TYPES,
            "execution_implemented": key in ("fx", "equity", "etf"),
            "activation_status": "check_per_account_in_trading",
            "instruments": [r["id"] for r in instruments if r["asset_type"] == key],
            "detail": "Daily native crypto context; intraday setup assessment remains unavailable."
            if ready
            else "Read-only data, calendar-aware screening, explicit-cost replay, daily native research, exact-version assessment and governed Alpaca execution code are implemented. Qualification, connected acceptance and account-specific authority are separate checks in Trading. Chart/native sources are separate; ETF company fundamentals are omitted."
            if key in ("equity", "etf")
            else "Read-only data, calendar-aware screening, explicit-cost replay, exact-version research qualification and governed OANDA execution code are implemented. Daily native research retains explicit coverage gaps. Prospective qualification, connected acceptance and account-specific authority are separate checks in Trading."
            if key == "fx"
            else "Data/research adapter is not enabled. Research requests are deferred.",
            "priority": "next_together" if key in ("fx", "equity", "etf") else "planned",
        }
        for key, label, broker, ready in rows
    ]
