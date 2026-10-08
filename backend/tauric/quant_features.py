"""Versioned closed-bar features with purged chronological targets."""

import math
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

VERSION = "session-features-v1"
FEATURES = [
    "return_1",
    "return_3",
    "return_lookback",
    "volatility",
    "range_fraction",
    "close_location",
    "activity_ratio",
    "spread_fraction",
    "hour_sin",
    "hour_cos",
]


def stamp(value):
    return datetime.fromisoformat(value).astimezone(UTC)


def in_session(opening, closing, session):
    """Retain whole bars; overnight sessions belong to their opening weekday."""
    zone = ZoneInfo(session.timezone)
    local = opening.astimezone(zone)
    op, cl = time.fromisoformat(session.opening), time.fromisoformat(session.closing)
    day = local.date()
    if cl <= op and local.time().replace(tzinfo=None) < op:
        day -= timedelta(days=1)
    if cl == op:
        # A full-day window has no time-of-day exclusion. Provider daily bars
        # may cross UTC midnight and have 23/25-hour DST-adjusted durations.
        return day.weekday() in session.weekdays
    start = datetime.combine(day, op, zone)
    end = datetime.combine(day + timedelta(days=cl <= op), cl, zone)
    return day.weekday() in session.weekdays and start <= opening and closing <= end


def vector(candles, index, lookback):
    prefix = candles[index - lookback : index + 1]
    prices = [float(b["close"]) for b in prefix]
    returns = [math.log(b / a) for a, b in zip(prices, prices[1:], strict=False)]
    mean = sum(returns) / len(returns)
    bar = prefix[-1]
    high, low, close = (float(bar[k]) for k in ("high", "low", "close"))
    activity = [float(b["volume"]) for b in prefix[:-1]]
    average = sum(activity) / len(activity)
    hour = stamp(bar["close_time"]).hour + stamp(bar["close_time"]).minute / 60
    bid, ask = bar.get("bid"), bar.get("ask")
    values = [
        returns[-1],
        math.log(prices[-1] / prices[-4]),
        math.log(prices[-1] / prices[0]),
        math.sqrt(sum((r - mean) ** 2 for r in returns) / len(returns)),
        (high - low) / close,
        (close - low) / (high - low) if high > low else 0.5,
        float(bar["volume"]) / average if average else 0,
        (float(ask["close"]) - float(bid["close"])) / close if bid and ask else 0,
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
    ]
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Feature arithmetic must remain finite")
    return values


def build(candles, request):
    ends = [request.train_end, request.validation_end, request.test_end]
    partitions = {"train": [], "validation": [], "test": []}
    horizon, lookback = request.horizon_bars, request.lookback_bars
    for i in range(lookback, len(candles) - horizon):
        bar, target = candles[i], candles[i + horizon]
        observed = stamp(bar["close_time"])
        target_at = stamp(target["close_time"])
        if not in_session(stamp(bar["open_time"]), observed, request.session):
            continue
        segment = next((j for j, end in enumerate(ends) if observed <= end), None)
        if segment is None or target_at > ends[segment]:
            continue  # purge targets overlapping the next partition
        key = ("train", "validation", "test")[segment]
        entry = candles[i + 1]
        # Predict executable next-open to later-close return, including historical FX spread.
        entry_price = float((entry.get("ask") or entry)["open"])
        exit_price = float((target.get("bid") or target)["close"])
        partitions[key].append(
            {
                "index": i,
                "time": observed.isoformat(),
                "target_time": target_at.isoformat(),
                "features": vector(candles, i, lookback),
                "label": exit_price / entry_price - 1,
            }
        )
    if any(len(rows) < 20 for rows in partitions.values()):
        raise ValueError(
            "Each purged training, validation and test partition needs at least 20 samples"
        )
    eligible = [
        i
        for i, bar in enumerate(candles)
        if i >= lookback
        and stamp(bar["close_time"]) <= request.test_end
        and in_session(stamp(bar["open_time"]), stamp(bar["close_time"]), request.session)
    ]
    latest = eligible[-1]
    return {
        "version": VERSION,
        "features": FEATURES,
        "partitions": partitions,
        "forecast": {
            "time": candles[latest]["close_time"],
            "features": vector(candles, latest, lookback),
        },
        "horizon_bars": horizon,
        "lookback_bars": lookback,
        "normalization": "none; tree model sees raw features",
        "label": "next executable open to horizon close return",
    }
