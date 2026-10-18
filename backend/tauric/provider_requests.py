"""Shared read-only data budgets. No financial credentials or broker requests."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from decimal import Decimal, localcontext

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import now

# Conservative standard-feed allowance; never infer a paid entitlement.
# Small burst capacity also limits requests across minute boundaries.
LIMITS = {"oanda_v20": (50, Decimal(50)), "alpaca_stocks": (20, Decimal(3))}
CURRENT = ContextVar("market_request_budget", default=None)


def remaining(db, provider):
    """Observe the shared read-only allowance without consuming a request."""
    capacity, rate = LIMITS[provider]
    row = db.execute(
        text(
            "SELECT tokens,updated_at,retry_at FROM market_request_budgets WHERE provider=:provider"
        ),
        {"provider": provider},
    ).first()
    if not row:
        return capacity
    stamp = now()
    if row.retry_at and row.retry_at > stamp:
        return 0
    with localcontext() as ctx:
        ctx.prec = 120
        elapsed = max(Decimal(0), Decimal(str((stamp - row.updated_at).total_seconds())))
        return int(min(Decimal(capacity), row.tokens + elapsed * rate))


class Budget:
    def __init__(self, bind, provider):
        self.bind, self.provider = bind, provider
        self.remaining = None

    def acquire(self):
        capacity, rate = LIMITS[self.provider]
        stamp = now()
        denied = None
        with Session(self.bind) as db, localcontext() as ctx:
            ctx.prec = 120
            db.execute(
                text(
                    "INSERT INTO market_request_budgets(provider,tokens,updated_at) VALUES(:provider,:tokens,:stamp) ON CONFLICT DO NOTHING"
                ),
                {"provider": self.provider, "tokens": capacity, "stamp": stamp},
            )
            row = db.execute(
                text(
                    "SELECT tokens,updated_at,retry_at FROM market_request_budgets WHERE provider=:provider FOR UPDATE"
                ),
                {"provider": self.provider},
            ).one()
            elapsed = max(Decimal(0), Decimal(str((stamp - row.updated_at).total_seconds())))
            tokens = min(Decimal(capacity), row.tokens + elapsed * rate)
            if row.retry_at and row.retry_at > stamp:
                denied = max(1, int((row.retry_at - stamp).total_seconds()) + 1)
            elif tokens < 1:
                denied = 1
            else:
                tokens -= 1
            self.remaining = int(tokens)
            db.execute(
                text(
                    "UPDATE market_request_budgets SET tokens=:tokens,updated_at=:stamp WHERE provider=:provider"
                ),
                {"provider": self.provider, "tokens": tokens, "stamp": stamp},
            )
            db.commit()
        if denied:
            raise HTTPException(
                429,
                "The shared market-data budget is cooling down. The catalog slice will resume.",
                headers={"Retry-After": str(denied)},
            )

    def backoff(self, seconds=60):
        retry = now() + timedelta(seconds=max(1, min(seconds, 300)))
        with Session(self.bind) as db:
            db.execute(
                text(
                    "UPDATE market_request_budgets SET retry_at=GREATEST(COALESCE(retry_at,:retry),:retry) WHERE provider=:provider"
                ),
                {"provider": self.provider, "retry": retry},
            )
            db.commit()


@contextmanager
def scope(db, provider):
    if provider not in LIMITS:
        yield None
        return
    budget = Budget(db.get_bind(), provider)
    token = CURRENT.set(budget)
    try:
        yield budget
    finally:
        CURRENT.reset(token)


def acquire():
    budget = CURRENT.get()
    if budget:
        budget.acquire()


def observe(response):
    budget = CURRENT.get()
    if budget and response.status_code == 429:
        try:
            seconds = int(response.headers.get("Retry-After", "60"))
        except ValueError:
            seconds = 60
        budget.backoff(seconds)
