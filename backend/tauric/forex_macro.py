"""Bounded primary-source headlines and date-only policy calendars for FX.

Every observation is receipt-stamped and frozen in the analysis request. Source
failures remain explicit; this is not a comprehensive economic release calendar.
"""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from .models import now

VERSION = "tauric_primary_policy_context_v1"
MAX_BYTES = 1_000_000
SOURCES = {
    "USD": (
        "Federal Reserve",
        "America/New_York",
        "https://www.federalreserve.gov/feeds/press_monetary.xml",
        "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
    ),
    "EUR": (
        "European Central Bank",
        "Europe/Berlin",
        "https://www.ecb.europa.eu/rss/press.html",
        "https://www.ecb.europa.eu/press/calendars/mgcgc/html/index.en.html",
    ),
    "GBP": (
        "Bank of England",
        "Europe/London",
        "https://www.bankofengland.co.uk/rss/news",
        "https://www.bankofengland.co.uk/monetary-policy/upcoming-mpc-dates",
    ),
    "JPY": (
        "Bank of Japan",
        "Asia/Tokyo",
        "https://www.boj.or.jp/en/rss/whatsnew.xml",
        "https://www.boj.or.jp/en/mopo/mpmsche_minu/index.htm",
    ),
}
MONTHS = {
    name: index
    for index, name in enumerate(
        (
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ),
        1,
    )
}


def month(value):
    value = value.lower().strip(". ")
    found = [index for name, index in MONTHS.items() if name.startswith(value)]
    if len(found) != 1:
        raise ValueError("Unrecognised calendar month")
    return found[0]


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.parts = tag, dict(attrs), []

    def text(self):
        return " ".join(
            " ".join(p.text() if isinstance(p, Node) else p for p in self.parts).split()
        )

    def walk(self):
        yield self
        for part in self.parts:
            if isinstance(part, Node):
                yield from part.walk()


class Document(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack, self.count = [self.root], 0
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 20_000 or len(self.stack) > 64:
            raise ValueError("Oversized source DOM")
        node = Node(tag, attrs)
        self.stack[-1].parts.append(node)
        if tag not in {
            "br",
            "hr",
            "meta",
            "link",
            "input",
            "img",
            "source",
            "wbr",
            "area",
            "base",
            "embed",
            "param",
            "col",
        }:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        if not any(n.tag in {"script", "style"} for n in self.stack):
            self.stack[-1].parts.append(data)


def events(html, currency, stamp):
    nodes = list(Document(html).root.walk())
    found = []
    if currency == "EUR":
        pending = None
        for node in nodes:
            if node.tag == "dt":
                pending = node.text()
            elif node.tag == "dd" and pending:
                text = node.text().lower()
                if (
                    "monetary policy meeting" in text
                    and "non-monetary" not in text
                    and ("day 2" in text or "press conference" in text)
                ):
                    found.append(datetime.strptime(pending, "%d/%m/%Y").date())
                pending = None
    elif currency == "USD":
        year, current_month = None, None
        for node in nodes:
            title = (
                re.fullmatch(r"(20\d{2}) FOMC Meetings", node.text()) if node.tag == "h4" else None
            )
            if title:
                year, current_month = int(title[1]), None
            classes = (node.attrs.get("class") or "").split()
            if year and year >= stamp.year:
                if "fomc-meeting__month" in classes:
                    current_month = month(node.text())
                if "fomc-meeting__date" in classes:
                    match = re.fullmatch(r"(\d{1,2})(?:\s*-\s*(\d{1,2}))?\*?", node.text())
                    if not match or not current_month:
                        raise ValueError("Unrecognised FOMC meeting date")
                    last = int(match[2] or match[1])
                    if last < int(match[1]):
                        raise ValueError("Cross-month meeting needs an explicit parser")
                    found.append(date(year, current_month, last))
    elif currency == "JPY":
        for table in (n for n in nodes if n.tag == "table"):
            captions = [n.text() for n in table.walk() if n.tag == "caption"]
            year_match = re.fullmatch(r"Table\s*:\s*(20\d{2})", captions[0]) if captions else None
            if not year_match or int(year_match[1]) < stamp.year:
                continue
            if not any(n.tag == "th" and n.text() == "Date of MPM" for n in table.walk()):
                continue
            for row in (n for n in table.walk() if n.tag == "tr"):
                cells = [p for p in row.parts if isinstance(p, Node) and p.tag == "td"]
                if not cells:
                    continue
                match = re.match(
                    r"([A-Za-z]+)\.?\s+(\d{1,2})\s*\([^)]*\)(?:\s*,\s*(\d{1,2})\s*\([^)]*\))?",
                    cells[0].text(),
                )
                if not match:
                    raise ValueError("Unrecognised BOJ meeting date")
                found.append(date(int(year_match[1]), month(match[1]), int(match[3] or match[2])))
    elif currency == "GBP":
        year = None
        for node in nodes:
            heading = (
                re.fullmatch(r"(20\d{2}) confirmed dates", node.text(), re.I)
                if node.tag in {"h2", "h3"}
                else None
            )
            if heading:
                year = int(heading[1])
            if node.tag == "tr" and year and year >= stamp.year:
                cells = [p for p in node.parts if isinstance(p, Node) and p.tag == "td"]
                if cells:
                    match = re.fullmatch(
                        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday)\s+(\d{1,2})\s+([A-Za-z]+)",
                        cells[0].text(),
                    )
                    if not match:
                        raise ValueError("Unrecognised MPC announcement date")
                    found.append(date(year, month(match[2]), int(match[1])))
    local_day = stamp.astimezone(ZoneInfo(SOURCES[currency][1])).date()
    upcoming = sorted({d for d in found if local_day <= d <= local_day + timedelta(days=365)})
    if not upcoming:
        raise ValueError("No recognised upcoming policy dates")
    label, timezone, _, url = SOURCES[currency]
    return [
        {
            "date": d.isoformat(),
            "time": None,
            "timezone": timezone,
            "label": f"{label} policy meeting / announcement · final day",
            "url": url,
        }
        for d in upcoming[:8]
    ]


def headlines(xml, currency, stamp):
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", xml, re.I):
        raise ValueError("Feed entities are not supported")
    root = ElementTree.fromstring(xml.lstrip("\ufeff"))
    items = root.findall("./channel/item")
    if not items or len(items) > 500:
        raise ValueError("No bounded RSS items")
    rows, seen = [], set()
    expected_host = urlsplit(SOURCES[currency][2]).hostname
    for item in items:
        title = " ".join((item.findtext("title") or "").split())[:300]
        url = (item.findtext("link") or "").strip()
        endpoint = urlsplit(url)
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
        except (ValueError, TypeError):
            continue
        if (
            not title
            or endpoint.scheme not in {"http", "https"}
            or endpoint.hostname != expected_host
            or endpoint.username
            or endpoint.password
            or endpoint.port is not None
            or not published.tzinfo
            or published > stamp + timedelta(seconds=90)
            or url in seen
        ):
            continue
        seen.add(url)
        rows.append(
            {
                "title": title,
                "url": endpoint._replace(scheme="https").geturl(),
                "source_url": url,
                "published_at": published.astimezone(UTC).isoformat(),
            }
        )
    if not rows:
        raise ValueError("No valid dated primary-source headlines")
    return sorted(rows, key=lambda row: row["published_at"], reverse=True)[:5]


def _fetch(client, url):
    with client.stream("GET", url) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > MAX_BYTES:
                raise ValueError("Oversized primary source")
        return bytes(data).decode("utf-8-sig", errors="strict")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def valid_currencies(currencies):
    return (
        bool(currencies)
        and len(set(currencies)) == len(currencies)
        and all(isinstance(c, str) and re.fullmatch(r"[A-Z]{3}", c) for c in currencies)
    )


def collect(spec):
    currencies = [spec.base_currency, spec.quote_currency]
    if not valid_currencies(currencies):
        raise ValueError("Unsupported policy currency")
    return collect_currencies(currencies)


def collect_currencies(currencies):
    """Share each primary source once across the intelligence research universe."""
    if not valid_currencies(currencies):
        raise ValueError("Unsupported policy currencies")
    stamp = now()

    def read(task):
        currency, kind = task
        configured = SOURCES.get(currency)
        label, timezone, news_url, calendar_url = configured or (
            currency + " policy source",
            None,
            None,
            None,
        )
        url = news_url if kind == "news" else calendar_url
        result = {
            "currency": currency,
            "kind": kind,
            "label": label,
            "url": url,
            "checked_at": stamp.isoformat(),
            "status": "unavailable",
            "items": [],
            "warning": "The primary source could not be fetched or parsed; coverage is unavailable.",
        }
        if not configured:
            result["warning"] = (
                f"No verified official {kind} source adapter for {currency}; this coverage is unavailable."
            )
            return result
        try:
            with httpx.Client(
                timeout=httpx.Timeout(6, connect=4),
                follow_redirects=False,
                headers={"User-Agent": "Tauric research source reader"},
            ) as client:
                raw = _fetch(client, url)
            result["items"] = (
                headlines(raw, currency, stamp) if kind == "news" else events(raw, currency, stamp)
            )
            result["response_checksum"] = hashlib.sha256(raw.encode()).hexdigest()
            result["status"], result["warning"] = "available", None
            if kind == "news" and datetime.fromisoformat(
                result["items"][0]["published_at"]
            ) < stamp - timedelta(days=45):
                result["status"], result["warning"] = (
                    "stale",
                    "Latest published headline is more than 45 days old; do not treat it as current news.",
                )
        except (httpx.HTTPError, ValueError, TypeError, ElementTree.ParseError):
            pass
        return result

    with ThreadPoolExecutor(max_workers=4) as pool:
        sources = list(
            pool.map(read, [(c, kind) for c in currencies for kind in ("news", "calendar")])
        )
    context = {
        "version": VERSION,
        "fetched_at": stamp.isoformat(),
        "currencies": currencies,
        "coverage": "Central-bank headlines and date-only policy meetings; not a full economic release calendar.",
        "sources": sources,
        "limitations": [
            "Headline metadata only; article bodies and current policy rates are not supplied.",
            "Inflation/employment releases, consensus forecasts, surprise values, unscheduled events and broker financing are not covered.",
            "Meeting dates are date-only, may change, and do not establish an exact release time.",
        ],
    }
    return {"data": context, "checksum": digest(context)}


def validate(snapshot, spec):
    data = snapshot["data"]
    if (
        snapshot.get("checksum") != digest(data)
        or data.get("version") != VERSION
        or data.get("currencies") != [spec["base_currency"], spec["quote_currency"]]
    ):
        raise ValueError("Policy evidence binding mismatch")
    stamp = datetime.fromisoformat(data["fetched_at"])
    if (
        not stamp.tzinfo
        or stamp > now() + timedelta(seconds=90)
        or now() - stamp > timedelta(hours=6)
    ):
        raise ValueError("Policy evidence receipt is stale or invalid")
    expected = [
        (currency, kind) for currency in data["currencies"] for kind in ("news", "calendar")
    ]
    if [(row["currency"], row["kind"]) for row in data["sources"]] != expected:
        raise ValueError("Incomplete policy source declaration")
    for row in data["sources"]:
        configured = SOURCES.get(row["currency"])
        url = configured[2 if row["kind"] == "news" else 3] if configured else None
        if (
            row["url"] != url
            or row["status"] not in {"available", "stale", "unavailable"}
            or (not configured and (row["status"] != "unavailable" or row["items"]))
        ):
            raise ValueError("Invalid policy source declaration")
    return data
