"""Daily portfolio research continues with the browser closed; no execution authority."""

import signal
import time

from sqlalchemy.orm import Session

from . import discovery, history_store, portfolio_research
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
            worked = False
            for lane in (history_store, discovery, portfolio_research):
                try:
                    with Session(engine()) as db:
                        worked = lane.process_one(db) or worked
                        if lane is discovery:
                            discovery.heartbeat(db)
                except Exception:
                    print("Research lane unavailable: " + lane.__name__.split(".")[-1], flush=True)
            if not worked:
                time.sleep(2)
        except Exception:
            print("Portfolio research coordination unavailable; inspect saved status.", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
