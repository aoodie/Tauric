"""Independent valuation lane so market reads do not delay protection and exits."""

import signal
import time

from sqlalchemy.orm import Session

from . import valuation
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
                row = db.get(WorkerStatus, "valuation")
                if row:
                    row.heartbeat_at = now()
                else:
                    db.add(WorkerStatus(id="valuation", heartbeat_at=now(), version="0.12.0"))
                db.commit()
            with Session(engine()) as db:
                valuation.sample_one(db)
        except Exception:
            # Never print response bodies, credentials, SQL or connection URLs.
            print(
                "Valuation observation failed; inspect incomplete account marks and storage readiness.",
                flush=True,
            )
        time.sleep(5)


if __name__ == "__main__":
    main()
