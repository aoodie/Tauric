"""Exact broker product coverage is independent of the daily-report catalog."""

import re

from . import native_scope


def provider(spec):
    if spec.provider == "oanda_v20":
        if spec.asset_type == "fx" and native_scope.supported(spec):
            return "oanda"
        if (
            spec.asset_type in {"commodity", "cfd"}
            and spec.classification_source == "provider_product"
            and spec.provider_product_type == ("METAL" if spec.asset_type == "commodity" else "CFD")
            and re.fullmatch(r"[A-Z0-9]{1,20}_[A-Z]{3}", spec.provider_symbol)
            and spec.quote_currency == spec.provider_symbol[-3:]
            and spec.quote_currency not in {"XAU", "XAG", "XPT", "XPD"}
        ):
            return "oanda"
    if (
        spec.provider == "alpaca_stocks"
        and spec.asset_type in {"equity", "etf"}
        and native_scope.supported(spec)
    ):
        return "alpaca"
    return None
