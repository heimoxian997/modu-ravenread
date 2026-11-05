"""伴读 agent 的会话、能力与对话接口。"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.models.agent_session import AgentSession
from app.models.book import Book
from app.schemas.agent import AgentChatRequest, AgentToolRequest
from app.services import (
    agent_capability_service,
    agent_context_service,
    agent_service,
    agent_tool_service,
    reader_progress_service,
)

router = APIRouter(prefix="/books", tags=["伴读"])


@router.get("/{book_id}/agent/model-capabilities")
def model_capabilities(book_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    from app.services import model_tool_service
    agent_context_service.book_or_404(db, book_id)
    caps = agent_capability_service.detect(db, book_id)
    return Response.success(data={"enabled":caps.enabled, "chapter_numbering":"1-based",
        "tools":[s["name"] for s in model_tool_service.specs(available=caps.available_tools())] if caps.enabled else []})


@router.post("/{book_id}/agent/model-tool")
def run_model_tool(payload: AgentToolRequest, book_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    from app.services import model_tool_service
    book = agent_context_service.book_or_404(db, book_id)
    progress = reader_progress_service.get(db, book_id)
    if not progress.get("available"):
        raise BusinessException(message="请先在阅读器打开这本书",code=4000)
    chapter = agent_context_service.get_chapter_or_404(db,book_id,progress["chapter_idx"])
    # Notes and navigation require no vector capability detection.
    basic = {"note_query", "note_upsert", "list_storylines", "report_correction",
             "find_note_duplicates", "delete_storyline", "restore_storyline", "list_deleted_storylines", "refresh_storyline_evidence",
             "get_toc", "read_chapter", "read_chapters", "read_passage", "read_around"}
    caps = None if payload.tool in basic else agent_capability_service.detect(db,book_id)
    ctx = agent_tool_service.ToolContext(db=db,book=book,chapter=chapter,safe_chapter=chapter.idx,
        char_offset=progress["char_offset"],disabled_tools=caps.disabled_tools() if caps else None,capabilities=caps)
    return Response.success(data={**model_tool_service.execute(payload.tool,payload.args,ctx),
        "book_id":book_id,"safe_chapter":chapter.idx+1,"chapter_numbering":"1-based"})


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
    chapter_idx: int | None = Query(default=None, ge=0),
    spoilers: bool = False,
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
    progress = reader_progress_service.get(db, book_id)
    safe = chapter_idx if chapter_idx is not None else progress.get("chapter_idx")
    if spoilers:
        from app.models.chapter import Chapter
        safe = max((r.idx for r in db.query(Chapter).filter_by(book_id=book_id, kind="main").all()), default=0)
    if safe is not None:
        agent_context_service.get_chapter_or_404(db, book_id, safe)
    return Response.success(
        data={
            "session_id": session.id,
            "book_id": book_id,
            "messages": agent_context_service.list_messages(db, session.id, safe=safe, scope="spoilers" if spoilers else "reading"),
        }
    )


@router.post(
    "/{book_id}/agent/tool",
    summary="执行单个伴读工具（外部宿主 / MCP 用）",
)
def run_agent_tool(
    payload: AgentToolRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    agent_context_service.book_or_404(db, book_id)
    progress = reader_progress_service.get(db, book_id)
    if not progress.get("available"):
        raise BusinessException(
            message="请先在阅读器打开这本书，再让外部助手查询。",
            code=4000,
        )
    chapter = agent_context_service.get_chapter_or_404(
        db, book_id, progress["chapter_idx"]
    )
    caps = agent_capability_service.detect(db, book_id)
    if not caps.enabled:
        raise BusinessException(
            message="这本书还没有可用于伴读的切块、摘要或人物数据，伴读已关闭。",
            code=4000,
        )
    book = db.get(Book, book_id)
    ctx = agent_tool_service.ToolContext(
        db=db,
        book=book,
        chapter=chapter,
        safe_chapter=progress["chapter_idx"],
        char_offset=progress["char_offset"],
        disabled_tools=caps.disabled_tools(),
        capabilities=caps,
    )
    result = agent_tool_service.execute(payload.tool, payload.args, ctx)
    return Response.success(
        data={
            **result,
            "book_id": book_id,
            "chapter_idx": progress["chapter_idx"],
            "char_offset": progress["char_offset"],
            "available_tools": sorted(caps.available_tools()),
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
        spoilers=payload.spoilers,
        request_key=payload.request_key,
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
    lock = agent_service._book_lock(book_id)
    if not lock.acquire(blocking=False):
        raise BusinessException(message="请等待回复结束或取消后再清空", code=4090)
    try:
        agent_context_service.clear_session(db, book_id)
    finally:
        lock.release()
    return Response.success(message="会话已清空", data={"cleared": True})


@router.delete("/{book_id}/agent/requests/{request_key}")
def cancel_request(book_id: int, request_key: str, db: Session = Depends(get_db)):
    from app.models.conversation_memory import ConversationRequest
    count = db.query(ConversationRequest).filter_by(book_id=book_id, request_key=request_key,
        status="running").update({"status": "cancelled"})
    db.commit()
    return Response.success(data={"cancelled": bool(count)})
