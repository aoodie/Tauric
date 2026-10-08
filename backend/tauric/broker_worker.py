"""Bounded paper execution service, independently restartable from AI workers."""

import signal
import time

from sqlalchemy.orm import Session

from . import broker_execution, broker_positions, broker_review, broker_routes, operations
from .database import engine

STOP = False


def stop(*_):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(
        "Governed broker worker started. Opt-in closed-bar signals, bound reviews, durable orders and protected positions; models run in their separate workers.",
        flush=True,
    )
    while not STOP:
        try:
            with Session(engine()) as db:
                broker_execution.heartbeat(db)
            worked = False
            # Every lane gets a bounded turn even while other lanes stay busy.
            for lane, work in (
                ("positions", broker_positions.process_one),
                ("orders", broker_execution.reconcile_one),
                ("discovery", broker_routes.discover_one),
                ("advancement", broker_routes.advance_one),
                ("delivery", broker_review.deliver_one),
            ):
                worked = operations.run_lane("broker_execution", lane, work) or worked
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
