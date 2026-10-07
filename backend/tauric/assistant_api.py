from fastapi import APIRouter

from . import assistant
from .assistant_contracts import AssistantState
from .auth import CurrentOwner, Db

router = APIRouter(prefix="/api/v1/assistant", tags=["owner assistant"])


@router.get("", response_model=AssistantState)
def state(db: Db, owner: CurrentOwner):
    return assistant.state(db, owner)
