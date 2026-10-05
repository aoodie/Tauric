INSTRUMENTS = [
    {
        "id": f"binance:spot:{base}USDT",
        "symbol": f"{base}/USDT",
        "name": name,
        "provider": "binance_spot_public",
        "provider_symbol": f"{base}USDT",
        "asset_type": "crypto_spot",
        "base_currency": base,
        "quote_currency": "USDT",
        "volume_type": "exchange_base",
        "native_symbol": f"{base}-USD",
        "native_asset_type": "crypto",
        "analysis_scope": "Current daily crypto research; native sources use USD, chart uses USDT.",
    }
    for base, name in [("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana")]
]

INSTRUMENTS += [
    {
        "id": f"oanda:fx:{base}_{quote}",
        "symbol": f"{base}/{quote}",
        "name": f"{base}/{quote} FX",
        "provider": "oanda_v20",
        "provider_symbol": f"{base}_{quote}",
        "asset_type": "fx",
        "base_currency": base,
        "quote_currency": quote,
        "volume_type": "provider_ticks",
        "native_symbol": f"{base}{quote}=X",
        "native_asset_type": "forex",
        "analysis_scope": "Read-only FX charts and retrospective unlevered long-only research; native assessment and broker execution pending.",
        "calendar_id": "oanda_fx",
        "price_side": "bid_ask",
    }
    for base, quote in [("EUR", "USD"), ("GBP", "USD"), ("USD", "JPY")]
]

INSTRUMENTS += [
    {
        "id": f"alpaca:{asset}:{symbol}",
        "symbol": symbol,
        "name": name,
        "provider": "alpaca_stocks",
        "provider_symbol": symbol,
        "asset_type": asset,
        "base_currency": symbol,
        "quote_currency": "USD",
        "volume_type": "feed_shares",
        "native_symbol": symbol,
        "native_asset_type": "stock",
        "calendar_id": "us_equity",
        "price_side": "trades",
        "analysis_scope": "Read-only US regular-session charts and retrospective long-only research; native assessment and broker execution pending.",
    }
    for symbol, name, asset in [
        ("AAPL", "Apple", "equity"),
        ("MSFT", "Microsoft", "equity"),
        ("SPY", "SPDR S&P 500 ETF", "etf"),
    ]
]
