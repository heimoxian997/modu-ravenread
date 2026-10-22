"""伴读 agent 的会话、能力与对话接口。"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.models.agent_session import AgentSession
from app.schemas.agent import AgentChatRequest
from app.services import (
    agent_capability_service,
    agent_context_service,
    agent_service,
)

router = APIRouter(prefix="/books", tags=["伴读"])


@router.get(
    "/{book_id}/agent/capabilities",
    summary="伴读能力探测",
)
def get_agent_capabilities(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    agent_context_service.book_or_404(db, book_id)
    caps = agent_capability_service.detect(db, book_id)
    return Response.success(
        data={
            "enabled": caps.enabled,
            "tools": sorted(caps.available_tools()),
            "features": {
                "chunks": caps.chunks,
                "text_index": caps.text_index,
                "summary_index": caps.summary_index,
                "summaries": caps.summaries,
                "character_cards": caps.character_cards,
                "alias_layer": caps.alias_layer,
                "character_index": caps.character_index,
                "lore_index": caps.lore_index,
            },
        }
    )


@router.get(
    "/{book_id}/agent/session",
    summary="伴读会话与消息",
)
def get_agent_session(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    agent_context_service.book_or_404(db, book_id)
    session = (
        db.query(AgentSession)
        .filter(AgentSession.book_id == book_id)
        .first()
    )
    if not session:
        return Response.success(
            data={"session_id": None, "book_id": book_id, "messages": []}
        )
    return Response.success(
        data={
            "session_id": session.id,
            "book_id": book_id,
            "messages": agent_context_service.list_messages(db, session.id),
        }
    )


@router.post(
    "/{book_id}/agent/chat",
    summary="向伴读 agent 提问",
)
def chat(
    payload: AgentChatRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    data = agent_service.chat(
        db,
        book_id,
        payload.message,
        payload.chapter_idx,
        payload.char_offset,
        session_id=payload.session_id,
        provider_id=payload.provider_id,
    )
    return Response.success(data=data)


@router.delete(
    "/{book_id}/agent/session",
    summary="清空伴读会话",
)
def clear_agent_session(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    agent_context_service.book_or_404(db, book_id)
    agent_context_service.clear_session(db, book_id)
    return Response.success(message="会话已清空", data={"cleared": True})
