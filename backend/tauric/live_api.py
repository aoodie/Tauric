"""Owner-only portfolio observations and exact-version forward validation."""

from uuid import UUID

from fastapi import APIRouter

from . import live_trading, operations, valuation
from .auth import CurrentOwner, Db
from .live_contracts import (
    ForwardRegister,
    ReadinessAction,
    ReadinessRecordView,
    TradingReadinessView,
)
from .operations_contracts import OperatingStateView

router = APIRouter(prefix="/api/v1/trading-readiness", tags=["trading readiness"])


@router.get("", response_model=TradingReadinessView)
def state(db: Db, owner: CurrentOwner):
    return live_trading.state(db, owner)


@router.get("/operations", response_model=OperatingStateView)
def operating_state(db: Db, owner: CurrentOwner):
    return operations.state(db, owner)


@router.post("/valuation", response_model=ReadinessRecordView)
def capture(body: ReadinessAction, db: Db, owner: CurrentOwner):
    return live_trading.record_view(valuation.capture(db, owner, body))


@router.post("/forward", response_model=ReadinessRecordView, status_code=201)
def register(body: ForwardRegister, db: Db, owner: CurrentOwner):
    return live_trading.register(db, owner, body)


@router.post("/forward/{protocol_id}/evaluate", response_model=ReadinessRecordView)
def evaluate(protocol_id: UUID, body: ReadinessAction, db: Db, owner: CurrentOwner):
    return live_trading.evaluate(db, owner, protocol_id, body)
