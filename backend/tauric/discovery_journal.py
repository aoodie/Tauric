"""Small research journal; service jobs remain authoritative for their execution."""

from fastapi import HTTPException
from sqlalchemy import select

from . import paper, services
from .discovery_contracts import DiscoveryRecordView
from .discovery_models import DiscoveryRecord
from .models import Owner

TERMINAL = {"completed", "failed", "cancelled", "rejected", "screen_passed"}


def checked(row):
    if not row or paper.checksum(row.payload) != row.checksum:
        raise HTTPException(409, "Research discovery evidence integrity failed")
    return row


def view(row):
    checked(row)
    return DiscoveryRecordView(
        id=row.id,
        kind=row.kind,
        parent_id=row.parent_id,
        checksum=row.checksum,
        created_at=row.created_at,
        payload=row.payload,
    )


def owned(db, owner, identity, kind=None):
    row = db.get(DiscoveryRecord, str(identity))
    if not row or row.owner_id != owner.id or (kind and row.kind != kind):
        raise HTTPException(404, "This research discovery record does not exist")
    return checked(row)


def lock(db, owner):
    db.scalar(select(Owner.id).where(Owner.id == owner.id).with_for_update())


def repeated(db, owner, kind, request_id, request):
    lock(db, owner)
    row = db.scalar(
        select(DiscoveryRecord).where(
            DiscoveryRecord.owner_id == owner.id,
            DiscoveryRecord.kind == kind,
            DiscoveryRecord.request_id == str(request_id),
        )
    )
    if row and checked(row).payload["request"] != request:
        raise HTTPException(409, "This request identifier belongs to different research inputs")
    return row


def append(db, owner, kind, request_id, request, *, inputs=None, parent_id=None, **state):
    row = DiscoveryRecord(
        owner_id=owner.id,
        kind=kind,
        request_id=str(request_id),
        parent_id=parent_id,
        payload={"request": request, "input": inputs or {}, **state},
        checksum="",
    )
    row.checksum = paper.checksum(row.payload)
    db.add(row)
    db.flush()
    services.audit(
        db,
        owner,
        "research.discovery_" + kind,
        "research_discovery",
        row.id,
        request.get("reason", "Record research work"),
        {"checksum": row.checksum, "authority": "research_only"},
    )
    return row


def update(row, **state):
    checked(row)
    if row.payload.get("status") in TERMINAL or {"request", "input"} & state.keys():
        raise HTTPException(409, "Frozen or terminal research evidence cannot change")
    row.payload = {**row.payload, **state}
    row.checksum = paper.checksum(row.payload)
