"""Timeframes for observations only; independent of execution interval contracts."""

import re
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

OANDA = {
    **{f"{n}s": f"S{n}" for n in (5, 10, 15, 30)},
    **{f"{n}m": f"M{n}" for n in (1, 2, 4, 5, 10, 15, 30)},
    **{f"{n}h": f"H{n}" for n in (1, 2, 3, 4, 6, 8, 12)},
    "1d": "D",
    "1w": "W",
    "1mo": "M",
}
YAHOO = {
    "1m": "1m",
    "2m": "2m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "90m": "90m",
    "1d": "1d",
    "1w": "1wk",
    "1mo": "1mo",
}
SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
NY = ZoneInfo("America/New_York")


def parse(value):
    match = re.fullmatch(r"([1-9][0-9]{0,3})(mo|s|m|h|d|w|y)", value)
    if not match:
        raise ValueError("Use a timeframe such as 5s, 7m, 2h, 1d, 2w, 1mo or 1y")
    return int(match[1]), match[2]


def add_months(opening, count):
    index = opening.year * 12 + opening.month - 1 + count
    year, month = divmod(index, 12)
    return opening.replace(
        year=year, month=month + 1, day=min(opening.day, monthrange(year, month + 1)[1])
    )


def closing(opening, interval, provider="yahoo", timezone=None):
    count, unit = parse(interval)
    local = (
        opening.astimezone(NY if provider == "oanda" else ZoneInfo(timezone or "UTC"))
        if unit in {"d", "w", "mo", "y"}
        else opening
    )
    if unit in {"mo", "y"}:
        if provider == "oanda":
            # The New York 17:00 session preceding the first calendar day
            # belongs to the next month (e.g. Aug 31 17:00 starts September).
            label = local + timedelta(hours=7)
            result = add_months(label, count * (12 if unit == "y" else 1)) - timedelta(hours=7)
        else:
            result = add_months(local, count * (12 if unit == "y" else 1))
    else:
        result = local + timedelta(seconds=count * SECONDS[unit])
    return result.astimezone(UTC)


def plan(interval, provider):
    count, unit = parse(interval)
    native = OANDA if provider == "oanda" else YAHOO
    if interval in native:
        return interval, native[interval], 1
    base = {"s": "5s", "m": "1m", "h": "1h", "d": "1d", "w": "1w", "mo": "1mo", "y": "1mo"}[unit]
    if base not in native or (unit == "s" and count % 5):
        raise ValueError(
            f"{provider} does not supply the base bars for {interval}; use source research or another feed"
        )
    factor = count * (12 if unit == "y" else 1) // (5 if unit == "s" else 1)
    if factor > 5000:
        raise ValueError(
            "This timeframe exceeds a single bounded observation; source research remains available"
        )
    return base, native[base], factor


def bucket(opening, interval, provider, timezone=None):
    count, unit = parse(interval)
    if unit in {"d", "w"}:
        local = opening.astimezone(NY if provider == "oanda" else ZoneInfo(timezone or "UTC"))
        origin = datetime(1970, 1, (2 if provider == "oanda" else 5) if unit == "w" else 1).date()
        size = count * (7 if unit == "w" else 1)
        day = origin + timedelta(days=(local.date() - origin).days // size * size)
        return local.replace(year=day.year, month=day.month, day=day.day).astimezone(UTC)
    if unit in {"mo", "y"}:
        # OANDA month bars begin at the configured 17:00 New York boundary.
        local = opening.astimezone(NY if provider == "oanda" else ZoneInfo(timezone or "UTC"))
        if provider == "oanda":
            local += timedelta(hours=7)
        index = local.year * 12 + local.month - 1
        size = count * (12 if unit == "y" else 1)
        year, month = divmod(index // size * size, 12)
        boundary = local.replace(year=year, month=month + 1, day=1)
        if provider == "oanda":
            boundary -= timedelta(hours=7)
        return boundary.astimezone(UTC)
    # Custom fixed-duration bars have explicitly declared UTC epoch alignment.
    size = count * SECONDS[unit]
    return datetime.fromtimestamp(int(opening.timestamp()) // size * size, UTC)


def aggregate(bars, interval, base, factor, provider, timezone=None):
    if factor == 1:
        return bars, 0
    groups = {}
    for bar in bars:
        opening = datetime.fromisoformat(bar["open_time"])
        groups.setdefault(bucket(opening, interval, provider, timezone), []).append(bar)
    result, dropped = [], 0
    for opening, group in sorted(groups.items()):
        # Never turn partial/gapped base bars into a completed requested bar.
        if (
            len(group) != factor
            or not all(b["closed"] for b in group)
            or any(
                a["close_time"] != b["open_time"] for a, b in zip(group, group[1:], strict=False)
            )
            or datetime.fromisoformat(group[0]["open_time"]) != opening
        ):
            dropped += 1
            continue
        from decimal import Decimal

        def sides(rows):
            return {
                "open": rows[0]["open"],
                "close": rows[-1]["close"],
                "high": str(max(Decimal(b["high"]) for b in rows)),
                "low": str(min(Decimal(b["low"]) for b in rows)),
            }

        merged = {
            "open_time": group[0]["open_time"],
            "close_time": group[-1]["close_time"],
            **sides(group),
            "volume": str(sum(Decimal(b["volume"]) for b in group)),
            "closed": True,
        }
        for side in ("bid", "ask"):
            if side in group[0]:
                merged[side] = sides([b[side] for b in group])
        result.append(merged)
    return result, dropped
