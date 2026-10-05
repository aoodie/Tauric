"""Bounded long-only spot replay, closed-bar signals and next-open execution.

No model, broker, account, deployment or strategy mutation is reachable here.
OHLC stop time is unknown: intrabar stops are stamped at that bar's close.
"""

from datetime import datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from types import SimpleNamespace

from . import strategy_rules
from .asset_costs import commission, execution_price, rollover_times
from .outcomes import drawdown, metrics
from .paper import decimal_text
from .research_contracts import ReplayResult, ResearchBar

ENGINE_VERSION = "sma_cross_spot_v1.0.2"
BREAKOUT_ENGINE_VERSION = "closed_bar_breakout_spot_v1.0.1"
ASSET_ENGINE_VERSION = "sma_cross_assets_v1.0.0"
D = Decimal
ZERO = D("0")


def simulate(candles, params, strategy=None, *, data_context=None):
    if (params.engine == "closed_bar_breakout_v1") != (strategy is not None):
        raise ValueError("Breakout replay requires a pinned executable strategy version.")
    if strategy and (
        not strategy.parameters.rules or str(params.strategy_version_id) != strategy.id
    ):
        raise ValueError("The pinned strategy must match the selected executable version.")
    with localcontext() as ctx:
        ctx.prec = 120
        return _simulate(candles, params, strategy, data_context)


def _simulate(candles, p, strategy, data_context):
    cash = start = D(p.starting_balance)
    fee_rate, slip, step = D(p.fee_bps) / 10000, D(p.slippage_bps) / 10000, D(p.quantity_step)
    closes = []
    rules = strategy.parameters.rules if strategy else None
    closed_bars = [ResearchBar.model_validate(bar) for bar in candles] if rules else []
    if rules:
        step = D(strategy.parameters.quantity_step)
    fills, signals, trades = [], [], []
    position = pending = None
    costs = p.asset_costs
    financing_events = []
    finance_clock = datetime.fromisoformat(candles[0]["open_time"])
    skipped = 0
    points = [(candles[0]["open_time"], start)]

    def finance_until(stamp, mark):
        nonlocal cash, finance_clock
        ending = datetime.fromisoformat(stamp)
        if position:
            for at, days in rollover_times(finance_clock, ending, costs):
                charge = (
                    D(position["quantity"]) * mark * D(costs.financing_daily_bps) / 10000 * days
                )
                cash -= charge
                position["financing"] = decimal_text(D(position.get("financing", "0")) + charge)
                financing_events.append(
                    {
                        "time": at.isoformat(),
                        "days": days,
                        "mark": decimal_text(mark),
                        "amount": decimal_text(charge),
                        "entry_fill_id": position["id"],
                    }
                )
        finance_clock = ending

    def exit_position(observed, stamp, cause, source_bar=None, field="open"):
        nonlocal cash, position
        price = execution_price(observed, "sell", source_bar, field, costs, slip)
        quantity = D(position["quantity"])
        fee = commission(quantity, price, "sell", fee_rate, costs)
        cash += quantity * price - fee
        financing = D(position.get("financing", "0"))
        pnl = quantity * (price - D(position["price"])) - fee - D(position["fee"]) - financing
        fill = {
            "id": f"fill-{len(fills) + 1}",
            "side": "sell",
            "time": stamp,
            "price": decimal_text(price),
            "observed_price": decimal_text(observed),
            "quantity": position["quantity"],
            "fee": decimal_text(fee),
            "cause": cause,
            "entry_fill_id": position["id"],
        }
        fills.append(fill)
        trades.append(
            {
                "entry_fill_id": position["id"],
                "exit_fill_id": fill["id"],
                "entry_time": position["time"],
                "exit_time": stamp,
                "quantity": position["quantity"],
                "entry_price": position["price"],
                "exit_price": fill["price"],
                "fees": decimal_text(D(position["fee"]) + fee),
                "net_pnl": decimal_text(pnl),
                "financing": decimal_text(financing),
                "holding_seconds": (
                    datetime.fromisoformat(stamp) - datetime.fromisoformat(position["time"])
                ).total_seconds(),
                "exit_reason": cause,
            }
        )
        position = None

    for i, bar in enumerate(candles):
        opening = D(bar["open"])
        prior_mark = D(candles[i - 1]["close"]) if i else opening
        finance_until(bar["open_time"], prior_mark)
        # Gap stops take precedence over a prior close's queued crossover exit.
        if not rules and position and opening <= D(position["stop_price"]):
            exit_position(opening, bar["open_time"], "stop_gap", bar)
        if pending:
            if pending["side"] == "sell" and position:
                exit_position(
                    opening,
                    bar["open_time"],
                    f"{pending['exit_trigger']}_next_open" if rules else "cross_down_next_open",
                    bar,
                )
            elif pending["side"] == "buy" and not position:
                entry = execution_price(opening, "buy", bar, "open", costs, slip)
                stop = (
                    D(pending["stop_price"]) if rules else opening * (1 - D(p.stop_percent) / 100)
                )
                stop_fill = execution_price(stop, "sell", bar, "stop", costs, slip)
                risk_per_unit = entry - stop_fill + fee_rate * (entry + stop_fill)
                risk_budget = cash * D(p.risk_percent) / 100
                capital_budget = cash * D(p.allocation_percent) / 100
                if costs:
                    risk_per_unit += (
                        D(costs.commission_per_unit) * 2 + stop_fill * D(costs.sell_tax_bps) / 10000
                    )
                    risk_budget = max(ZERO, risk_budget - D(costs.minimum_commission) * 2)
                    capital_budget = max(ZERO, capital_budget - D(costs.minimum_commission))
                if rules:
                    risk_budget = min(
                        risk_budget,
                        D(strategy.parameters.capital_limit)
                        * D(strategy.parameters.risk_percent)
                        / 100,
                    )
                    capital_budget = min(capital_budget, D(strategy.parameters.capital_limit))
                quantity = (
                    (
                        min(
                            risk_budget / risk_per_unit,
                            capital_budget
                            / (
                                entry * (1 + fee_rate)
                                + (D(costs.commission_per_unit) if costs else ZERO)
                            ),
                        )
                        / step
                    ).to_integral_value(rounding=ROUND_DOWN)
                    * step
                    if stop < opening
                    else ZERO
                )
                if quantity > 0:
                    fee = commission(quantity, entry, "buy", fee_rate, costs)
                    cash -= quantity * entry + fee
                    position = {
                        "id": f"fill-{len(fills) + 1}",
                        "side": "buy",
                        "time": bar["open_time"],
                        "signal_time": pending["time"],
                        "signal_bar_index": pending["bar_index"],
                        "quantity": decimal_text(quantity),
                        "price": decimal_text(entry),
                        "observed_price": bar["open"],
                        "fee": decimal_text(fee),
                        "stop_price": decimal_text(stop),
                        "cause": "breakout_next_open" if rules else "cross_up_next_open",
                    }
                    fills.append(position)
                else:
                    skipped += 1
            pending = None
        # A bar's close is unknown at an interior rollover, so retain the prior
        # observed mark. Closed-bar funding precedes any same-timestamp exit.
        finance_until(bar["close_time"], prior_mark)
        if not rules and position and D(bar["low"]) <= D(position["stop_price"]):
            exit_position(
                D(position["stop_price"]),
                bar["close_time"],
                "stop_intrabar_close_time_assumed",
                bar,
                "stop",
            )
        closes.append(D(bar["close"]))
        if rules:
            held = (
                SimpleNamespace(
                    id=position["id"],
                    payload=position,
                    created_at=datetime.fromisoformat(position["time"]),
                )
                if position
                else None
            )
            evaluation = strategy_rules.evaluate_closed_bars(
                closed_bars[: i + 1], rules, held, data_context=data_context
            )
            side = {"enter": "buy", "exit": "sell"}.get(evaluation["action"])
            if side:
                signal = {
                    "side": side,
                    "time": bar["close_time"],
                    "bar_index": i,
                    "execution": "next_bar_open" if i + 1 < len(candles) else "no_next_bar",
                    "evidence": evaluation,
                    **(
                        {"stop_price": evaluation["stop_price"]}
                        if side == "buy"
                        else {"exit_trigger": evaluation["exit_trigger"]}
                    ),
                }
                signals.append(signal)
                if i + 1 < len(candles):
                    pending = signal
        elif len(closes) > p.slow_window:
            # Compare exact sums instead of rounded averages; only this prefix is visible.
            current = (
                sum(closes[-p.fast_window :], ZERO) * p.slow_window
                - sum(closes[-p.slow_window :], ZERO) * p.fast_window
            )
            prior = (
                sum(closes[-p.fast_window - 1 : -1], ZERO) * p.slow_window
                - sum(closes[-p.slow_window - 1 : -1], ZERO) * p.fast_window
            )
            side = (
                "buy"
                if prior <= 0 < current and not position
                else "sell"
                if prior >= 0 > current and position
                else None
            )
            if side:
                signal = {
                    "side": side,
                    "time": bar["close_time"],
                    "bar_index": i,
                    "execution": "next_bar_open" if i + 1 < len(candles) else "no_next_bar",
                }
                signals.append(signal)
                if i + 1 < len(candles):
                    pending = signal
        if i == len(candles) - 1 and position:
            exit_position(D(bar["close"]), bar["close_time"], "end_of_dataset_close", bar, "close")
        equity = cash + (D(position["quantity"]) * D(bar["close"]) if position else ZERO)
        if cash < 0 or equity < 0:
            raise ValueError("Replay balances failed to reconcile.")
        points.append((bar["close_time"], equity))
    fees = sum((D(f["fee"]) for f in fills), ZERO)
    outcome = metrics(trades, fees)
    if cash != start + D(outcome.net_pnl):
        raise ValueError("Replay cash and closed outcomes failed to reconcile.")
    curve, max_amount, max_percent = drawdown(points)
    return ReplayResult(
        metrics=outcome,
        final_equity=decimal_text(cash),
        return_percent=decimal_text((cash / start - 1) * 100),
        max_drawdown_percent=max_percent,
        max_drawdown_amount=max_amount,
        equity=curve,
        trades=trades,
        fills=fills,
        signals=signals,
        skipped_entries=skipped,
        bars=len(candles),
        financing_cost=decimal_text(sum((D(e["amount"]) for e in financing_events), ZERO)),
        financing_events=financing_events,
    )
