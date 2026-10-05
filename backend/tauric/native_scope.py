"""Current daily native routes, independent of chart-provider credentials."""

NATIVE_ASSET_TYPES = {"crypto_spot": "crypto", "equity": "stock", "etf": "stock", "fx": "forex"}
FX_POLICY_CURRENCIES = {"USD", "EUR", "GBP", "JPY"}


def supported(spec):
    if spec.asset_type == "fx" and not {spec.base_currency, spec.quote_currency}.issubset(
        FX_POLICY_CURRENCIES
    ):
        return False
    return NATIVE_ASSET_TYPES.get(spec.asset_type) == spec.native_asset_type


def daily_scope(asset_type):
    if asset_type == "crypto_spot":
        return "Current daily crypto research. Native sources use USD; the Binance chart uses USDT. Chart snapshots are separate observations."
    if asset_type in ("equity", "etf"):
        scope = "Current daily research using native stock providers. Alpaca chart snapshots are separate regular-session observations and are not the graph's input data."
        if asset_type == "etf":
            scope += (
                " ETF research covers market, sentiment and news; company fundamentals are omitted."
            )
        return scope
    if asset_type == "fx":
        return "Forex daily research uses closed OANDA bid/ask candles, official central-bank headlines and policy-meeting dates. Run fetches daily evidence separately from the chart timeframe. Source gaps remain explicit; economic releases, policy benchmarks and account financing are supplied when their provider connections pass validation. Research status is REVIEW."
    return "Native research for this asset is not enabled."
