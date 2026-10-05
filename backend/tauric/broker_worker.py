"""Bounded paper execution service, independently restartable from AI workers."""

import signal
import time

from sqlalchemy.orm import Session

from . import broker_execution, broker_positions, broker_review, broker_routes
from .database import engine

STOP = False


def stop(*_):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        "Demo broker worker started. Opt-in closed-bar signals, bound reviews, durable orders and attributed positions; models run in their separate workers.",
        flush=True,
    )
    while not STOP:
        try:
            with Session(engine()) as db:
                broker_execution.heartbeat(db)
            worked = False
            # Every lane gets a bounded turn even while other lanes stay busy.
            for work in (
                broker_routes.discover_one,
                broker_routes.advance_one,
                broker_review.deliver_one,
                broker_execution.reconcile_one,
                broker_positions.process_one,
            ):
                try:
                    with Session(engine()) as db:
                        worked = work(db) or worked
                except Exception:
                    print(
                        "A demo worker lane is unavailable. Other lanes continue; inspect saved status.",
                        flush=True,
                    )
            if not worked:
                time.sleep(2)
        except Exception:
            print(
                "Demo broker reconciliation worker unavailable; waiting to reconnect. Inspect saved status.",
                flush=True,
            )
            time.sleep(3)


if __name__ == "__main__":
    main()
