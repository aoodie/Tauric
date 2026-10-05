"""Add read-only FX and US-stock instruments; preserve all existing history."""

import json

from alembic import op
from sqlalchemy import text

revision = "0019_market_adapters"
down_revision = "0018_strategy_qualification"
branch_labels = None
depends_on = None

INSTRUMENTS = [
    {
        "id": "oanda:fx:EUR_USD",
        "symbol": "EUR/USD",
        "name": "EUR/USD FX",
        "provider": "oanda_v20",
        "provider_symbol": "EUR_USD",
        "asset_type": "fx",
        "base_currency": "EUR",
        "quote_currency": "USD",
        "volume_type": "provider_ticks",
        "native_symbol": "EURUSD=X",
        "native_asset_type": "forex",
        "analysis_scope": "Read-only FX charts and retrospective unlevered long-only research; native assessment and broker execution pending.",
        "calendar_id": "oanda_fx",
        "price_side": "bid_ask",
    },
    {
        "id": "oanda:fx:GBP_USD",
        "symbol": "GBP/USD",
        "name": "GBP/USD FX",
        "provider": "oanda_v20",
        "provider_symbol": "GBP_USD",
        "asset_type": "fx",
        "base_currency": "GBP",
        "quote_currency": "USD",
        "volume_type": "provider_ticks",
        "native_symbol": "GBPUSD=X",
        "native_asset_type": "forex",
        "analysis_scope": "Read-only FX charts and retrospective unlevered long-only research; native assessment and broker execution pending.",
        "calendar_id": "oanda_fx",
        "price_side": "bid_ask",
    },
    {
        "id": "oanda:fx:USD_JPY",
        "symbol": "USD/JPY",
        "name": "USD/JPY FX",
        "provider": "oanda_v20",
        "provider_symbol": "USD_JPY",
        "asset_type": "fx",
        "base_currency": "USD",
        "quote_currency": "JPY",
        "volume_type": "provider_ticks",
        "native_symbol": "USDJPY=X",
        "native_asset_type": "forex",
        "analysis_scope": "Read-only FX charts and retrospective unlevered long-only research; native assessment and broker execution pending.",
        "calendar_id": "oanda_fx",
        "price_side": "bid_ask",
    },
    {
        "id": "alpaca:equity:AAPL",
        "symbol": "AAPL",
        "name": "Apple",
        "provider": "alpaca_stocks",
        "provider_symbol": "AAPL",
        "asset_type": "equity",
        "base_currency": "AAPL",
        "quote_currency": "USD",
        "volume_type": "feed_shares",
        "native_symbol": "AAPL",
        "native_asset_type": "stock",
        "calendar_id": "us_equity",
        "price_side": "trades",
        "analysis_scope": "Read-only US regular-session charts and retrospective long-only research; native assessment and broker execution pending.",
    },
    {
        "id": "alpaca:equity:MSFT",
        "symbol": "MSFT",
        "name": "Microsoft",
        "provider": "alpaca_stocks",
        "provider_symbol": "MSFT",
        "asset_type": "equity",
        "base_currency": "MSFT",
        "quote_currency": "USD",
        "volume_type": "feed_shares",
        "native_symbol": "MSFT",
        "native_asset_type": "stock",
        "calendar_id": "us_equity",
        "price_side": "trades",
        "analysis_scope": "Read-only US regular-session charts and retrospective long-only research; native assessment and broker execution pending.",
    },
    {
        "id": "alpaca:etf:SPY",
        "symbol": "SPY",
        "name": "SPDR S&P 500 ETF",
        "provider": "alpaca_stocks",
        "provider_symbol": "SPY",
        "asset_type": "etf",
        "base_currency": "SPY",
        "quote_currency": "USD",
        "volume_type": "feed_shares",
        "native_symbol": "SPY",
        "native_asset_type": "stock",
        "calendar_id": "us_equity",
        "price_side": "trades",
        "analysis_scope": "Read-only US regular-session charts and retrospective long-only research; native assessment and broker execution pending.",
    },
]


def upgrade():
    bind = op.get_bind()
    for spec in INSTRUMENTS:
        bind.execute(
            text(
                "INSERT INTO instruments (id, definition) VALUES (:id, CAST(:definition AS json))"
            ),
            {"id": spec["id"], "definition": json.dumps(spec)},
        )


def downgrade():
    raise RuntimeError(
        "Restore a verified backup; instrument and observation history is immutable."
    )
