"""Bounded long-only spot replay, closed-bar signals and next-open execution.

No model, broker, account, deployment or strategy mutation is reachable here.
OHLC stop time is unknown: intrabar stops are stamped at that bar's close.
"""

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal, localcontext
from types import SimpleNamespace

from . import strategy_rules
from .asset_costs import commission, execution_price, rollover_times, stop_trigger_price
from .outcomes import drawdown, metrics
from .paper import decimal_text
from .research_contracts import ReplayResult, ResearchBar

ENGINE_VERSION = "sma_cross_spot_v1.0.2"
BREAKOUT_ENGINE_VERSION = "closed_bar_breakout_spot_v1.0.1"
ASSET_ENGINE_VERSION = "sma_cross_assets_v1.0.1"
PROTECTED_ENGINE_VERSION = "broker_protected_ohlc_long_v1.0.0"
D = Decimal
ZERO = D("0")


def simulate(candles, params, strategy=None, *, data_context=None):
    if (params.engine == "closed_bar_breakout_v1") != (strategy is not None):
        raise ValueError("Breakout replay requires a pinned executable strategy version.")
    if strategy and (
        not strategy.parameters.rules or str(params.strategy_version_id) != strategy.id
    ):
        raise ValueError("The pinned strategy must match the selected executable version.")
    if params.broker_protection and params.asset_costs.asset_type == "fx" and (
        not data_context
        or data_context.get("price_side") != "bid_ask"
        or D(params.asset_costs.spread_bps) != 0
        or any(not bar.get("bid") or not bar.get("ask") for bar in candles)
    ):
        raise ValueError("Protected Forex replay requires historical bid/ask and zero added spread.")
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
    protection = p.broker_protection
    latencies, ambiguous_exits = [], []
    expired_entries = unfilled_entries = same_bar_exits = 0
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

    def exit_position(observed, stamp, cause, source_bar=None, field="open", *, limit=None):
        nonlocal cash, position
        price = execution_price(observed, "sell", source_bar, field, costs, slip)
        if limit is not None:
            price = max(limit, price)
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

    def bid(bar, field):
        if costs.asset_type == "fx":
            return D(bar["bid"][field])
        return D(bar[field]) * (1 - D(costs.spread_bps) / 20000)

    def protected_exit(bar, index, *, at_open):
        nonlocal same_bar_exits
        stop, target = D(position["stop_price"]), D(position["target_price"])
        stop_touched = stop_trigger_price(bar, "open" if at_open else "low", costs) <= stop
        target_touched = bid(bar, "open" if at_open else "high") >= target
        if not stop_touched and not target_touched:
            return
        if position["entry_bar_index"] == index:
            same_bar_exits += 1
        if not at_open and stop_touched and target_touched:
            ambiguous_exits.append(
                {
                    "bar_index": index,
                    "open_time": bar["open_time"],
                    "close_time": bar["close_time"],
                    "stop_price": decimal_text(stop),
                    "target_price": decimal_text(target),
                    "selected_exit": "stop",
                }
            )
        if stop_touched:
            exit_position(
                D(bar["open"]) if at_open else stop,
                bar["open_time"] if at_open else bar["close_time"],
                "protected_stop_gap" if at_open else "protected_stop_intrabar_time_assumed",
                bar,
                "open" if at_open else "stop",
            )
        else:
            # A take-profit is a limit order. Costs must never manufacture a
            # pre-fee fill worse than its limit, including after an opening gap.
            exit_position(
                D(bar["open"]) if at_open else target,
                bar["open_time"] if at_open else bar["close_time"],
                "protected_target_gap" if at_open else "protected_target_intrabar_time_assumed",
                bar if at_open else None,
                "open" if at_open else "stop",
                limit=target,
            )

    for i, bar in enumerate(candles):
        opening = D(bar["open"])
        prior_mark = D(candles[i - 1]["close"]) if i else opening
        finance_until(bar["open_time"], prior_mark)
        if protection and position:
            protected_exit(bar, i, at_open=True)
        # Gap stops take precedence over a prior close's queued crossover exit.
        if (
            not rules
            and position
            and stop_trigger_price(bar, "open", costs) <= D(position["stop_price"])
        ):
            exit_position(opening, bar["open_time"], "stop_gap", bar)
        waiting = False
        if protection and pending and pending["side"] == "buy":
            at = datetime.fromisoformat(bar["open_time"])
            if at >= datetime.fromisoformat(pending["expires_at"]):
                pending["execution"] = "expired_before_entry"
                expired_entries += 1
                pending = None
            elif at < datetime.fromisoformat(pending["available_at"]):
                waiting = True
        if pending and not waiting:
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
                    if stop < stop_trigger_price(bar, "open", costs)
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
                        **(
                            {
                                "target_price": pending["target_price"],
                                "reference_ask": pending["reference_ask"],
                                "entry_bar_index": i,
                                "cause": "protected_breakout_observed_open",
                            }
                            if protection
                            else {}
                        ),
                    }
                    fills.append(position)
                    if protection:
                        signal_at = datetime.fromisoformat(pending["time"])
                        filled_at = datetime.fromisoformat(bar["open_time"])
                        latencies.append(
                            D(str((filled_at - signal_at).total_seconds()))
                        )
                        pending["execution"] = "filled_at_observed_open"
                        pending["filled_at"] = bar["open_time"]
                else:
                    skipped += 1
                    if protection:
                        pending["execution"] = "skipped_opening_stop_or_budget"
            pending = None
        if protection and position and position["entry_bar_index"] == i:
            protected_exit(bar, i, at_open=True)
        # A bar's close is unknown at an interior rollover, so retain the prior
        # observed mark. Closed-bar funding precedes any same-timestamp exit.
        finance_until(bar["close_time"], prior_mark)
        if protection and position:
            protected_exit(bar, i, at_open=False)
        if (
            not rules
            and position
            and stop_trigger_price(bar, "low", costs) <= D(position["stop_price"])
        ):
            exit_position(
                D(position["stop_price"]),
                bar["close_time"],
                "stop_intrabar_close_time_assumed",
                bar,
                "stop",
            )
        closes.append(D(bar["close"]))
        if rules and not (protection and pending):
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
            if protection and position:
                # Broker protection handles stop/target touches. The only
                # strategy-managed exit is the original closed-bar channel.
                channel = min(
                    stop_trigger_price(previous, "low", costs)
                    for previous in candles[i - rules.exit_lookback : i]
                )
                exit_channel = stop_trigger_price(bar, "close", costs) < channel
                evaluation = {
                    "action": "exit" if exit_channel else "hold",
                    "reasons": ["Closed-bar channel exit." if exit_channel else "No channel exit."],
                    "exit_trigger": "channel",
                    "exit_channel": decimal_text(channel),
                    "bar_close_time": bar["close_time"],
                }
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
                if protection and side == "buy":
                    tick = D(protection.price_step)
                    stop = (
                        (D(signal["stop_price"]) / tick).to_integral_value(rounding=ROUND_FLOOR)
                        * tick
                    )
                    ask = (
                        D(bar["ask"]["close"])
                        if costs.asset_type == "fx"
                        else D(bar["close"]) * (1 + D(costs.spread_bps) / 20000)
                    )
                    if not ZERO < stop < ask:
                        raise ValueError(
                            "The rounded protective stop must be positive and below the reference ask."
                        )
                    target = (
                        ((ask + (ask - stop) * D(rules.take_profit_r)) / tick)
                        .to_integral_value(rounding=ROUND_CEILING)
                        * tick
                    )
                    stamp = datetime.fromisoformat(signal["time"])
                    signal.update(
                        stop_price=decimal_text(stop),
                        target_price=decimal_text(target),
                        reference_ask=decimal_text(ask),
                        available_at=(stamp + timedelta(seconds=protection.entry_delay_seconds)).isoformat(),
                        expires_at=(stamp + timedelta(seconds=rules.signal_ttl_seconds)).isoformat(),
                        execution="awaiting_observed_open" if i + 1 < len(candles) else "no_next_bar",
                    )
                    if i + 1 == len(candles):
                        unfilled_entries += 1
                if i + 1 < len(candles):
                    pending = signal
        elif not rules and len(closes) > p.slow_window:
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
        mark = stop_trigger_price(bar, "close", costs)
        equity = cash + (D(position["quantity"]) * mark if position else ZERO)
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
        execution_evidence={
            "model": "broker_protected_ohlc_long_v1",
            "entry_delay_seconds": protection.entry_delay_seconds,
            "signal_to_fill": timing_summary(latencies),
            "expired_entries": expired_entries,
            "unfilled_entries": unfilled_entries + int(bool(pending and pending["side"] == "buy")),
            "same_bar_exits": same_bar_exits,
            "ambiguous_exits": ambiguous_exits,
        }
        if protection
        else None,
    )


def timing_summary(seconds):
    """Median and nearest-rank p95 of observed replay delays, in exact seconds."""
    values = sorted(seconds)
    n = len(values)
    return {
        "observations": n,
        "median_seconds": decimal_text((values[(n - 1) // 2] + values[n // 2]) / 2) if n else None,
        "p95_seconds": decimal_text(values[(95 * n + 99) // 100 - 1]) if n else None,
        "maximum_seconds": decimal_text(values[-1]) if n else None,
    }
