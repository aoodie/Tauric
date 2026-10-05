"""Explicit synthetic macro observations for offline browser checks."""

from datetime import timedelta

from tauric import forex_macro, forex_sources
from tauric.models import now


def fixture(spec):
    stamp = now()
    sources = []
    for currency in [spec.base_currency, spec.quote_currency]:
        country = forex_sources.COUNTRIES[currency]
        for kind in ["economic_calendar", "policy_rate"]:
            url = (
                forex_sources.HOST + "/calendar/country/" + country.lower().replace(" ", "%20")
                if kind == "economic_calendar"
                else forex_sources.HOST
                + "/country/"
                + country.lower().replace(" ", "%20")
                + "/interest%20rate"
            )
            items = (
                [
                    {
                        "id": "synthetic-release-" + currency,
                        "event": "Synthetic inflation release",
                        "category": "Inflation Rate",
                        "date": stamp.date().isoformat(),
                        "time": (stamp - timedelta(hours=1)).isoformat(),
                        "actual": "3.2%",
                        "forecast": "3.0%",
                        "provider_forecast": "3.1%",
                        "previous": "3.3%",
                        "reference": "Synthetic fixture",
                        "source": "Offline fixture",
                        "url": "https://www.bls.gov/",
                        "surprise": {"value": "0.2", "unit": "%"},
                    }
                ]
                if kind == "economic_calendar"
                else [
                    {
                        "name": "Synthetic policy benchmark",
                        "value": "4",
                        "unit": "percent",
                        "effective_date": stamp.date().isoformat(),
                        "scope": "Synthetic fixture, not a live rate",
                        "url": "https://www.federalreserve.gov/",
                    }
                ]
            )
            sources.append(
                {
                    "currency": currency,
                    "kind": kind,
                    "provider": "Trading Economics",
                    "url": url,
                    "status": "available",
                    "items": items,
                    "checked_at": stamp.isoformat(),
                    "connection_revision": 0,
                }
            )
    sources.append(
        {
            "currency": spec.quote_currency,
            "kind": "broker_financing",
            "provider": "OANDA",
            "url": "https://developer.oanda.com/rest-live-v20/primitives-df/#InstrumentFinancing",
            "status": "available",
            "items": [
                {
                    "long_rate_decimal": "-0.04",
                    "short_rate_decimal": "0.01",
                    "scope": "Synthetic fixture, not a broker charge",
                    "schedule": [{"day": "WEDNESDAY", "days_charged": 3}],
                }
            ],
            "checked_at": stamp.isoformat(),
            "connection_revision": 0,
        }
    )
    data = {
        "version": forex_sources.VERSION,
        "instrument_id": spec.id,
        "currencies": [spec.base_currency, spec.quote_currency],
        "fetched_at": stamp.isoformat(),
        "sources": sources,
        "coverage": "Explicit offline fixture; no public or account provider calls.",
    }
    return {"data": data, "checksum": forex_macro.digest(data)}
