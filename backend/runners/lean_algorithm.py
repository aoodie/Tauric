"""Fixed LEAN algorithm: native Forex/equity orders from causal frozen test signals."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from AlgorithmImports import (
    CashAmount,
    ConstantSlippageModel,
    DataNormalizationMode,
    FeeModel,
    Market,
    OrderFee,
    OrderStatus,
    QCAlgorithm,
    Resolution,
    TimeZones,
)


class ResearchFees(FeeModel):  # noqa: F405
    def __init__(self, request, currency):
        self.request, self.currency = request, currency

    def get_order_fee(self, parameters):
        qty = abs(float(parameters.order.quantity))
        security = parameters.security
        price = float(
            (security.ask_price if parameters.order.quantity > 0 else security.bid_price)
            or security.price
        )
        adjustment = (
            float(self.request["slippage_bps"]) / 10000
            + float(self.request["asset_costs"]["spread_bps"]) / 20000
        )
        price *= 1 + adjustment if parameters.order.quantity > 0 else 1 - adjustment
        costs = self.request["asset_costs"]
        fee = max(
            qty * price * float(self.request["fee_bps"]) / 10000
            + qty * float(costs["commission_per_unit"]),
            float(costs["minimum_commission"]),
        )
        if parameters.order.quantity < 0:
            fee += qty * price * float(costs["sell_tax_bps"]) / 10000
        return OrderFee(CashAmount(fee, self.currency))  # noqa: F405


class TauricResearch(QCAlgorithm):  # noqa: F405
    def initialize(self):
        self.job = json.loads(Path("/job/input.json").read_text())
        self.request = self.job["request"]
        spec = self.job["instrument"]
        self.currency = spec["quote_currency"]
        self.set_account_currency(self.currency)
        self.set_cash(float(self.request["starting_balance"]))
        self.set_time_zone(TimeZones.UTC)  # noqa: F405
        first = datetime.fromisoformat(self.job["candles"][0]["open_time"])
        last = datetime.fromisoformat(self.job["candles"][-1]["close_time"])
        self.set_start_date(first.year, first.month, first.day)
        self.set_end_date(last.year, last.month, last.day)
        fx = spec["asset_type"] == "fx"
        security = (
            self.add_forex(self.job["ticker"], Resolution.TICK, Market.OANDA, fill_forward=False)
            if fx
            else self.add_equity(
                self.job["ticker"], Resolution.TICK, Market.USA, fill_forward=False
            )
        )  # noqa: F405
        if not fx:
            security.set_data_normalization_mode(DataNormalizationMode.RAW)  # noqa: F405
        security.set_fee_model(ResearchFees(self.request, self.currency))
        security.set_slippage_model(
            ConstantSlippageModel(
                float(self.request["slippage_bps"]) / 10000
                + float(self.request["asset_costs"]["spread_bps"]) / 20000
            )
        )  # noqa: F405
        self.symbol = security.symbol
        security.set_leverage(1)
        self.set_benchmark(self.symbol)
        self.fills, self.curve, self.financing = [], [], 0.0
        self.previous = None
        self.previous_notional = 0.0
        self.last_tick = datetime.fromisoformat(self.job["last_tick"]).astimezone(UTC)

    def on_data(self, data):
        if self.symbol not in data.ticks:
            return
        instant = self.utc_time.replace(tzinfo=UTC)
        self.rollover(instant)
        key = instant.replace(tzinfo=UTC).isoformat()
        target = self.job["plan"].get(key)
        holdings = float(self.portfolio[self.symbol].quantity)
        if instant >= self.last_tick:
            if holdings > 0:
                self.market_order(self.symbol, -holdings, tag="test end")
        elif target is True and holdings == 0:
            security = self.securities[self.symbol]
            price = float(security.ask_price or security.price)
            quantity = int(
                float(self.portfolio.total_portfolio_value)
                * float(self.request["allocation_percent"])
                / 100
                / price
            )
            if quantity > 0:
                self.market_order(self.symbol, quantity, tag="previous closed-bar signal")
        elif target is False and holdings > 0:
            self.market_order(self.symbol, -holdings, tag="previous closed-bar exit")
        self.curve.append({"time": key, "equity": float(self.portfolio.total_portfolio_value)})
        self.previous = instant
        self.previous_notional = abs(float(self.portfolio[self.symbol].holdings_value))

    def rollover(self, instant):
        costs = self.request["asset_costs"]
        if costs["asset_type"] != "fx" or self.previous is None:
            return
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("America/New_York")
        previous = self.previous.replace(tzinfo=UTC)
        end = instant.replace(tzinfo=UTC)
        day = previous.astimezone(zone).date()
        while day <= end.astimezone(zone).date():
            at = datetime(day.year, day.month, day.day, 17, tzinfo=zone).astimezone(UTC)
            if day.weekday() < 5 and previous < at <= end:
                multiplier = 3 if day.weekday() == costs["triple_rollover_weekday"] else 1
                debit = (
                    self.previous_notional
                    * float(costs["financing_daily_bps"])
                    / 10000
                    * multiplier
                )
                self.portfolio.cash_book[self.currency].add_amount(-debit)
                self.financing += debit
            day += timedelta(days=1)

    def on_order_event(self, event):
        if event.status == OrderStatus.FILLED:  # noqa: F405
            self.fills.append(
                {
                    "time": self.utc_time.replace(tzinfo=UTC).isoformat(),
                    "quantity": float(event.fill_quantity),
                    "price": float(event.fill_price),
                    "fee": float(event.order_fee.value.amount),
                }
            )

    def on_end_of_algorithm(self):
        if not self.curve:
            raise ValueError("LEAN processed no imported observations")
        peak, drawdown = float(self.request["starting_balance"]), 0.0
        for point in self.curve:
            peak = max(peak, point["equity"])
            drawdown = max(drawdown, 1 - point["equity"] / peak)
        final = float(self.portfolio.total_portfolio_value)
        result = {
            "engine": "lean",
            "currency": self.currency,
            "final_equity": final,
            "net_pnl": final - float(self.request["starting_balance"]),
            "return_percent": (final / float(self.request["starting_balance"]) - 1) * 100,
            "max_drawdown_percent": drawdown * 100,
            "fees": sum(f["fee"] for f in self.fills),
            "financing": self.financing,
            "fills": self.fills,
            "equity_curve": self.curve,
            "authority": "research_only",
            "warnings": [
                "Long/flat, integer-unit execution in the instrument quote currency; no leverage.",
                "Orders use native LEAN fills on observed next-open bid/ask quotes or stock trades.",
                "Open/close snapshots do not reconstruct intrabar prices, liquidity, stops or limit fills.",
                "Imported calendar is authoritative; LEAN exchange-hours filtering is disabled.",
                "No further split/dividend adjustments are applied; stock dividends are excluded.",
                "Constant slippage and daily financing are explicit assumptions, not measured broker costs.",
                "Rollover within observation gaps uses the last observed holding notional.",
            ],
        }
        Path("/job/result.json").write_text(json.dumps(result, allow_nan=False))
