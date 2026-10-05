"""Daily portfolio research continues with the browser closed; no execution authority."""

import signal
import time

from sqlalchemy.orm import Session

from . import portfolio_research
from .database import engine

STOP = False


def stop(*_):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        "Portfolio research coordinator started. Saved schedules only; no trading authority.",
        flush=True,
    )
    while not STOP:
        try:
            with Session(engine()) as db:
                portfolio_research.heartbeat(db)
            with Session(engine()) as db:
                worked = portfolio_research.process_one(db)
            if not worked:
                time.sleep(2)
        except Exception:
            print("Portfolio research coordination unavailable; inspect saved status.", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
