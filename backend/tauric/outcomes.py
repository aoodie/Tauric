"""Exact outcome calculations shared by retrospective research and paper analytics."""

from decimal import Decimal, localcontext

from .paper import decimal_text
from .research_contracts import OutcomeMetrics

D = Decimal
ZERO = D("0")


def metrics(trades, fees):
    with localcontext() as ctx:
        ctx.prec = 120
        pnl = [D(t["net_pnl"]) for t in trades]
        gains = sum((x for x in pnl if x > 0), ZERO)
        losses = -sum((x for x in pnl if x < 0), ZERO)
        count = len(pnl)
        return OutcomeMetrics(
            closed_trades=count,
            wins=sum(x > 0 for x in pnl),
            losses=sum(x < 0 for x in pnl),
            breakeven=sum(x == 0 for x in pnl),
            net_pnl=decimal_text(sum(pnl, ZERO)),
            total_fees=decimal_text(fees),
            win_rate_percent=decimal_text(D(sum(x > 0 for x in pnl)) * 100 / count)
            if count
            else None,
            average_trade_pnl=decimal_text(sum(pnl, ZERO) / count) if count else None,
            profit_factor=decimal_text(gains / losses) if losses else None,
            average_holding_seconds=decimal_text(
                sum((D(str(t["holding_seconds"])) for t in trades), ZERO) / count
            )
            if count
            else None,
        )


def drawdown(points):
    """Drawdown uses the running peak, including starting capital."""
    peak = ZERO
    max_amount = max_percent = ZERO
    result = []
    with localcontext() as ctx:
        ctx.prec = 120
        for stamp, equity in points:
            peak = max(peak, equity)
            amount = peak - equity
            percent = amount / peak * 100 if peak else ZERO
            max_amount = max(max_amount, amount)
            max_percent = max(max_percent, percent)
            result.append(
                {
                    "time": stamp,
                    "equity": decimal_text(equity),
                    "drawdown_percent": decimal_text(percent),
                }
            )
    return result, decimal_text(max_amount), decimal_text(max_percent)
