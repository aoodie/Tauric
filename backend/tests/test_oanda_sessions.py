"""Published session examples, DST boundaries and holiday interruption checks."""

from datetime import datetime, timedelta

import pytest

from tauric.market_calendars import default_context, oanda_session_open, periods
from tauric.market_contracts import DataContext
from tauric.oanda_sessions import RULES


def dt(value):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize(
    "symbol,stamp,opened",
    [
        ("XAU_GBP", "2026-10-25T22:04:00+00:00", False),
        ("XAU_GBP", "2026-10-25T22:05:00+00:00", True),
        ("HK33_HKD", "2026-10-20T03:58:00+00:00", True),
        ("HK33_HKD", "2026-10-20T04:00:00+00:00", False),
        ("HK33_HKD", "2026-10-20T05:01:00+00:00", True),
        ("HK33_HKD", "2026-10-19T05:01:00+00:00", False),
        ("DE30_EUR", "2026-10-23T00:15:00+00:00", False),
        ("DE30_EUR", "2026-10-23T00:16:00+00:00", True),
        ("DE30_EUR", "2026-10-26T00:16:00+00:00", True),
        ("AU200_AUD", "2026-10-26T20:58:00+00:00", True),
        ("AU200_AUD", "2026-10-26T21:00:00+00:00", False),
        ("CORN_USD", "2026-10-26T12:44:00+00:00", False),
        ("CORN_USD", "2026-10-26T13:31:00+00:00", True),
        ("SPX500_USD", "2026-09-07T16:58:00+00:00", True),
        ("SPX500_USD", "2026-09-07T17:00:00+00:00", False),
        ("SPX500_USD", "2026-09-07T22:02:00+00:00", True),
        ("XAU_USD", "2026-04-03T12:00:00+00:00", False),
        ("SPX500_USD", "2026-04-03T13:13:00+00:00", True),
        ("SPX500_USD", "2026-04-03T13:15:00+00:00", False),
        ("XAU_USD", "2026-04-05T22:05:00+00:00", True),
    ],
)
def test_product_sessions_match_published_boundaries(symbol, stamp, opened):
    assert (
        oanda_session_open(symbol, "METAL" if symbol.startswith("XA") else "CFD", dt(stamp))
        is opened
    )


@pytest.mark.parametrize(
    "symbol,stamp,opened",
    [
        ("NZD_USD", "2026-10-26T17:59:00+00:00", False),
        ("NZD_USD", "2026-10-26T18:05:00+00:00", True),
        ("EUR_USD", "2026-10-26T17:59:00+00:00", True),
        ("EUR_USD", "2026-01-01T12:00:00+00:00", False),
        ("EUR_USD", "2026-01-01T22:05:00+00:00", True),
    ],
)
def test_fx_special_sessions_and_new_year_closure(symbol, stamp, opened):
    assert oanda_session_open(symbol, "CURRENCY", dt(stamp)) is opened


def test_asian_multiple_sessions_preserve_alignment_without_duplicate_bars():
    context = DataContext(
        calendar_id="oanda_product",
        provider_symbol="HK33_HKD",
        timezone="UTC",
        price_side="bid_ask",
        volume_type="provider_ticks",
    )
    start, end = dt("2026-10-20T00:00:00+00:00"), dt("2026-10-20T06:00:00+00:00")
    bars = list(periods(context, "4h", start, end))
    assert [opening.hour for opening, _ in bars] == [1, 5]
    assert all(closing - opening == timedelta(hours=4) for opening, closing in bars)
    daily = list(periods(context, "1d", start - timedelta(days=2), end + timedelta(days=1)))
    assert len(daily) == len({opening for opening, _ in daily})


def test_unknown_or_conflicting_broker_hours_never_become_a_closed_session():
    # The broker's Brent table and footnote give conflicting timezone/hour rules.
    assert "BCO_USD" not in RULES
    assert oanda_session_open("BCO_USD", "CFD", dt("2026-10-26T12:00:00+00:00")) is None
    from types import SimpleNamespace

    context = default_context(SimpleNamespace(asset_type="cfd", provider_symbol="BCO_USD"))
    assert context.calendar_id == "provider_observed"
    assert oanda_session_open("USD_TRY", "CURRENCY", dt("2026-10-26T12:00:00+00:00")) is None
