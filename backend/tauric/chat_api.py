from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select

from . import chat, portfolios
from .auth import CurrentOwner, Db
from .chat_contracts import (
    ChatConfigChange,
    ChatConfigView,
    ChatSend,
    ChatTurnView,
    ConversationView,
)
from .models import ChatTurn

router = APIRouter(prefix="/api/v1/chat")


@router.get("/settings", response_model=ChatConfigView)
def settings(db: Db, owner: CurrentOwner):
    return chat.config_view(db, owner)


@router.put("/settings", response_model=ChatConfigView)
def save_settings(body: ChatConfigChange, db: Db, owner: CurrentOwner):
    return chat.save_config(db, owner, body)


@router.get("/conversations", response_model=list[ConversationView])
def conversations(db: Db, owner: CurrentOwner, portfolio_id: UUID | None = None):
    if portfolio_id:
        portfolios.resolve(db, owner, portfolio_id)
    latest = db.execute(
        select(ChatTurn.conversation_id, func.max(ChatTurn.created_at))
        .where(
            ChatTurn.owner_id == owner.id,
            ChatTurn.portfolio_id == (str(portfolio_id) if portfolio_id else None),
        )
        .group_by(ChatTurn.conversation_id)
        .order_by(func.max(ChatTurn.created_at).desc())
        .limit(50)
    ).all()
    return [
        ConversationView(
            id=key,
            portfolio_id=str(portfolio_id) if portfolio_id else None,
            updated_at=stamp,
            title=db.scalar(
                select(ChatTurn.user_text)
                .where(ChatTurn.owner_id == owner.id, ChatTurn.conversation_id == key)
                .order_by(ChatTurn.created_at)
                .limit(1)
            )[:70],
        )
        for key, stamp in latest
    ]


@router.get("/conversations/{conversation_id}", response_model=list[ChatTurnView])
def turns(conversation_id: UUID, db: Db, owner: CurrentOwner, portfolio_id: UUID | None = None):
    if portfolio_id:
        portfolios.resolve(db, owner, portfolio_id)
    rows = list(
        db.scalars(
            select(ChatTurn)
            .where(
                ChatTurn.owner_id == owner.id,
                ChatTurn.conversation_id == str(conversation_id),
                ChatTurn.portfolio_id == (str(portfolio_id) if portfolio_id else None),
            )
            .order_by(ChatTurn.created_at.desc())
            .limit(60)
        )
    )
    return [chat.view(db, row) for row in reversed(rows)]


@router.post("/messages", response_model=ChatTurnView, status_code=202)
def send(body: ChatSend, db: Db, owner: CurrentOwner):
    return chat.enqueue(db, owner, body)
