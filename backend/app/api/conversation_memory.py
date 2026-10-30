"""Session memory inspection and explicit corrections. Author: heimoxian."""
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.common.response import Response
from app.common.exceptions import BusinessException
from app.models.chapter import Chapter
from app.models.agent_session import AgentSession
from app.models.conversation_memory import ConversationDigest
from app.services import conversation_memory_service as memory, agent_context_service, agent_service, reader_progress_service

router = APIRouter(prefix="/books", tags=["会话记忆"])


def context(db, book_id, spoilers):
    agent_context_service.book_or_404(db, book_id)
    progress = reader_progress_service.get(db, book_id)
    safe = progress.get("chapter_idx", 0)
    if spoilers:
        safe = max((c.idx for c in db.query(Chapter).filter_by(book_id=book_id, kind="main").all()), default=0)
    return safe, "spoilers" if spoilers else "reading"


@router.get("/{book_id}/agent/memory")
def read(book_id: int, spoilers: bool = False, db: Session = Depends(get_db)):
    safe, scope = context(db, book_id, spoilers)
    session = db.query(AgentSession).filter_by(book_id=book_id).first()
    if not session:
        return Response.success(data={"items": [], "digests": [], "sources": []})
    rows = memory.messages(db, session.id, safe, scope)
    available = {r.id for r in rows}
    explicit = memory.memories(db, session.id, safe, scope)
    suppressed = {i for r in explicit for i in r.sources}
    digests = db.query(ConversationDigest).filter_by(session_id=session.id, scope=scope).filter(
        ConversationDigest.safe_chapter <= safe).all()
    return Response.success(data={
        "items": [{"id": r.id, "kind": r.kind, "content": r.content, "sources": r.sources,
                   "status": r.status} for r in explicit],
        "digests": [{"id": d.id, "sources": d.sources, "model": d.model,
                     "items": [i for i in d.content if not set(i["sources"]) & suppressed]}
                    for d in digests if set(d.sources).issubset(available)],
        "sources": [{"id": r.id, "role": r.role, "content": r.content} for r in rows],
    })


class Edit(BaseModel):
    kind: Literal["preference", "agreement", "hypothesis", "question", "correction"]
    content: str = Field(min_length=1, max_length=1200)
    sources: list[int] = Field(default_factory=list, max_length=50)
    supersedes: int | None = None
    forget: bool = False
    spoilers: bool = False


@router.post("/{book_id}/agent/memory")
def edit(book_id: int, payload: Edit, db: Session = Depends(get_db)):
    lock = agent_service._book_lock(book_id)
    if not lock.acquire(blocking=False):
        raise BusinessException(message="请等待当前回复完成后再更正记忆", code=4090)
    try:
        safe, scope = context(db, book_id, payload.spoilers)
        session = agent_context_service.get_or_create_session(db, book_id)
        row = memory.edit(db, session.id, safe, scope, payload.kind, payload.content,
                          payload.sources, payload.supersedes, payload.forget)
        return Response.success(data={"id": row.id})
    finally:
        lock.release()
