"""Fixed causal research signals. No generated code, broker access or order authority."""

import math
from bisect import bisect_right
from statistics import NormalDist

from .discovery_contracts import ResearchStrategy
from .quant_features import stamp

PAPERS = [
    {
        "title": "Qlib: An AI-oriented Quantitative Investment Platform",
        "year": 2020,
        "url": "https://arxiv.org/abs/2009.11189",
        "use": "Machine-learning research infrastructure",
    },
    {
        "title": "R&D-Agent-Quant",
        "year": 2025,
        "url": "https://arxiv.org/abs/2505.15155",
        "use": "Hypothesis, experiment and feedback workflow; not evidence of Tauric performance",
    },
    {
        "title": "The Deflated Sharpe Ratio",
        "year": 2014,
        "url": "https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf",
        "use": "Selection-bias diagnostics across recorded trials",
    },
]


def component_signal(bars, index, component, holding):
    slow, fast = component.slow_window, component.fast_window
    if index < slow:
        return False, "Waiting for the required closed-bar history"
    closes = [float(b["close"]) for b in bars[index - slow + 1 : index + 1]]
    average = sum(closes) / slow
    if component.family == "trend":
        short = sum(closes[-fast:]) / fast
        return (
            short > average,
            "Fast average above slow average" if short > average else "Trend filter is flat",
        )
    if component.family == "mean_reversion":
        deviation = math.sqrt(sum((p - average) ** 2 for p in closes) / slow)
        if holding:
            return closes[-1] < average, "Hold below the rolling mean" if closes[
                -1
            ] < average else "Exit at the rolling mean"
        enter = deviation > 0 and closes[-1] <= average - component.entry_z * deviation
        return enter, "Close below the lower statistical band" if enter else "No lower-band entry"
    previous = bars[index - slow : index]
    activity = sum(float(b["volume"]) for b in previous) / slow
    if holding:
        floor = min(float(b["low"]) for b in previous[-fast:])
        return closes[-1] >= floor, "Hold above exit channel" if closes[
            -1
        ] >= floor else "Exit below the prior channel"
    enter = (
        closes[-1] > max(float(b["high"]) for b in previous)
        and activity > 0
        and float(bars[index]["volume"]) / activity >= float(component.activity_ratio)
    )
    return (
        enter,
        "Closed-bar breakout with provider activity filter"
        if enter
        else "Breakout conditions did not pass",
    )


def plan(bars, definition, context_bars=None, *, start_index=0):
    strategy = ResearchStrategy.model_validate(definition)
    context_bars = context_bars or []
    context_times = [stamp(b["close_time"]) for b in context_bars]
    positions = [False] * len(strategy.components)
    result = {}
    for index, bar in enumerate(bars):
        if index < start_index:
            result[index] = {"exposure": 0.0, "components": [], "as_of": bar["close_time"]}
            continue
        context_trend, context_ready = False, not strategy.context_interval
        if strategy.context_interval:
            at = bisect_right(context_times, stamp(bar["close_time"]))
            window = strategy.context_window
            if at >= window:
                prices = [float(b["close"]) for b in context_bars[at - window : at]]
                context_trend = prices[-1] > sum(prices) / window
                context_ready = True
        details, exposure = [], 0.0
        for number, component in enumerate(strategy.components):
            active, reason = component_signal(bars, index, component, positions[number])
            if strategy.context_interval:
                if strategy.mode == "regime_switch":
                    permitted = (
                        context_trend if component.family != "mean_reversion" else not context_trend
                    )
                else:
                    permitted = (
                        not context_trend if component.family == "mean_reversion" else context_trend
                    )
                active = active and context_ready and permitted
            positions[number] = active
            weight = float(component.weight) if strategy.mode != "regime_switch" else 1.0
            if active:
                exposure += weight
            details.append(
                {"family": component.family, "active": active, "weight": weight, "reason": reason}
            )
        # A switching policy chooses one active family, never leverages overlapping candidates.
        result[index] = {
            "exposure": min(exposure, 1.0),
            "components": details,
            "context_ready": context_ready,
            "context_trend": context_trend,
            "as_of": bar["close_time"],
        }
    return result


def daily_returns(curve):
    closes = {}
    for point in curve:
        closes[stamp(point["time"]).date().isoformat()] = float(point["equity"])
    values = [closes[day] for day in sorted(closes)]
    return [b / a - 1 for a, b in zip(values, values[1:], strict=False) if a > 0]


def sharpe(values):
    if len(values) < 2:
        return None
    average = sum(values) / len(values)
    deviation = math.sqrt(sum((v - average) ** 2 for v in values) / (len(values) - 1))
    return average / deviation if deviation else None


def selection_diagnostic(values, trial_sharpes):
    """Daily unannualized DSR diagnostic; dependent returns/trials limit its interpretation."""
    samples = len(values)
    sr = sharpe(values)
    others = [x for x in trial_sharpes if x is not None and math.isfinite(x)]
    if samples < 30 or len(others) < 2 or sr is None:
        return {
            "status": "insufficient",
            "daily_observations": samples,
            "recorded_trials": len(others),
            "probability": None,
        }
    average = sum(values) / samples
    variance = sum((v - average) ** 2 for v in values) / samples
    if variance <= 0:
        return {"status": "insufficient", "probability": None}
    skew = sum((v - average) ** 3 for v in values) / samples / variance**1.5
    kurtosis = sum((v - average) ** 4 for v in values) / samples / variance**2
    n = len(others)
    dispersion = math.sqrt(sum((x - sum(others) / n) ** 2 for x in others) / (n - 1))
    normal, gamma = NormalDist(), 0.5772156649015329
    reference = dispersion * (
        (1 - gamma) * normal.inv_cdf(1 - 1 / n) + gamma * normal.inv_cdf(1 - 1 / (n * math.e))
    )
    denominator = 1 - skew * sr + (kurtosis - 1) * sr * sr / 4
    probability = (
        normal.cdf((sr - reference) * math.sqrt(samples - 1) / math.sqrt(denominator))
        if denominator > 0
        else None
    )
    return {
        "status": "diagnostic",
        "probability": probability,
        "daily_observations": samples,
        "recorded_trials": n,
        "daily_sharpe": sr,
        "limitation": "Selection diagnostic only; trial dependence, serial dependence and incomplete search history can invalidate confidence. No trading qualification.",
    }
