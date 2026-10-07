"""Optional local collector. Memory failures never fail a trading or chat job."""

import signal
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import honcho_memory, knowledge
from .database import engine
from .knowledge_models import MemorySyncState
from .models import Owner, now

STOP = False


def stop(*_):
    global STOP
    STOP = True


def cycle():
    with Session(engine()) as db:
        owners = list(db.scalars(select(Owner)))
        for owner in owners:
            config, _ = knowledge.settings(db, owner.id)
            if not config.enabled:
                continue
            try:
                knowledge.collect(db, owner)
                knowledge.import_vault(db, owner)
                knowledge.export_vault(db, owner)
                honcho_memory.sync(db, owner)
            except Exception:
                db.rollback()
                state = db.get(MemorySyncState, owner.id)
                if state:
                    state.last_error = "Local memory sync needs attention. Wiki copies remain available; check the local Honcho service and model."
                    state.heartbeat_at = now()
                    db.commit()


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not STOP:
        try:
            cycle()
        except Exception:
            print("Local memory storage unavailable; waiting to reconnect.", flush=True)
        for _ in range(30):
            if STOP:
                return
            time.sleep(1)


if __name__ == "__main__":
    main()
