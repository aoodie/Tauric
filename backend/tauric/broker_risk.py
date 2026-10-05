"""Estimate long-entry stop risk in the demo account currency, with exact decimals."""

from decimal import ROUND_FLOOR, Decimal, localcontext

from .broker_adapters import BrokerError


def positive(value, label):
    try:
        result = Decimal(value)
        if not result.is_finite() or result <= 0:
            raise ValueError()
        return result
    except (ValueError, TypeError, ArithmeticError):
        raise BrokerError(f"Positive {label} evidence is required for automatic sizing.") from None


def conversion(account, quote):
    if account.provider == "alpaca":
        if account.currency != "USD":
            raise BrokerError("Automatic stock sizing requires a USD paper account.")
        return Decimal(1)
    if quote.symbol[-3:] == account.currency:
        return Decimal(1)
    return positive(quote.quote_to_home_negative, "quote-to-account loss conversion")


def calculate(account, instrument, quote, stop, risk_percent):
    with localcontext() as precision:
        precision.prec = 120
        percent = positive(risk_percent, "risk percentage")
        if percent > 100:
            raise BrokerError("Risk percentage must be at most 100.")
        equity = positive(account.equity, "account equity")
        power = positive(account.buying_power, "broker buying power or available margin")
        ask, stop = positive(quote.ask, "ask"), positive(stop, "stop")
        if stop >= ask:
            raise BrokerError("The stop must be below the executable ask.")
        factor = conversion(account, quote)
        per_unit = (ask - stop) * factor
        budget = equity * percent / 100
        margin = (
            positive(instrument.margin_rate, "broker margin rate")
            if account.provider == "oanda"
            else Decimal(1)
        )
        value_factor = (
            positive(quote.quote_to_home_position, "quote-to-account position conversion")
            if account.provider == "oanda" and quote.symbol[-3:] != account.currency
            else Decimal(1)
        )
        capacity = power / (ask * value_factor * margin)
        step = positive(instrument.quantity_step, "quantity increment")
        candidates = [budget / per_unit, capacity]
        if instrument.maximum_quantity:
            candidates.append(positive(instrument.maximum_quantity, "broker maximum quantity"))
        quantity = (min(candidates) / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if not instrument.tradable or quantity < positive(
            instrument.minimum_quantity, "minimum quantity"
        ):
            raise BrokerError(
                "Available risk budget or broker buying power cannot support the minimum order."
            )
        evidence = {
            "method": "account_equity_stop_distance_v1",
            "risk_percent": str(risk_percent),
            "account_currency": account.currency,
            "account_equity": account.equity,
            "risk_budget_home": format(budget, "f"),
            "estimated_stop_loss_home": format(quantity * per_unit, "f"),
            "loss_per_unit_home": format(per_unit, "f"),
            "quote_to_home_loss": format(factor, "f"),
            "quote_to_home_position": format(value_factor, "f"),
            "broker_capacity_quantity": format(capacity, "f"),
            "quantity": format(quantity, "f"),
            "limitations": "An estimate at the observed ask and stop, not a guaranteed maximum loss. Gaps, slippage, fees and financing can increase losses. Broker buying power/margin and increments apply; no additional owner exposure or position caps.",
        }
        return evidence["quantity"], evidence


def validate_current(evidence, actual):
    sizing = evidence.get("automatic_sizing")
    if not sizing:
        return
    from .broker_contracts import BrokerAccountView, BrokerInstrumentView, BrokerQuote

    account = BrokerAccountView.model_validate(actual["account"])
    quote = BrokerQuote.model_validate(actual["quote"])
    instrument = BrokerInstrumentView.model_validate(actual["broker_instrument"])
    available, _ = calculate(
        account, instrument, quote, evidence["planned_order"]["stop_loss"], sizing["risk_percent"]
    )
    if Decimal(available) < Decimal(sizing["quantity"]):
        raise BrokerError(
            "Current risk budget or broker capacity no longer supports the assessed quantity; no resizing or resubmission is allowed."
        )
    with localcontext() as precision:
        precision.prec = 120
        budget = min(
            positive(sizing["risk_budget_home"], "captured risk budget"),
            positive(account.equity, "current equity")
            * positive(sizing["risk_percent"], "risk percentage")
            / 100,
        )
        risk = (
            (Decimal(quote.ask) - Decimal(evidence["planned_order"]["stop_loss"]))
            * conversion(account, quote)
            * Decimal(sizing["quantity"])
        )
        if risk <= 0 or risk > budget:
            raise BrokerError(
                "Current stop risk exceeds the assessed sizing budget; this signal is not resized or resubmitted."
            )
