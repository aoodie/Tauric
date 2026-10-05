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
    return [
        {
            "asset_class": key,
            "label": label,
            "planned_broker_routes": broker,
            "data_ready": ready
            or (
                providers.get("oanda_v20", False)
                if key == "fx"
                else providers.get("alpaca_stocks", False)
                if key in ("equity", "etf")
                else False
            ),
            "data_implemented": ready or key in ("fx", "equity", "etf"),
            "screening_ready": ready or key in ("fx", "equity", "etf"),
            "replay_ready": ready or key in ("fx", "equity", "etf"),
            "native_research_ready": key in NATIVE_ASSET_TYPES,
            "live_execution_ready": False,
            "instruments": [r["id"] for r in instruments if r["asset_type"] == key],
            "detail": "Daily native crypto context; intraday setup assessment remains unavailable."
            if ready
            else "Read-only data, calendar-aware breakout screening, explicit-cost retrospective SMA replay, current daily native research and bound reviewed whole-share USD paper entries with attributed exits implemented. Chart and native sources are separate; ETF company fundamentals are omitted. Exact-version strategy assessment, automatic entries and live execution remain pending."
            if key in ("equity", "etf")
            else "Read-only data, calendar-aware price/activity screening, explicit-cost retrospective replay and exact-cost research qualification, and owner-reviewed long-only quote-currency paper entries with attributed exits implemented. Current Forex daily research includes policy and optional supplemental source context with explicit coverage gaps and REVIEW status. Forward execution qualification, automatic entries and live broker execution remain pending."
            if key == "fx"
            else "Data/research adapter is not enabled. Research requests are deferred.",
            "priority": "next_together" if key in ("fx", "equity", "etf") else "planned",
        }
        for key, label, broker, ready in rows
    ]
