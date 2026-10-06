import copy
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from test_market_adapters import FX, spec

from tauric import forex_macro

STAMP = datetime(2026, 10, 4, 12, tzinfo=UTC)
HTML = {
    "USD": '<h4>2026 FOMC Meetings</h4><div class="fomc-meeting__month"><strong>October</strong></div><div class="fomc-meeting__date">27-28*</div><p>Minutes released November 18</p><h4>2027 FOMC Meetings</h4><div class="fomc-meeting__month">January</div><div class="fomc-meeting__date">26-27</div>',
    "EUR": "<dl><dt>28/10/2026</dt><dd>monetary policy meeting (Day 1)</dd><dt>29/10/2026</dt><dd>monetary policy meeting (Day 2), followed by press conference</dd><dt>25/11/2026</dt><dd>non-monetary policy meeting</dd></dl>",
    "GBP": "<h2>2026 confirmed dates</h2><table><tr><td>Thursday 5 November</td><td>MPC summary</td></tr></table><h2>2027 confirmed dates</h2><table><tr><td>Thursday 4 February</td><td>MPC summary</td></tr></table>",
    "JPY": "<table><caption>Table : 2026</caption><tr><th>Date of MPM</th><th>Outlook</th><th>Summary</th><th>Minutes</th></tr><tr><td>Oct. 29 (Thurs.), 30 (Fri.)</td><td>Oct. 30 (Fri.)</td><td>Nov. 10 (Tues.)</td><td>Dec. 23 (Wed.)</td></tr></table>",
}


def feed(currency, published=STAMP - timedelta(days=1), url=None):
    url = url or forex_macro.SOURCES[currency][2]
    return f"<rss><channel><item><title>Official {currency} policy headline</title><link>{url}</link><pubDate>{format_datetime(published)}</pubDate></item></channel></rss>"


@pytest.mark.parametrize(
    "currency,expected",
    [
        ("USD", ["2026-10-28", "2027-01-27"]),
        ("EUR", ["2026-10-29"]),
        ("GBP", ["2026-11-05", "2027-02-04"]),
        ("JPY", ["2026-10-30"]),
    ],
)
def test_calendar_parsers_preserve_year_and_meeting_end_without_minutes_or_times(
    currency, expected
):
    rows = forex_macro.events(HTML[currency], currency, STAMP)
    assert [row["date"] for row in rows] == expected
    assert all(row["time"] is None for row in rows)
    assert all(row["timezone"] == forex_macro.SOURCES[currency][1] for row in rows)


def test_calendar_uses_source_local_date():
    html = '<h4>2026 FOMC Meetings</h4><div class="fomc-meeting__month">October</div><div class="fomc-meeting__date">4</div>'
    stamp = datetime(2026, 10, 5, 1, tzinfo=UTC)  # Still October 4 in New York.
    assert forex_macro.events(html, "USD", stamp)[0]["date"] == "2026-10-04"


@pytest.mark.parametrize("currency", ["USD", "EUR", "GBP", "JPY"])
def test_unrecognised_calendar_never_claims_no_events(currency):
    with pytest.raises(ValueError):
        forex_macro.events(
            "<html><p>Source format changed; next meeting soon</p></html>", currency, STAMP
        )


def test_feed_dedup_dates_future_filter_and_boj_secure_link_normalization():
    url = "http://www.boj.or.jp/en/mopo/policy.htm"
    rows = forex_macro.headlines(feed("JPY", url=url), "JPY", STAMP)
    assert rows[0]["source_url"] == url
    assert rows[0]["url"] == "https://www.boj.or.jp/en/mopo/policy.htm"
    assert rows[0]["published_at"].endswith("+00:00")
    with pytest.raises(ValueError):
        forex_macro.headlines(feed("USD", published=STAMP + timedelta(days=1)), "USD", STAMP)
    with pytest.raises(ValueError):
        forex_macro.headlines(feed("USD", url="https://untrusted.example/news"), "USD", STAMP)


def test_feed_entities_are_rejected():
    with pytest.raises(ValueError, match="entities"):
        forex_macro.headlines('<!DOCTYPE rss [<!ENTITY a "boom">]><rss/>', "USD", STAMP)


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(forex_macro, "now", lambda: STAMP)
    original = httpx.Client
    state = {"requests": [], "fault": None}
    routes = {
        url: (currency, kind)
        for currency, (_, _, news, calendar) in forex_macro.SOURCES.items()
        for kind, url in (("news", news), ("calendar", calendar))
    }

    def handle(request):
        state["requests"].append(str(request.url))
        currency, kind = routes[str(request.url)]
        if currency == "EUR" and kind == "news":
            if state["fault"] == "redirect":
                return httpx.Response(302, headers={"Location": "https://untrusted.example/"})
            if state["fault"] == "403":
                return httpx.Response(403, text="Do not expose this upstream error body")
            if state["fault"] == "timeout":
                raise httpx.ReadTimeout("fixture", request=request)
            if state["fault"] == "oversize":
                return httpx.Response(200, text="x" * (forex_macro.MAX_BYTES + 1))
            if state["fault"] == "stale":
                return httpx.Response(
                    200, text=feed(currency, published=STAMP - timedelta(days=50))
                )
        return httpx.Response(200, text=feed(currency) if kind == "news" else HTML[currency])

    monkeypatch.setattr(
        forex_macro.httpx,
        "Client",
        lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs),
    )
    return state


@pytest.mark.parametrize(
    "fault,status",
    [
        (None, "available"),
        ("redirect", "unavailable"),
        ("403", "unavailable"),
        ("timeout", "unavailable"),
        ("oversize", "unavailable"),
        ("stale", "stale"),
    ],
)
def test_collector_keeps_partial_coverage_and_never_follows_feed_links(offline, fault, status):
    offline["fault"] = fault
    snapshot = forex_macro.collect(spec(FX))
    data = forex_macro.validate(snapshot, spec(FX).model_dump())
    assert len(offline["requests"]) == 4
    assert data["sources"][0]["status"] == status
    assert all(row["status"] == "available" for row in data["sources"][1:])
    assert "Do not expose this" not in str(data)
    assert data["currencies"] == ["EUR", "USD"]
    assert "not a full economic" in data["coverage"]


@pytest.mark.parametrize(
    "fault",
    ["checksum", "currency", "stale_receipt", "future_receipt", "missing_source", "wrong_url"],
)
def test_policy_evidence_binding_is_checked_before_model_use(offline, fault):
    snapshot = copy.deepcopy(forex_macro.collect(spec(FX)))
    data = snapshot["data"]
    if fault == "currency":
        data["currencies"] = ["GBP", "USD"]
    if fault == "stale_receipt":
        data["fetched_at"] = (STAMP - timedelta(hours=7)).isoformat()
    if fault == "future_receipt":
        data["fetched_at"] = (STAMP + timedelta(hours=1)).isoformat()
    if fault == "missing_source":
        data["sources"].pop()
    if fault == "wrong_url":
        data["sources"][0]["url"] = "https://untrusted.example/"
    snapshot["checksum"] = forex_macro.digest(data) if fault != "checksum" else "wrong"
    with pytest.raises(ValueError):
        forex_macro.validate(snapshot, spec(FX).model_dump())


@pytest.mark.parametrize("currencies", [("NZD", "USD"), ("AUD", "CAD"), ("SEK", "NOK")])
def test_missing_currency_adapters_are_explicit_gaps_not_pair_failures(offline, currencies):
    instrument = spec(FX).model_copy(
        update={"base_currency": currencies[0], "quote_currency": currencies[1]}
    )
    snapshot = forex_macro.collect(instrument)
    data = forex_macro.validate(snapshot, instrument.model_dump())
    missing = [row for row in data["sources"] if row["currency"] not in forex_macro.SOURCES]
    assert missing and all(
        row["status"] == "unavailable" and row["items"] == [] and row["url"] is None
        for row in missing
    )
    assert len(offline["requests"]) == 2 * sum(c in forex_macro.SOURCES for c in currencies)
    for fault in ("status", "items", "url"):
        altered = copy.deepcopy(snapshot)
        row = next(
            row for row in altered["data"]["sources"] if row["currency"] not in forex_macro.SOURCES
        )
        row[fault] = {
            "status": "available",
            "items": [{"title": "invented policy"}],
            "url": "https://untrusted.example/",
        }[fault]
        altered["checksum"] = forex_macro.digest(altered["data"])
        with pytest.raises(ValueError):
            forex_macro.validate(altered, instrument.model_dump())
