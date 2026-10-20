"""Owner-only research workspace, history library and adjustable research ceilings."""

import math
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime

from . import discovery, discovery_journal, history_store, market, quant_features
from .auth import CurrentOwner, Db
from .discovery_contracts import DiscoverySettingsSave, DiscoveryStart, HistoryLoad
from .market_contracts import Interval

router = APIRouter(prefix="/api/v1/research/discovery", tags=["strategy discovery"])


@router.get("")
def state(db: Db, owner: CurrentOwner, offset: int = Query(default=0, ge=0, le=100000)) -> dict:
    return discovery.state(db, owner, offset)


@router.put("/settings")
def settings(body: DiscoverySettingsSave, db: Db, owner: CurrentOwner) -> dict:
    return discovery.save_settings(db, owner, body)


@router.post("/cycles", status_code=201)
def start(body: DiscoveryStart, db: Db, owner: CurrentOwner) -> dict:
    return discovery.start(db, owner, body).model_dump(mode="json")


@router.post("/history", status_code=201)
def history(body: HistoryLoad, db: Db, owner: CurrentOwner) -> dict:
    return history_store.request_load(db, owner, body).model_dump(mode="json")


@router.get("/history")
def saved(
    db: Db,
    owner: CurrentOwner,
    instrument_id: str,
    interval: Interval,
    start: AwareDatetime,
    end: AwareDatetime,
    maximum: int = Query(default=750, ge=10, le=1500),
    source_key: str | None = Query(default=None, pattern="^[a-f0-9]{64}$"),
) -> dict:
    spec = market.instrument(db, instrument_id)
    if (
        start >= end
        or (end - start).total_seconds() > 1500 * 86400
        or (end - start).total_seconds() / market.INTERVAL_SECONDS[interval] > 100000
    ):
        from fastapi import HTTPException

        raise HTTPException(
            422, "Inspect at most 1,500 days and 100,000 calendar intervals at once"
        )
    left, right = history_store.bounds(spec, interval, start, end)
    value = history_store.read(
        db, owner, instrument_id, interval, left, right, key=source_key, require_complete=False
    )
    bars = value["candles"]
    stride = max(1, math.ceil(len(bars) / maximum))
    value["candles"] = bars[::stride]
    if bars and value["candles"][-1] != bars[-1]:
        value["candles"].append(bars[-1])
    return {**value, "total_bars": len(bars), "preview": stride > 1, "network_calls": 0}


@router.get("/records/{record_id}")
def record(record_id: UUID, db: Db, owner: CurrentOwner) -> dict:
    return discovery_journal.view(discovery_journal.owned(db, owner, record_id)).model_dump(
        mode="json"
    )


@router.post("/records/{record_id}/{action}")
def control(
    record_id: UUID, action: Literal["pause", "resume", "cancel"], db: Db, owner: CurrentOwner
) -> dict:
    return discovery.control(db, owner, record_id, action).model_dump(mode="json")


@router.get("/records/{record_id}/prices")
def prices(record_id: UUID, db: Db, owner: CurrentOwner) -> dict:
    from . import research

    row = discovery_journal.owned(db, owner, record_id)
    if row.kind not in {"trial", "confirmation"}:
        from fastapi import HTTPException

        raise HTTPException(422, "Choose a recorded strategy experiment")
    if not row.payload.get("dataset_id"):
        return {"candles": [], "network_calls": 0}
    source = research.owned(db, owner, row.payload["dataset_id"], "dataset")
    cutoff = quant_features.stamp(
        row.payload["input"].get("holdout_start") or row.payload["validation_end"]
    )
    ending = quant_features.stamp(row.payload["test_end"])
    return {
        "candles": [
            b
            for b in source.payload["candles"]
            if quant_features.stamp(b["open_time"]) >= cutoff
            and quant_features.stamp(b["close_time"]) <= ending
        ],
        "network_calls": 0,
        "dataset_checksum": source.checksum,
        "interval": source.payload["interval"],
    }
