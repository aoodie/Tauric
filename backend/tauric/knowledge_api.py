from uuid import UUID

from fastapi import APIRouter, Query

from . import knowledge
from .auth import CurrentOwner, Db
from .knowledge_contracts import (
    KnowledgeKind,
    KnowledgeNote,
    KnowledgeSearch,
    KnowledgeView,
    KnowledgeVisibility,
    MemorySettingsChange,
    MemoryState,
)

router = APIRouter(prefix="/api/v1/knowledge", tags=["knowledge and memory"])


@router.get("/settings", response_model=MemoryState)
def settings(db: Db, owner: CurrentOwner):
    return knowledge.state(db, owner)


@router.put("/settings", response_model=MemoryState)
def save_settings(body: MemorySettingsChange, db: Db, owner: CurrentOwner):
    return knowledge.save_settings(db, owner, body)


@router.get("", response_model=KnowledgeSearch)
def search(
    db: Db,
    owner: CurrentOwner,
    query: str = Query(default="", max_length=300),
    kind: KnowledgeKind | None = None,
    include_hidden: bool = False,
    offset: int = Query(default=0, ge=0, le=100000),
):
    return knowledge.search(db, owner, query, kind, include_hidden, offset=offset)


@router.post("/collect")
def collect(db: Db, owner: CurrentOwner):
    return {"copied": knowledge.collect(db, owner), "authority": "memory_context_only"}


@router.post("/notes", response_model=KnowledgeView)
def save_note(body: KnowledgeNote, db: Db, owner: CurrentOwner):
    return knowledge.save_note(db, owner, body)


@router.get("/{document_id}", response_model=KnowledgeView)
def document(document_id: UUID, db: Db, owner: CurrentOwner):
    return knowledge.view(knowledge.owned(db, owner, document_id))


@router.put("/{document_id}", response_model=KnowledgeView)
def edit_note(document_id: UUID, body: KnowledgeNote, db: Db, owner: CurrentOwner):
    return knowledge.save_note(db, owner, body, document_id)


@router.put("/{document_id}/visibility", response_model=KnowledgeView)
def visibility(document_id: UUID, body: KnowledgeVisibility, db: Db, owner: CurrentOwner):
    return knowledge.visibility(db, owner, document_id, body)
