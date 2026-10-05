"""Bounded paper execution service, independently restartable from AI workers."""

import signal
import time

from sqlalchemy.orm import Session

from . import autonomous
from .database import engine

STOP = False


def stop(*_):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print("Autonomous paper worker started. Saved opt-in policies only.", flush=True)
    while not STOP:
        try:
            with Session(engine()) as db:
                autonomous.heartbeat(db)
            with Session(engine()) as db:
                worked = autonomous.process_one(db)
            if not worked:
                time.sleep(2)
        except Exception:
            print(
                "Autonomous paper worker unavailable; waiting to reconnect. Inspect saved status.",
                flush=True,
            )
            time.sleep(3)


if __name__ == "__main__":
    main()
