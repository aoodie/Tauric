"""Bounded paper monitor service, independently restartable from AI workers."""

import signal
import time

from sqlalchemy.orm import Session

from . import strategy_scheduler
from .database import engine

STOP = False


def stop(*_):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("Paper strategy monitor started. Saved opt-in schedules only.", flush=True)
    while not STOP:
        try:
            with Session(engine()) as db:
                strategy_scheduler.heartbeat(db)
            with Session(engine()) as db:
                worked = strategy_scheduler.process_one(db)
            if not worked:
                time.sleep(2)
        except Exception:
            print(
                "Paper monitor unavailable; waiting to reconnect. Inspect saved status.", flush=True
            )
            time.sleep(3)


if __name__ == "__main__":
    main()
