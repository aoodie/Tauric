"""Fresh, bounded market observations for owner chat; no research or order authority."""

from fastapi import HTTPException
from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import select

from . import chat, chat_research, market, open_research, research_intervals, services
from .market_contracts import Interval
from .models import Instrument
from .portfolio_contracts import ChatIdentity


class MarketObservationRequest(ChatIdentity):
    symbol: str = Field(min_length=1, max_length=120)
    intervals: list[str] = Field(default_factory=lambda: ["15m", "1h"], min_length=1, max_length=4)

    start: AwareDatetime | None = None
    end: AwareDatetime | None = None
    count: int = Field(default=120, ge=1, le=500, strict=True)

    @model_validator(mode="after")
    def history(self):
        if self.start and self.end and self.start >= self.end:
            raise ValueError("The requested history must end after it starts")
        from .models import now

        if (self.start and self.start > now()) or (self.end and self.end > now()):
            raise ValueError("Future price history is unavailable")
        return self

    @field_validator("intervals")
    @classmethod
    def unique_intervals(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Choose distinct chart intervals")
        for value in values:
            research_intervals.parse(value)
        return values


def normalize(symbol):
    return symbol.strip().upper().replace("/", "").replace("_", "").replace("-", "")


def gold(interval, connection):
    """Compatibility entry point; all OANDA products now share observation checks."""
    return open_research.oanda_prices("XAU/USD", interval, connection)


def observations(db, owner, body):
    config, _ = chat.load_config(db)
    chat_research.authorize(db, owner, body, config)
    key = normalize(body.symbol)
    exact = db.get(Instrument, body.symbol)
    records = (
        [exact]
        if exact
        else list(db.scalars(select(Instrument).order_by(Instrument.id).limit(5000)))
    )
    matches = [
        r
        for r in records
        if r.id == body.symbol
        or normalize(r.definition["symbol"]) == key
        or normalize(r.definition["provider_symbol"]) == key
    ]
    if len(matches) > 1:
        raise HTTPException(422, "Choose the exact instrument ID; this symbol is ambiguous.")
    results = []
    for interval in body.intervals:
        if matches and interval in Interval.__args__ and not body.start and not body.end:
            feed = market.refresh(db, matches[0].id, interval, commit=False)
            value = feed.model_dump(mode="json")
            value["candles"] = [c for c in value["candles"] if c["closed"]][-body.count :]
            value.update(
                symbol=feed.instrument.symbol,
                native_research_supported=feed.instrument.native_research_ready,
            )
            results.append(value)
        else:
            request = open_research.OpenResearchRequest(
                **body.model_dump(exclude={"intervals"}),
                operation="prices",
                interval=interval,
            )
            if matches:
                spec = matches[0].definition
                request.symbol = spec["provider_symbol"]
                request.provider = "oanda" if spec["provider"] == "oanda_v20" else "yahoo"
                if spec["asset_type"] == "crypto_spot":
                    raise HTTPException(
                        422,
                        "This registered crypto chart has no extended-history/timeframe adapter. Use open source research; do not substitute a USD market for the requested USDT market.",
                    )
            results.append(open_research.prices(db, request))
    # Data collection does not extend a revoked chat lease.
    chat_research.authorize(db, owner, body, config)
    services.audit(
        db,
        owner,
        "chat.market_observed",
        "chat",
        body.tool_call_id,
        "OpenClaw read fresh market observations through Tauric",
        {
            "symbol": body.symbol,
            "intervals": body.intervals,
            "observations": [
                {
                    "interval": result["interval"],
                    "received_at": result.get("received_at"),
                    "checksum": result.get("checksum"),
                }
                for result in results
            ],
            "native_report_created": False,
            "orders_submitted": False,
        },
    )
    db.commit()
    return {
        "authority": "market_observation_only",
        "observations": results,
        "native_report_created": False,
        "orders_submitted": False,
        "instruction": "Use the received timestamps, closed bars and warnings. Distinguish your chart interpretation from a native Tauric report. Outdated observations cannot establish current entry levels.",
    }
