"""Independent prospective evidence collection; this lane has no order authority."""

import signal
import time

from sqlalchemy.orm import Session

from . import live_trading
from .database import engine
from .models import WorkerStatus, now

STOP = False


def stop(*_):
    global STOP
    STOP = True


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not STOP:
        try:
            with Session(engine()) as db:
                row = db.get(WorkerStatus, "forward_validation")
                if row:
                    row.heartbeat_at = now()
                else:
                    db.add(
                        WorkerStatus(id="forward_validation", heartbeat_at=now(), version="0.12.0")
                    )
                db.commit()
            with Session(engine()) as db:
                live_trading.process_one(db)
        except Exception:
            print(
                "Forward evidence is unavailable; inspect its saved report. No entry was authorized.",
                flush=True,
            )
        time.sleep(5)


if __name__ == "__main__":
    main()
