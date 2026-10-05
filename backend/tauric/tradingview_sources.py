"""Receipt-bound calendar/rate observations from the subscribed official MCP."""

import re
from datetime import timedelta
from decimal import Decimal

from .forex_sources import link, number, surprise, text, utc
from .models import now
from .tradingview_connection import credentials
from .tradingview_mcp import EconomicMCP

URL = "https://www.tradingview.com/mcp/docs"
COUNTRIES = {"USD": "US", "EUR": "EU", "GBP": "GB", "JPY": "JP"}
COUNTRY_NAMES = {
    "US": {"United States"},
    "EU": {"Euro Area", "European Union"},
    "GB": {"United Kingdom"},
    "JP": {"Japan"},
}


def rows(data, keys):
    if isinstance(data, list):
        result = data
    elif isinstance(data, dict):
        result = next((data[k] for k in keys if isinstance(data.get(k), list)), None)
    else:
        result = None
    if not isinstance(result, list) or len(result) > 2000:
        raise ValueError("Unrecognized or oversized economic response")
    return result


def field(row, *keys):
    return next((row[k] for k in keys if k in row and row[k] is not None), None)


def calendar(data, currency, country, stamp):
    result, seen = [], set()
    for row in rows(data, ("events", "data", "calendar", "result")):
        if not isinstance(row, dict):
            raise ValueError("Invalid economic event")
        # Responses filtered by currency may include another currency; never relabel it.
        if field(row, "currency") != currency:
            raise ValueError("Economic event currency mismatch")
        returned_country = field(row, "country", "country_code")
        if returned_country is not None and returned_country not in {
            country,
            *COUNTRY_NAMES[country],
        }:
            raise ValueError("Economic event country mismatch")
        key = text(field(row, "id", "event_id"), 100)
        title = text(field(row, "title", "event", "name"))
        raw_date = field(row, "date", "time", "timestamp")
        if not key or key in seen or not title or not isinstance(raw_date, str):
            raise ValueError("Economic event identity missing")
        seen.add(key)
        date_only = len(raw_date) == 10
        # Only explicitly offset timestamps have usable intraday release times.
        aware = bool(re.search(r"(?:Z|[+-]\d{2}:\d{2})$", raw_date))
        event_date = utc(raw_date)
        if not stamp - timedelta(days=8) <= event_date <= stamp + timedelta(days=15):
            raise ValueError("Event outside requested window")
        actual = text(field(row, "actual"), 80)
        if event_date > stamp or date_only or not aware:
            actual = None
        forecast = text(field(row, "forecast", "consensus"), 80)
        unit = text(field(row, "unit"), 80)
        scale = text(field(row, "scale"), 80)
        if scale not in {None, "K", "M", "B", "T"}:
            raise ValueError("Unsupported calendar display scale")
        # Calendar actual/forecast are already displayed values; retain K/M/B/T labels.
        display_unit = " ".join(v for v in (scale, unit) if v) or None
        result.append(
            {
                "id": key,
                "event": title,
                "date": event_date.date().isoformat(),
                "time": event_date.isoformat() if aware and not date_only else None,
                "timezone": "UTC",
                "actual": actual,
                "forecast": forecast,
                "provider_forecast": None,
                "previous": text(field(row, "previous"), 80),
                "unit": display_unit,
                "importance": text(field(row, "importance"), 80),
                "reference": text(field(row, "reference", "period"), 80),
                "url": link(field(row, "source_url", "sourceURL", "url")),
                "surprise": surprise(actual, forecast, display_unit),
            }
        )
    selected = sorted(
        result, key=lambda r: abs((utc(r["time"] or r["date"]) - stamp).total_seconds())
    )[:40]
    return sorted(selected, key=lambda r: (r["date"], r["time"] or "", r["id"])), max(
        0, len(result) - 40
    )


def policy_symbol(data, country):
    if isinstance(data, dict) and isinstance(data.get("country"), dict):
        if data["country"].get("code") != country:
            raise ValueError("Economic catalog country mismatch")
    candidates = []
    for row in rows(data, ("symbols", "indicators", "data", "tickers")):
        if not isinstance(row, dict):
            raise ValueError("Invalid economic catalog")
        symbol = field(row, "symbol", "ticker")
        name = text(field(row, "name", "description", "title"))
        if (
            isinstance(symbol, str)
            and re.fullmatch(r"ECONOMICS:" + country + r"[A-Z0-9]+", symbol)
            and name
            and name.lower()
            in {f"{label} Interest Rate".lower() for label in COUNTRY_NAMES[country]}
            and row.get("category") in {None, "mny"}
        ):
            candidates.append((symbol, name))
    if len(candidates) != 1:
        raise ValueError("A unique policy-rate benchmark could not be discovered")
    return candidates[0]


def rates(data, symbol, name, stamp):
    if not isinstance(data, dict):
        raise ValueError("Rate series metadata missing")
    if data.get("symbol") not in {None, symbol}:
        raise ValueError("Rate symbol mismatch")
    unit = text(data.get("unit"), 80)
    if not unit or unit.lower() not in {"%", "percent", "percentage"}:
        raise ValueError("Rate percentage unit missing")
    scale = data.get("scale", 1)
    # The live MCP returns percent release values (e.g. 4) with scale=100.
    # Calendar actual/actualRaw cross-checks confirm these are not scaled integers.
    if str(scale) not in {"1", "1.0", "100", "100.0"}:
        raise ValueError("Unsupported rate scale")
    values, seen = [], set()
    for row in rows(data, ("data", "series", "values")):
        date = utc(row["date"])
        value = number(row["value"])
        if date in seen or date > stamp + timedelta(seconds=90) or not -20 <= Decimal(value) <= 100:
            raise ValueError("Invalid rate date/value")
        seen.add(date)
        values.append((date, value))
    if not values:
        raise ValueError("No observed rate")
    latest = max(values, key=lambda row: row[0])
    if stamp - latest[0] > timedelta(days=120):
        raise ValueError("Rate benchmark too old")
    return [
        {
            "name": name,
            "symbol": symbol,
            "value": latest[1],
            "unit": unit,
            "provider_scale": str(scale),
            "effective_date": latest[0].date().isoformat(),
            "url": "https://www.tradingview.com/symbols/" + symbol.replace(":", "-"),
            "scope": "Provider interest-rate benchmark observation date; may be one facility or a point in a range. Series may lag official releases and be revised. Values retain provider percentage units without rescaling. No independently verified announcement time or historical vintage.",
        }
    ]


def collect(db, spec):
    stamp = now()
    sources = [
        {
            "currency": currency,
            "kind": kind,
            "provider": "TradingView official MCP",
            "url": URL,
            "checked_at": stamp.isoformat(),
            "status": "unavailable",
            "items": [],
            "warning": "Connect and test your subscribed TradingView account in Settings.",
        }
        for currency in (spec.base_currency, spec.quote_currency)
        for kind in ("economic_calendar", "policy_rate")
    ]
    try:
        config, revision = credentials(db)
        if not config.get("access_token"):
            return sources
        with EconomicMCP(config["access_token"]) as client:
            for source in sources:
                country = COUNTRIES[source["currency"]]
                source["connection_revision"] = revision
                try:
                    if source["kind"] == "economic_calendar":
                        data = client.call(
                            "get_economic_calendar",
                            {
                                "countries": country,
                                "currencies": source["currency"],
                                "date_from": (stamp - timedelta(days=7)).date().isoformat(),
                                "date_to": (stamp + timedelta(days=14)).date().isoformat(),
                            },
                        )
                        items, omitted = calendar(data, source["currency"], country, stamp)
                        source["items_omitted"] = omitted
                        source["warning"] = (
                            f"Nearest 40 events retained; {omitted} other returned events omitted."
                            if omitted
                            else None
                        )
                    else:
                        catalog = client.call(
                            "get_economic_symbols",
                            {"country": country, "search": "interest rate", "category": "mny"},
                        )
                        symbol, name = policy_symbol(catalog, country)
                        data = client.call(
                            "get_economic_data",
                            {
                                "symbol": symbol,
                                "date_from": (stamp - timedelta(days=120)).date().isoformat(),
                                "date_to": stamp.date().isoformat(),
                            },
                        )
                        items = rates(data, symbol, name, stamp)
                        source["warning"] = None
                    source["items"], source["status"] = items, "available"
                except Exception:
                    source["warning"] = (
                        "TradingView economic source was unavailable, rate-limited or not in a verified format. Test the connection in Settings."
                    )
    except Exception:
        for source in sources:
            source["warning"] = (
                "TradingView connection could not provide verified economic data. Reconnect or retry its Settings check."
            )
    return sources
