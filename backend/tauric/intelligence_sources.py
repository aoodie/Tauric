"""Read-only, bounded headline metadata. Source text is evidence, never instructions."""

import hashlib
import ipaddress
import json
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from .data_providers import connections
from .models import now

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
MAX_BYTES = 1_000_000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def plain(value, limit):
    if not isinstance(value, str) or len(value) > 100_000:
        raise ValueError("Unbounded headline text")
    parser = PlainText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())[:limit]


def public_url(value):
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("Invalid source link")
    endpoint = urlsplit(value)
    if (
        endpoint.scheme != "https"
        or not endpoint.hostname
        or "." not in endpoint.hostname
        or endpoint.username
        or endpoint.password
        or endpoint.port is not None
        or endpoint.hostname.endswith((".local", ".localhost", ".internal"))
    ):
        raise ValueError("Invalid source link")
    try:
        ipaddress.ip_address(endpoint.hostname)
    except ValueError:
        return value
    raise ValueError("IP source links are unsupported")


def timestamp(value, stamp):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not result.tzinfo or result > stamp + timedelta(seconds=90):
        raise ValueError("Invalid source timestamp")
    return result.astimezone(UTC).isoformat()


def parse_news(data, specs, stamp):
    articles = data.get("news")
    if not isinstance(articles, list) or len(articles) > 50:
        raise ValueError("Unbounded news response")
    symbols = {s.provider_symbol: s.id for s in specs}
    items, seen = [], set()
    for row in articles:
        try:
            if (
                not isinstance(row, dict)
                or not isinstance(row.get("symbols"), list)
                or len(row["symbols"]) > 1000
            ):
                raise ValueError("Invalid symbols")
            ids = sorted(
                {symbols[s] for s in row["symbols"] if isinstance(s, str) and s in symbols}
            )
            title, url = plain(row["headline"], 300), public_url(row["url"])
            published = timestamp(row["created_at"], stamp)
            updated = timestamp(row.get("updated_at", row["created_at"]), stamp)
            if updated < published or not ids or not title:
                continue
            key = "alpaca:" + str(row["id"])
            if len(key) > 100 or key in seen:
                continue
            seen.add(key)
            items.append(
                {
                    "id": key,
                    "kind": "headline",
                    "title": title,
                    "url": url,
                    "source": plain(row.get("source", "Alpaca news"), 100),
                    "instrument_ids": ids,
                    "published_at": published,
                    "updated_at": updated,
                    "summary": plain(row.get("summary", ""), 700),
                    "evidence_type": "reported",
                }
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
    if articles and not items:
        raise ValueError("No verified matching news metadata")
    return sorted(items, key=lambda x: (x["updated_at"], x["id"]), reverse=True)


def alpaca_news(db, specs):
    stamp = now()
    source = {
        "key": "alpaca_news",
        "label": "Alpaca news",
        "url": NEWS_URL,
        "status": "unavailable",
        "checked_at": stamp.isoformat(),
        "instrument_ids": [s.id for s in specs],
        "items": [],
        "warning": "News access is unavailable. Check the Alpaca connection and news entitlement in Settings.",
    }
    try:
        config = connections(db).alpaca
    except HTTPException:
        return source
    if not config.key.get_secret_value() or not config.secret.get_secret_value():
        return source
    try:
        with httpx.Client(timeout=httpx.Timeout(8, connect=4), follow_redirects=False) as client:
            with client.stream(
                "GET",
                NEWS_URL,
                headers={
                    "APCA-API-KEY-ID": config.key.get_secret_value(),
                    "APCA-API-SECRET-KEY": config.secret.get_secret_value(),
                },
                params={
                    "symbols": ",".join(sorted({s.provider_symbol for s in specs})),
                    "start": (stamp - timedelta(days=7)).isoformat(),
                    "limit": 50,
                    "sort": "desc",
                    "include_content": "false",
                },
            ) as response:
                response.raise_for_status()
                raw = bytearray()
                for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_BYTES:
                        raise ValueError("Oversized news response")
        data = json.loads(raw)
        source["items"] = parse_news(data, specs, stamp)
        source.update(
            status="available",
            warning=None,
            truncated=bool(data.get("next_page_token")),
            response_checksum=hashlib.sha256(raw).hexdigest(),
        )
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return source
