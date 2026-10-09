"""Read-only connected commodity evidence; no native jobs or financial actions."""

import json
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session
from tauric import open_research
from tauric.database import engine
from tauric.market_connections import stored_connection
from tauric.models import now


def summary(value):
    return {
        key: value.get(key)
        for key in (
            "symbol",
            "provider",
            "instrument_type",
            "interval",
            "base_interval",
            "aggregation_factor",
            "state",
            "last_closed_at",
            "checksum",
            "native_research_supported",
            "execution_eligible",
            "warnings",
            "dropped_partial_groups",
            "quarantined_invalid_ohlc_bars",
        )
    } | {"closed_bars": len(value["candles"])}


if __name__ == "__main__":
    proof = {"checked_at": now().isoformat(), "observations": [], "gaps": []}
    with Session(engine()) as db:
        connection, _, _ = stored_connection(db, "oanda")
        catalog, _ = open_research.oanda_catalog(connection)
        proof["catalog"] = {
            "metals": sum(i["instrument_type"] == "METAL" for i in catalog),
            "cfds": sum(i["instrument_type"] == "CFD" for i in catalog),
            "silver_verified": any(i["symbol"] == "XAG_USD" for i in catalog),
        }
        for interval in ["15m", "1h", "1d", "1w", "1mo", "7m", "1y"]:
            try:
                observed = open_research.oanda_prices(
                    "XAG_USD", interval, connection, count=10
                )
                proof["observations"].append(summary(observed))
            except (HTTPException, ValueError) as error:
                proof["gaps"].append(
                    {
                        "symbol": "XAG_USD",
                        "interval": interval,
                        "detail": getattr(error, "detail", type(error).__name__),
                    }
                )
        finish = now() - timedelta(days=10)
        try:
            observed = open_research.oanda_prices(
                "XAG_USD",
                "1h",
                connection,
                count=10,
                start=finish - timedelta(days=2),
                end=finish,
            )
            proof["observations"].append(summary(observed))
        except (HTTPException, ValueError) as error:
            proof["gaps"].append(
                {
                    "history": True,
                    "detail": getattr(error, "detail", type(error).__name__),
                }
            )
        oil = next(
            (
                i
                for i in catalog
                if i["instrument_type"] == "CFD"
                and ("brent" in i["name"].lower() or "crude" in i["name"].lower())
            ),
            None,
        )
        if oil:
            try:
                proof["observations"].append(
                    summary(
                        open_research.oanda_prices(
                            oil["symbol"], "1h", connection, count=10
                        )
                    )
                )
            except (HTTPException, ValueError) as error:
                proof["gaps"].append(
                    {
                        "symbol": oil["symbol"],
                        "detail": getattr(error, "detail", type(error).__name__),
                    }
                )
    for symbol, interval in [("KC=F", "1w"), ("CL=F", "1mo")]:
        try:
            proof["observations"].append(
                summary(open_research.yahoo_prices(symbol, interval, count=10))
            )
        except (HTTPException, ValueError) as error:
            proof["gaps"].append(
                {
                    "symbol": symbol,
                    "interval": interval,
                    "detail": getattr(error, "detail", type(error).__name__),
                }
            )
    try:
        found = open_research.discover(None, "coffee", "yahoo")
        proof["public_discovery"] = {
            "items": found["items"],
            "gaps": found["coverage_gaps"],
        }
        found = open_research.search("silver supply demand Silver Institute")
        proof["source_search"] = {
            "count": len(found["items"]),
            "urls": [i["url"] for i in found["items"]],
        }
        source = open_research.read("https://www.silverinstitute.org/")
        proof["source_read"] = {
            "url": source["url"],
            "resolved_url": source["resolved_url"],
            "checksum": source["content_sha256"],
            "characters": len(source["text"]),
        }
    except (HTTPException, ValueError) as error:
        proof["gaps"].append(
            {
                "source_research": True,
                "detail": getattr(error, "detail", type(error).__name__),
            }
        )
    assert proof["catalog"]["silver_verified"]
    assert {"15m", "1h", "1d", "1w", "1mo", "7m", "1y"} <= {
        i["interval"]
        for i in proof["observations"]
        if i["provider"] == "oanda" and i["symbol"] == "XAG/USD"
    }
    assert any(i["state"] == "historical" for i in proof["observations"])
    print(json.dumps(proof), flush=True)
