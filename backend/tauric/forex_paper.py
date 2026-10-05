"""Explicit fixed-cost FX cash simulation. Never reads or places broker orders."""

from decimal import Decimal

from .asset_costs import rollover_times
from .models import PaperDeployment
from .research_contracts import AssetCosts


def validate_quote(db, view, stamp):
    """Recheck the frozen source; cached quality flags cannot replace integrity."""
    import hashlib
    import json

    from .data_quality import verify
    from .models import MarketSnapshot

    stored = db.get(MarketSnapshot, view.snapshot_id)
    if (
        not stored
        or stored.instrument_id != view.instrument.id
        or stored.interval != "1m"
        or stored.data.get("source") != "oanda_v20"
        or stored.checksum
        != hashlib.sha256(json.dumps(stored.data, sort_keys=True).encode()).hexdigest()
    ):
        raise ValueError("Forex quote checksum/source mismatch")
    context = view.data_context.model_dump(mode="json")
    if context["calendar_id"] != "oanda_fx" or context["price_side"] != "bid_ask":
        raise ValueError("Forex quote context mismatch")
    quality = verify(stored.data["candles"], "1m", context, stamp)
    if quality.missing_bars:
        raise ValueError("Forex quote contains trading gaps")


def closing_financing(db, entry, ending):
    dep = db.get(PaperDeployment, entry.deployment_id)
    rate = dep.payload.get("fx_financing_daily_bps")
    if rate is None:
        return Decimal(0), []
    costs = AssetCosts(
        asset_type="fx",
        spread_bps="0",
        financing_daily_bps=rate,
        triple_rollover_weekday=dep.payload.get("fx_triple_rollover_weekday", 2),
    )
    notional = Decimal(entry.payload["quantity"]) * Decimal(entry.payload["price"])
    events = [
        {
            "at": at.isoformat(),
            "days": days,
            "amount": format(notional * Decimal(rate) / 10000 * days, "f"),
            "basis": "fixed opening quote-currency notional; debit positive, credit negative",
        }
        for at, days in rollover_times(entry.created_at, ending, costs)
    ]
    return sum((Decimal(e["amount"]) for e in events), Decimal(0)), events
