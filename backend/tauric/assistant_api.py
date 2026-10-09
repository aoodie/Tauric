from fastapi import APIRouter, Query

from . import assistant
from .assistant_contracts import (
    ActivityObservationRequest,
    AssistantActivityFeed,
    AssistantActivityItem,
    AssistantState,
)
from .auth import CurrentOwner, Db

router = APIRouter(prefix="/api/v1/assistant", tags=["owner assistant"])


@router.get("", response_model=AssistantState)
def state(db: Db, owner: CurrentOwner):
    return assistant.state(db, owner)


@router.get("/activities", response_model=AssistantActivityFeed)
def activities(
    db: Db,
    owner: CurrentOwner,
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=400),
):
    return assistant.activity_stream(db, owner, limit, cursor)


@router.post("/activities/observe", response_model=list[AssistantActivityItem])
def observe(body: ActivityObservationRequest, db: Db, owner: CurrentOwner):
    from .assistant_activity import observations

    return observations(db, owner, body.identities)
