"""Pure trailing-bar setup scoring. No database, providers, models or execution."""

from datetime import timedelta
from decimal import ROUND_DOWN, Decimal, localcontext

VERSION = "multifactor_v1"
HIGHER = {
    "1m": ("5m", "15m"),
    "5m": ("15m", "1h"),
    "15m": ("1h", "4h"),
    "1h": ("4h", "1d"),
    "4h": ("1d",),
    "1d": (),
}
D = Decimal
SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def clamp(value):
    return max(D(0), min(D(1), value))


def text(value):
    return format(value.quantize(D("0.00000001")), "f")


def closed(feed, stamp):
    return sorted(
        (b for b in feed.candles if b.closed and b.close_time <= stamp), key=lambda b: b.close_time
    )


def ema(values, period):
    if len(values) < period:
        return None
    value = sum(values[:period]) / period
    alpha = D(2) / (period + 1)
    for close in values[period:]:
        value = alpha * close + (1 - alpha) * value
    return value


def band_width(bars):
    values = [D(b.close) for b in bars]
    mean = sum(values) / len(values)
    deviation = (sum((v - mean) ** 2 for v in values) / len(values)).sqrt()
    return 4 * deviation / mean if mean > 0 else D(0)


def pivots(bars, side, radius=2):
    values = [
        D(getattr(getattr(b, side, None) or b, "low" if side == "bid" else "high")) for b in bars
    ]
    low = side == "bid"
    return [
        v
        for i, v in enumerate(values)
        if radius <= i < len(values) - radius
        and all(
            v < x if low else v > x for x in values[i - radius : i] + values[i + 1 : i + radius + 1]
        )
    ]


def geometry(feed, bars, direction):
    last = bars[-1]
    long = direction == "long"
    price_side = last.ask if long else last.bid
    entry = D((price_side or last).close)
    stops = (
        [v for v in pivots(bars[-54:], "bid") if v < entry]
        if long
        else [v for v in pivots(bars[-54:], "ask") if v > entry]
    )
    targets = [
        v
        for v in pivots(bars[-54:], "ask" if long else "bid")
        if (v > entry if long else v < entry)
    ]
    result = {
        "suggested_entry": text(entry),
        "suggested_stop": None,
        "target_2r": None,
        "target_3r": None,
        "structural_target": None,
        "risk_reward_ratio": None,
    }
    if feed.instrument.asset_type == "fx" and any(not b.bid or not b.ask for b in bars[-54:]):
        return result, D(0), False
    if not stops:
        return result, D(0), False
    stop = stops[-1]
    risk = abs(entry - stop)
    if risk <= 0:
        return result, D(0), False
    sign = D(1) if long else D(-1)
    result.update(
        suggested_stop=text(stop),
        target_2r=text(entry + sign * 2 * risk) if entry + sign * 2 * risk > 0 else None,
        target_3r=text(entry + sign * 3 * risk) if entry + sign * 3 * risk > 0 else None,
    )
    if not targets:
        return result, D(0), False
    target = min(targets) if long else max(targets)
    ratio = abs(target - entry) / risk
    result.update(structural_target=text(target), risk_reward_ratio=text(ratio))
    return result, clamp(ratio / D("2.5")), True


def calculate_setup_score(
    feed, features, parameters, *, higher_feeds=None, macro=None, direction="long", stamp=None
):
    """No component can inspect a candle later than the signal's close time."""
    with localcontext() as ctx:
        ctx.prec = 120
        eligible = [b for b in feed.candles if b.closed and b.close_time <= feed.provider_time]
        stamp = stamp or (max(b.close_time for b in eligible) if eligible else feed.provider_time)
        bars = closed(feed, stamp)
        factors = {}
        points = {
            "pattern": D(0),
            "activity": D(0),
            "trend": D(0),
            "squeeze": D(0),
            "geometry": D(0),
        }
        missing = []
        geometry_values = {
            k: None
            for k in (
                "suggested_entry",
                "suggested_stop",
                "target_2r",
                "target_3r",
                "structural_target",
                "risk_reward_ratio",
            )
        }
        if len(bars) >= 22:
            prior = bars[-21:-1]
            before = bars[-22:-2]
            previous_closes = [D(b.close) for b in before]
            atr = (
                sum(
                    max(D(b.high) - D(b.low), abs(D(b.high) - c), abs(D(b.low) - c))
                    for b, c in zip(prior, previous_closes, strict=True)
                )
                / 20
            )
            reference = D(
                features.get(
                    "prior_high",
                    features.get("prior_low", features.get("reference_price", bars[-1].close)),
                )
            )
            distance = max(
                D(0), (D(bars[-1].close) - reference) * (1 if direction == "long" else -1)
            )
            points["pattern"] = clamp(distance / atr) if atr else D(0)
            average = sum(D(b.volume) for b in prior) / 20
            ratio = D(bars[-1].volume) / average if average > 0 else D(0)
            points["activity"] = clamp(ratio / D("2.5"))
            factors.update(
                pattern=text(points["pattern"]),
                activity=text(ratio) + "x",
                activity_type=feed.instrument.volume_type,
            )
            geometry_values, points["geometry"], valid = geometry(feed, bars, direction)
            factors["geometry"] = (
                (geometry_values["risk_reward_ratio"] + ":1") if valid else "unknown"
            )
            if not valid:
                missing.append("structural_geometry")
        else:
            missing.extend(("pattern", "activity", "structural_geometry"))
            factors.update(pattern="unknown", activity="unknown", geometry="unknown")
        if len(bars) >= 41:
            widths = [band_width(bars[i - 19 : i + 1]) for i in range(len(bars) - 22, len(bars))]
            previous, current = widths[-2:]
            historical = sorted(widths[:-2])
            compressed = previous <= historical[3]
            expanding = previous > 0 and current >= previous * D("1.2")
            if compressed and expanding:
                points["squeeze"] = clamp((current / previous - 1) / D("0.5"))
            factors["squeeze"] = "expanding" if compressed and expanding else "no expansion"
        else:
            missing.append("squeeze")
            factors["squeeze"] = "unknown"
        contexts = HIGHER[feed.interval]
        trend = []
        for interval in contexts:
            context = (higher_feeds or {}).get(interval)
            valid = (
                context
                and context.instrument.id == feed.instrument.id
                and context.state == "fresh"
                and not context.gaps
            )
            context_bars = closed(context, stamp) if valid else []
            if context_bars and stamp - context_bars[-1].close_time > timedelta(
                seconds=SECONDS[interval]
            ):
                context_bars = []
            values = [D(b.close) for b in context_bars]
            fast, slow = ema(values, 50), ema(values, 200)
            if fast is None or slow is None:
                trend.append(None)
            else:
                trend.append(
                    values[-1] > fast > slow if direction == "long" else values[-1] < fast < slow
                )
        if not contexts or None in trend:
            missing.append("higher_timeframe_trend")
            factors["trend"] = "unknown"
        else:
            points["trend"] = D(sum(trend)) / len(trend)
            factors["trend"] = (
                ("aligned" if all(trend) else "counter trend") + " (" + "/".join(contexts) + ")"
            )
        macro_status = (macro or {}).get("status", "unknown")
        if (macro or {}).get("complete") is False:
            missing.append("economic_calendar_coverage")
        if macro_status not in {"clean", "event", "not_applicable"}:
            macro_status = "unknown"
            missing.append("economic_calendar")
        penalty = D("0.20") if macro_status in {"event", "unknown"} else D(0)
        factors["macro"] = macro_status
        weights = {
            "pattern": D("0.20"),
            "activity": D("0.25"),
            "trend": D("0.25"),
            "squeeze": D("0.15"),
            "geometry": D("0.15"),
        }
        contributions = {k: text(v * weights[k]) for k, v in points.items()}
        score = clamp(sum(v * weights[k] for k, v in points.items()) - penalty)
        if missing:
            score = min(score, D("0.819999"))
        # Classification precedes both float conversion and display rounding.
        rating = "HIGH" if score >= D("0.82") else "MODERATE" if score >= D("0.65") else "LOW"
        return {
            "version": VERSION,
            "score": format(score.quantize(D("0.000000000001"), rounding=ROUND_DOWN), "f"),
            "rating": rating,
            "factors": factors,
            "contributions": contributions,
            "macro_penalty": text(penalty),
            "missing_evidence": missing,
            "bar_close_time": stamp.isoformat(),
            "geometry": geometry_values,
            "execution_authority": False,
            "context_receipts": {
                k: {
                    "snapshot_id": v.snapshot_id,
                    "checksum": v.checksum,
                    "received_at": v.received_at.isoformat() if v.received_at else None,
                }
                for k, v in (higher_feeds or {}).items()
                if k in contexts
            },
            "macro_receipts": (macro or {}).get("receipts", []),
        }
