"""Receipt-bound macro releases, policy benchmarks and account instrument financing.

All requests are read-only and fixed-host. Current observations never populate a
historical cost series or establish a broker fill/rollover charge.
"""

import json
import re
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote, urlsplit

import httpx

from . import forex_macro
from .models import now

VERSION = "tauric_fx_supplemental_v1"
TRADINGVIEW_VERSION = "tauric_fx_tradingview_v1"
COUNTRIES = {
    "USD": "United States",
    "EUR": "Euro Area",
    "GBP": "United Kingdom",
    "JPY": "Japan",
    "AUD": "Australia",
    "CAD": "Canada",
    "CHF": "Switzerland",
    "NZD": "New Zealand",
}
HOST = "https://api.tradingeconomics.com"


def text(value, limit=300):
    if value is None:
        return None
    value = str(value).strip()
    if len(value) > limit:
        raise ValueError("Oversized field")
    if value.lower() in {"nan", "infinity", "+infinity", "-infinity"}:
        raise ValueError("Non-finite provider observation")
    return value or None


def number(value):
    value = text(value, 80)
    if value is None:
        raise ValueError("Missing numeric observation")
    result = Decimal(value)
    if not result.is_finite() or abs(result) > Decimal("1e12"):
        raise ValueError("Invalid numeric observation")
    return format(result, "f")


def utc(value):
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    # Trading Economics documents provider dates as UTC, including offset-free dates.
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def link(value):
    value = text(value, 1500)
    if not value:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.port
    ):
        return None
    return value


def fetch(client, url, headers):
    started, chunks, length = time.monotonic(), [], 0
    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError("Redirect rejected")
        for chunk in response.iter_bytes():
            length += len(chunk)
            if length > 1_000_000 or time.monotonic() - started > 15:
                raise ValueError("Response exceeds collection bounds")
            chunks.append(chunk)
    data = json.loads(b"".join(chunks), parse_float=Decimal)
    return data


def surprise(actual, forecast, unit=None):
    pattern = r"([+-]?\d+(?:\.\d+)?)(%|K|M|B)?"
    left, right = re.fullmatch(pattern, actual or ""), re.fullmatch(pattern, forecast or "")
    if not left or not right or left[2] != right[2]:
        return None
    return {
        "value": number(Decimal(left[1]) - Decimal(right[1])),
        "unit": "percentage points"
        if left[2] == "%"
        or (not left[2] and unit and unit.lower() in {"%", "percent", "percentage"})
        else left[2] or unit or "provider units",
        "basis": "actual minus survey consensus; same displayed units",
    }


def calendar(rows, country, stamp):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Unbounded calendar")
    result, ids = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("Country") != country:
            raise ValueError("Calendar country mismatch")
        key = text(row.get("CalendarId"), 80)
        date = utc(row["Date"])
        update = utc(row["LastUpdate"])
        if not key or key in ids or update > stamp + timedelta(seconds=90):
            raise ValueError("Calendar binding/date mismatch")
        ids.add(key)
        if not stamp - timedelta(days=8) <= date <= stamp + timedelta(days=15):
            raise ValueError("Calendar outside requested window")
        actual, forecast = text(row.get("Actual"), 80), text(row.get("Forecast"), 80)
        date_only = str(row.get("DateSpan")) != "0"
        # Future scheduled observations do not contain usable released actuals.
        if date > stamp or date_only:
            actual = None
        result.append(
            {
                "id": key,
                "event": text(row.get("Event")),
                "category": text(row.get("Category")),
                "date": date.date().isoformat(),
                "time": None if date_only else date.isoformat(),
                "timezone": "UTC",
                "actual": actual,
                "forecast": forecast,
                "provider_forecast": text(row.get("TEForecast"), 80),
                "previous": text(row.get("Previous"), 80),
                "revised": text(row.get("Revised"), 80),
                "unit": text(row.get("Unit"), 80),
                "reference": text(row.get("Reference"), 80),
                "importance": row.get("Importance"),
                "updated_at": update.isoformat(),
                "source": text(row.get("Source")),
                "url": link(row.get("SourceURL")),
                "surprise": surprise(actual, forecast, text(row.get("Unit"), 80)),
            }
        )
    return sorted(result, key=lambda r: (r["date"], r["time"] or "", r["id"]))


def rates(rows, country, stamp):
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("Missing policy benchmark")
    row = rows[0]
    date = utc(row["LatestValueDate"])
    if (
        row.get("Country") != country
        or row.get("Category") != "Interest Rate"
        or date > stamp + timedelta(seconds=90)
        or (stamp - date).days > 120
    ):
        raise ValueError("Mismatched or old policy benchmark")
    unit = text(row.get("Unit"), 80)
    if not unit or unit.lower() not in {"%", "percent", "percentage"}:
        raise ValueError("Policy unit missing")
    return [
        {
            "name": text(row.get("Title")),
            "value": number(row["LatestValue"]),
            "unit": unit,
            "effective_date": date.date().isoformat(),
            "source": text(row.get("Source")),
            "url": link(row.get("SourceURL")),
            "scope": "Provider benchmark; may represent one point of a target range or one of several policy facilities.",
        }
    ]


def financing(payload, spec):
    rows = payload.get("instruments")
    if (
        not isinstance(rows, list)
        or len(rows) != 1
        or rows[0].get("name") != spec.provider_symbol
        or rows[0].get("type") != "CURRENCY"
    ):
        raise ValueError("Instrument financing mismatch")
    row = rows[0]["financing"]
    long, short = number(row["longRate"]), number(row["shortRate"])
    if max(abs(Decimal(long)), abs(Decimal(short))) > 1:
        raise ValueError("Financing rate outside bounds")
    days, seen = [], set()
    for item in row["financingDaysOfWeek"]:
        day, count = item["dayOfWeek"], item["daysCharged"]
        if (
            day
            not in {"MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"}
            or day in seen
            or type(count) is not int
            or not 0 <= count <= 7
        ):
            raise ValueError("Invalid financing schedule")
        seen.add(day)
        days.append({"day": day, "days_charged": count})
    if not days:
        raise ValueError("Financing schedule unavailable")
    return [
        {
            "instrument": spec.symbol,
            "long_rate_decimal": long,
            "short_rate_decimal": short,
            "schedule": days,
            "scope": "Current account-specific quoted rates; not a historical series or an actual charge. Charge timing and conversion depend on the broker account.",
        }
    ]


def collect(db, spec):
    from .forex_connection import saved
    from .market_connections import stored_connection
    from .tradingview_connection import saved as tradingview_saved

    stamp, sources = now(), []
    key, revision = saved(db)
    tv_config, _ = tradingview_saved(db)
    use_tradingview = bool(tv_config.get("access_token")) or not key
    with httpx.Client(timeout=httpx.Timeout(6, connect=4), follow_redirects=False) as client:
        for currency in [spec.base_currency, spec.quote_currency]:
            country = COUNTRIES.get(currency)
            encoded = quote(country.lower(), safe="") if country else ""
            for kind in ("economic_calendar", "policy_rate"):
                url = (
                    f"{HOST}/calendar/country/{encoded}/{(stamp - timedelta(days=7)).date()}/{(stamp + timedelta(days=14)).date()}"
                    if kind == "economic_calendar"
                    else f"{HOST}/country/{encoded}/interest%20rate"
                )
                source = {
                    "currency": currency,
                    "kind": kind,
                    "provider": "Trading Economics",
                    "url": url if country else HOST,
                    "status": "unavailable",
                    "items": [],
                    "checked_at": stamp.isoformat(),
                    "warning": "Configure Trading Economics calendar and indicators access in Settings.",
                    "connection_revision": revision,
                }
                if not country:
                    source["warning"] = (
                        f"No verified economic country mapping for {currency}; this coverage is unavailable."
                    )
                if country and key and not use_tradingview:
                    try:
                        data = fetch(client, url, {"Authorization": key})
                        source["items"] = (
                            calendar(data, country, stamp)
                            if kind == "economic_calendar"
                            else rates(data, country, stamp)
                        )
                        if not source["items"]:
                            raise ValueError("No verified observations")
                        source["status"], source["warning"] = "available", None
                        if kind == "economic_calendar" and len(source["items"]) > 40:
                            total = len(source["items"])
                            selected = sorted(
                                source["items"],
                                key=lambda r: abs(
                                    (utc(r["time"] or r["date"]) - stamp).total_seconds()
                                ),
                            )[:40]
                            source["items"] = sorted(
                                selected, key=lambda r: (r["date"], r["time"] or "", r["id"])
                            )
                            source["items_omitted"] = total - 40
                            source["warning"] = (
                                f"Showing the nearest 40 releases; {total - 40} other returned events omitted from this bounded report."
                            )
                        source["response_checksum"] = forex_macro.digest(
                            json.loads(json.dumps(data, default=str))
                        )
                    except Exception:
                        source["warning"] = (
                            "Source access or data validation failed; check API entitlement and retry."
                        )
                sources.append(source)
        config, revision, _ = stored_connection(db, "oanda")
        source = {
            "currency": spec.quote_currency,
            "kind": "broker_financing",
            "provider": "OANDA",
            "url": "https://developer.oanda.com/rest-live-v20/primitives-df/#InstrumentFinancing",
            "status": "unavailable",
            "items": [],
            "checked_at": stamp.isoformat(),
            "warning": "Save an OANDA account ID and token to read account-specific instrument financing.",
            "connection_revision": revision,
        }
        if config.account_id and config.token.get_secret_value():
            try:
                host = (
                    "api-fxpractice.oanda.com"
                    if config.environment == "practice"
                    else "api-fxtrade.oanda.com"
                )
                data = fetch(
                    client,
                    f"https://{host}/v3/accounts/{config.account_id}/instruments?instruments={spec.provider_symbol}",
                    {"Authorization": "Bearer " + config.token.get_secret_value()},
                )
                source["items"] = financing(data, spec)
                source["status"], source["warning"] = "available", None
                # Only selected public fields are retained; account IDs and tokens never enter evidence.
                source["response_checksum"] = forex_macro.digest(source["items"])
            except Exception:
                source["warning"] = "Account instrument financing was unavailable or invalid."
        sources.append(source)
    if use_tradingview:
        from .tradingview_sources import collect as tradingview_collect

        sources[:4] = tradingview_collect(db, spec)
        for source in sources[:4]:
            source["checked_at"] = stamp.isoformat()
    data = {
        "version": TRADINGVIEW_VERSION if use_tradingview else VERSION,
        "instrument_id": spec.id,
        "currencies": [spec.base_currency, spec.quote_currency],
        "fetched_at": stamp.isoformat(),
        "sources": sources,
        "coverage": "TradingView subscribed read-only MCP: past seven and upcoming fourteen UTC days for both currencies, nearest 40 returned events per economy, and discovered interest-rate benchmarks where verified. Missing or ambiguous observations remain unavailable. Provider beta market data may be delayed; OANDA separately supplies prices and account financing. Calendar/series observations are current receipts, not historical vintages."
        if use_tradingview
        else "Past seven and upcoming fourteen UTC days for the two currency economies, bounded to the nearest 40 returned releases each; provider entitlement may limit coverage. Rates are current provider benchmarks. Observations are not point-in-time historical evidence.",
    }
    return {"data": data, "checksum": forex_macro.digest(json.loads(json.dumps(data, default=str)))}


def validate(snapshot, spec):
    data = snapshot["data"]
    stamp = utc(data["fetched_at"])
    if (
        snapshot["checksum"] != forex_macro.digest(json.loads(json.dumps(data, default=str)))
        or data["version"] not in {VERSION, TRADINGVIEW_VERSION}
        or data["instrument_id"] != spec["id"]
        or data["currencies"] != [spec["base_currency"], spec["quote_currency"]]
        or not -90 <= (now() - stamp).total_seconds() <= 21600
        or len(data["sources"]) != 5
    ):
        raise ValueError("Supplemental Forex evidence binding/freshness failed")
    expected = [
        (currency, kind)
        for currency in data["currencies"]
        for kind in ("economic_calendar", "policy_rate")
    ] + [(spec["quote_currency"], "broker_financing")]
    for source, (currency, kind) in zip(data["sources"], expected, strict=True):
        if (
            source["currency"] != currency
            or source["kind"] != kind
            or source["status"] not in {"available", "unavailable"}
            or not isinstance(source["items"], list)
            or len(source["items"]) > 1000
            or utc(source["checked_at"]) != stamp
        ):
            raise ValueError("Supplemental source binding failed")
        parsed = urlsplit(source["url"])
        if (
            parsed.scheme != "https"
            or parsed.hostname
            != (
                "developer.oanda.com"
                if kind == "broker_financing"
                else "www.tradingview.com"
                if data["version"] == TRADINGVIEW_VERSION
                else "api.tradingeconomics.com"
            )
            or parsed.username
            or parsed.password
            or parsed.port
            or parsed.query
        ):
            raise ValueError("Supplemental source URL mismatch")
    return data
