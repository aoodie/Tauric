"""OANDA UK published regular sessions and dated holiday exceptions.

Sources checked 25 October 2026:
https://www.oanda.com/uk-en/trading/hours-of-operation/
https://www.oanda.com/uk-en/trading/holiday-trading-hours/
Holiday times use the publisher's ET convention. Unknown dates are not inferred.
"""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

QUOTES = {"USD", "AUD", "CAD", "CHF", "EUR", "GBP", "HKD", "JPY", "NZD", "SGD"}
METALS = {f"{metal}_{quote}" for metal in ("XAU", "XAG") for quote in QUOTES} | {"XAU_XAG"}
INDICES = {"SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD"}
BONDS = {"USB02Y_USD", "USB05Y_USD", "USB10Y_USD", "USB30Y_USD"}
ENERGY_METALS = METALS | {"WTICO_USD", "NATGAS_USD", "XCU_USD", "XPT_USD", "XPD_USD"}
AGRICULTURE = {"CORN_USD", "SOYBN_USD", "WHEAT_USD"}
EUROPE = {"EU50_EUR", "DE30_EUR", "FR40_EUR", "NL25_EUR", "ESPIX_EUR", "CH20_CHF", "UK100_GBP"}
HONG_KONG = {"HK33_HKD", "CHINAH_HKD"}

# Each segment is (opening day offset, time, closing day offset, time).
OVERNIGHT_US = ((-1, "18:01", 0, "16:59"),)
RULES = {
    **{
        symbol: ("America/New_York", OVERNIGHT_US)
        for symbol in INDICES | BONDS | (ENERGY_METALS - METALS) | {"JP225_USD", "JP225Y_JPY"}
    },
    **{symbol: ("America/New_York", ((-1, "18:05", 0, "16:59"),)) for symbol in METALS},
    **{
        symbol: ("America/Chicago", ((-1, "19:01", 0, "07:44"), (0, "08:31", 0, "13:19")))
        for symbol in AGRICULTURE
    },
    "SUGAR_USD": ("America/New_York", ((0, "03:31", 0, "12:59"),)),
    "AU200_AUD": ("Australia/Sydney", ((0, "09:51", 0, "16:29"), (0, "17:11", 1, "06:59"))),
    "CN50_USD": ("Asia/Singapore", ((0, "09:01", 0, "16:29"), (0, "17:01", 1, "04:44"))),
    "SG30_SGD": ("Asia/Singapore", ((0, "08:31", 0, "17:19"), (0, "17:51", 1, "05:14"))),
    **{
        symbol: (
            "Asia/Hong_Kong",
            ((0, "09:16", 0, "11:59"), (0, "13:01", 0, "16:29"), (0, "17:16", 1, "02:59")),
        )
        for symbol in HONG_KONG
    },
    **{
        symbol: ("Europe/Berlin", ((0, "01:16", 0, "21:59"),))
        for symbol in {"EU50_EUR", "DE30_EUR", "DE10YB_EUR"}
    },
    "FR40_EUR": ("Europe/Paris", ((0, "08:01", 0, "21:59"),)),
    "NL25_EUR": ("Europe/Amsterdam", ((0, "08:01", 0, "21:59"),)),
    "ESPIX_EUR": ("Europe/Madrid", ((0, "08:01", 0, "19:59"),)),
    "CH20_CHF": ("Europe/Berlin", ((0, "08:01", 0, "21:59"),)),
    "UK100_GBP": ("Europe/London", ((0, "01:01", 0, "20:59"),)),
    "UK10YB_GBP": ("Europe/London", ((0, "08:01", 0, "17:59"),)),
}
# BCO_USD is deliberately absent: the regular-hours table says New York while
# its footnote gives a different London session and two maintenance breaks.
# Keep Brent unknown until the broker supplies an unambiguous schedule.
NY = ZoneInfo("America/New_York")
HOLIDAY_SOURCE = "https://www.oanda.com/uk-en/trading/holiday-trading-hours/"
HOLIDAY_COVERAGE = (date(2026, 1, 1), date(2026, 10, 30))
# Entries contain (date, products, late opening ET, early closing ET).
# Both times absent means closed for the listed local business day. Only
# unambiguous published rules are included; typos are not interpreted as hours.
HOLIDAYS = (
    (
        "2026-01-01",
        AGRICULTURE | {"CN50_USD", "SG30_SGD", "SUGAR_USD", "DE10YB_EUR", "UK10YB_GBP"} | EUROPE,
        None,
        None,
    ),
    ("2026-01-01", INDICES | BONDS | ENERGY_METALS | {"JP225_USD", "JP225Y_JPY"}, "18:01", None),
    ("2026-01-01", HONG_KONG, "20:16", None),
    ("2026-01-01", {"AU200_AUD"}, "17:51", None),
    ("2026-01-02", {"CH20_CHF"}, None, None),
    ("2026-01-02", AGRICULTURE, "09:31", None),
    ("2026-01-19", INDICES | BONDS, None, "12:59"),
    ("2026-01-19", ENERGY_METALS, None, "14:29"),
    ("2026-01-19", AGRICULTURE | {"SUGAR_USD"}, None, None),
    ("2026-01-26", {"AU200_AUD"}, "01:11", None),
    ("2026-02-16", INDICES | BONDS, None, "12:59"),
    ("2026-02-16", ENERGY_METALS, None, "14:29"),
    ("2026-02-16", AGRICULTURE | {"SUGAR_USD"} | HONG_KONG, None, None),
    ("2026-02-17", HONG_KONG, None, None),
    ("2026-02-18", HONG_KONG, None, None),
    ("2026-02-19", HONG_KONG, "20:16", None),
    ("2026-04-02", {"AU200_AUD"}, None, "08:59"),
    ("2026-04-02", HONG_KONG, None, "14:59"),
    ("2026-04-02", {"DE30_EUR", "EU50_EUR", "DE10YB_EUR", "UK100_GBP"}, None, "15:59"),
    ("2026-04-02", ENERGY_METALS, None, "16:59"),
    ("2026-04-02", AGRICULTURE, None, "14:19"),
    (
        "2026-04-03",
        {"AU200_AUD", "DE10YB_EUR", "UK10YB_GBP", "SUGAR_USD"}
        | EUROPE
        | HONG_KONG
        | ENERGY_METALS
        | AGRICULTURE,
        None,
        None,
    ),
    ("2026-04-03", INDICES, None, "09:14"),
    ("2026-04-03", BONDS, None, "11:14"),
    (
        "2026-04-06",
        HONG_KONG | {"CH20_CHF", "ESPIX_EUR", "FR40_EUR", "NL25_EUR", "UK10YB_GBP"},
        None,
        None,
    ),
    ("2026-04-06", {"AU200_AUD"}, "19:51", None),
    ("2026-04-06", {"UK100_GBP"}, "20:01", None),
    ("2026-04-06", {"DE30_EUR", "EU50_EUR", "DE10YB_EUR"}, "20:16", None),
    ("2026-04-06", {"SUGAR_USD"}, "07:31", None),
    ("2026-04-07", HONG_KONG, "21:16", None),
    ("2026-04-30", HONG_KONG, None, "14:59"),
    ("2026-04-30", {"DE30_EUR", "EU50_EUR", "DE10YB_EUR"}, None, "15:59"),
    (
        "2026-05-01",
        HONG_KONG
        | {"CH20_CHF", "DE30_EUR", "ESPIX_EUR", "EU50_EUR", "FR40_EUR", "NL25_EUR", "DE10YB_EUR"},
        None,
        None,
    ),
    ("2026-05-04", {"UK100_GBP"}, "20:01", None),
    ("2026-05-04", {"UK10YB_GBP"}, None, None),
    ("2026-05-04", {"SUGAR_USD"}, "07:31", None),
    ("2026-05-14", {"CH20_CHF"}, None, None),
    ("2026-05-25", HONG_KONG | AGRICULTURE | {"CH20_CHF", "UK10YB_GBP", "SUGAR_USD"}, None, None),
    ("2026-05-25", {"UK100_GBP"}, "20:01", None),
    ("2026-05-25", INDICES | BONDS, None, "12:59"),
    ("2026-05-25", ENERGY_METALS, None, "14:29"),
    ("2026-06-08", {"AU200_AUD"}, "03:11", None),
    ("2026-06-19", HONG_KONG | AGRICULTURE | {"SUGAR_USD"}, None, None),
    ("2026-06-19", INDICES | BONDS | ENERGY_METALS, None, "12:59"),
    ("2026-07-01", HONG_KONG, None, None),
    ("2026-07-03", INDICES | BONDS | ENERGY_METALS, None, "12:59"),
    ("2026-07-03", AGRICULTURE | {"SUGAR_USD"}, None, None),
    ("2026-08-31", {"UK100_GBP", "UK10YB_GBP"}, None, None),
    ("2026-08-31", {"SUGAR_USD"}, "07:31", None),
    ("2026-09-07", INDICES | BONDS, None, "12:59"),
    ("2026-09-07", ENERGY_METALS, None, "14:29"),
    ("2026-09-07", AGRICULTURE | {"SUGAR_USD"}, None, None),
    ("2026-10-01", HONG_KONG, None, None),
    ("2026-10-19", HONG_KONG, None, None),
)


def endpoint(day, offset, clock, zone, *, shift=False):
    stamp = datetime.combine(day + timedelta(days=offset), time.fromisoformat(clock), zone)
    if shift and stamp.dst():
        stamp += timedelta(hours=1)
    return stamp.astimezone(UTC)


def subtract(opening, closing, pauses):
    spans = [(opening, closing)]
    for left, right in pauses:
        trimmed = []
        for start, end in spans:
            if right <= start or left >= end:
                trimmed.append((start, end))
            else:
                if start < left:
                    trimmed.append((start, left))
                if right < end:
                    trimmed.append((right, end))
        spans = trimmed
    return spans


def sessions(symbol, start, end, *, currency=False):
    rule = RULES.get(symbol)
    if currency:
        # TRY observations in the connected account extend beyond the published
        # Central Europe hours. Retain the conservative FX grid for those pairs.
        rule = ("America/New_York", ((-1, "17:05", 0, "16:59"),))
    if rule is None:
        return
    zone = ZoneInfo(rule[0])
    day = start.astimezone(zone).date() - timedelta(days=2)
    last = end.astimezone(zone).date() + timedelta(days=2)
    pauses = []
    if "NZD" in symbol.split("_"):
        auckland = ZoneInfo("Pacific/Auckland")
        local = start.astimezone(auckland).date() - timedelta(days=2)
        while local <= end.astimezone(auckland).date() + timedelta(days=2):
            pauses.append(
                (endpoint(local, 0, "06:59", auckland), endpoint(local, 0, "07:05", auckland))
            )
            local += timedelta(days=1)
    while day <= last:
        if day.weekday() < 5:
            exceptions = [
                (late, early)
                for stamp, symbols, late, early in HOLIDAYS
                if stamp == day.isoformat() and symbol in symbols
            ]
            for left, begin, right, finish in rule[1]:
                opening = endpoint(
                    day, left, begin, zone, shift=symbol in {"DE30_EUR", "EU50_EUR", "DE10YB_EUR"}
                )
                closing = endpoint(
                    day, right, finish, zone, shift=symbol == "AU200_AUD" and right == 1
                )
                if currency and day == date(2026, 1, 1):
                    opening = max(opening, endpoint(day, 0, "17:05", NY))
                for late, early in exceptions:
                    if late is None and early is None:
                        closing = opening
                    if late is not None:
                        opening = max(opening, endpoint(day, 0, late, NY))
                    if early is not None:
                        closing = min(closing, endpoint(day, 0, early, NY))
                if opening < closing:
                    yield from subtract(opening, closing, pauses)
        day += timedelta(days=1)
