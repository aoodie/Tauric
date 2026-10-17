from decimal import Decimal
from types import SimpleNamespace as Object

import pytest

from tauric import broker_risk
from tauric.broker_adapters import BrokerError


def facts(provider="alpaca"):
    account = Object(
        provider=provider,
        currency="USD" if provider == "alpaca" else "GBP",
        equity="10000",
        buying_power="10000",
    )
    instrument = Object(
        tradable=True,
        quantity_step="1",
        minimum_quantity="1",
        maximum_quantity=None,
        margin_rate="0.02",
    )
    quote = Object(
        symbol="AAPL" if provider == "alpaca" else "EUR_USD",
        ask="12.01" if provider == "alpaca" else "1.11015",
        quote_to_home_negative="0.76",
        quote_to_home_position="0.75",
    )
    return account, instrument, quote


def test_stock_risk_rounded_down_to_whole_shares():
    account, instrument, quote = facts()
    quantity, evidence = broker_risk.calculate(account, instrument, quote, "9", "1")
    assert quantity == "33"
    assert evidence["risk_budget_home"] == "100"
    assert evidence["estimated_stop_loss_home"] == "99.33"


def test_fx_loss_and_position_value_conversions_are_distinct():
    account, instrument, quote = facts("oanda")
    quantity, evidence = broker_risk.calculate(account, instrument, quote, "1.0899", "1")
    assert quantity == "6497"
    assert Decimal(evidence["estimated_stop_loss_home"]) <= 100
    assert evidence["quote_to_home_loss"] == "0.76"
    assert evidence["quote_to_home_position"] == "0.75"
    account.buying_power = "10"
    quantity, _ = broker_risk.calculate(account, instrument, quote, "1.0899", "1")
    assert quantity == "600"


def test_broker_buying_power_and_maximum_order_are_authoritative():
    account, instrument, quote = facts()
    account.buying_power = "40"
    assert broker_risk.calculate(account, instrument, quote, "9", "1")[0] == "3"
    account.buying_power, instrument.maximum_quantity = "10000", "2"
    assert broker_risk.calculate(account, instrument, quote, "9", "1")[0] == "2"


@pytest.mark.parametrize(
    "failure",
    [
        "equity",
        "buying_power",
        "conversion",
        "position_conversion",
        "margin",
        "stop",
        "risk",
        "minimum",
    ],
)
def test_missing_or_invalid_sizing_facts_never_create_quantity(failure):
    account, instrument, quote = facts("oanda")
    stop, percent = "1.0899", "1"
    if failure == "equity":
        account.equity = "NaN"
    elif failure == "buying_power":
        account.buying_power = "0"
    elif failure == "conversion":
        quote.quote_to_home_negative = None
    elif failure == "position_conversion":
        quote.quote_to_home_position = None
    elif failure == "margin":
        instrument.margin_rate = "0"
    elif failure == "stop":
        stop = "2"
    elif failure == "risk":
        percent = "101"
    else:
        instrument.minimum_quantity = "10000"
    with pytest.raises(BrokerError):
        broker_risk.calculate(account, instrument, quote, stop, percent)


@pytest.mark.parametrize("risk,accepted", [("5", True), ("4.99", True), ("5.01", False)])
def test_account_policy_caps_route_risk(risk, accepted):
    from fastapi import HTTPException

    policy = Object(payload={"maximum_risk_percent": "5"})
    if accepted:
        broker_risk.policy_risk(policy, risk)
    else:
        with pytest.raises(HTTPException, match="risk exceeds"):
            broker_risk.policy_risk(policy, risk)


@pytest.mark.parametrize(
    "observed,paused", [("14.99", False), ("15", True), ("16", True), (None, True), ("NaN", True)]
)
def test_account_drawdown_limit_is_fail_closed(observed, paused):
    from fastapi import HTTPException

    policy = Object(payload={"maximum_drawdown_percent": "15"})
    account = {"marked_max_drawdown_percent": observed}
    if paused:
        with pytest.raises(HTTPException):
            broker_risk.policy_drawdown(policy, account)
    else:
        broker_risk.policy_drawdown(policy, account)


@pytest.mark.parametrize(
    "changes,accepted",
    [
        (
            {
                "no_owner_limits": False,
                "maximum_risk_percent": "5",
                "maximum_drawdown_percent": "15",
            },
            True,
        ),
        ({"no_owner_limits": False, "maximum_risk_percent": "5"}, False),
        (
            {
                "no_owner_limits": True,
                "maximum_risk_percent": "5",
                "maximum_drawdown_percent": "15",
            },
            False,
        ),
        (
            {
                "no_owner_limits": False,
                "maximum_risk_percent": "101",
                "maximum_drawdown_percent": "15",
            },
            False,
        ),
    ],
)
def test_broker_policy_requires_explicit_consistent_limits(changes, accepted):
    from uuid import uuid4

    from pydantic import ValidationError

    from tauric.broker_contracts import BrokerPolicyChange

    values = {
        "request_id": uuid4(),
        "expected_revision": 0,
        "enabled": False,
        "reason": "Owner authorized limits",
        **changes,
    }
    if accepted:
        assert BrokerPolicyChange(**values).maximum_drawdown_percent == "15"
    else:
        with pytest.raises(ValidationError):
            BrokerPolicyChange(**values)
