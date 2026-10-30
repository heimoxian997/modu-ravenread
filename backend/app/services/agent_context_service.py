"""伴读 agent 的上下文、闸门与会话读写。

章级闸门：读者在第 N 章，就视为第 N 章已读完；所有工具只按章节截断，
不再按滚动光标裁正文。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.common.exceptions import BusinessException
from app.models.agent_message import AgentMessage
from app.models.agent_session import AgentSession
from app.models.book import Book
from app.models.chapter import Chapter

HISTORY_LIMIT = 6


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def book_or_404(db: Session, book_id: int) -> Book:
    return _book_or_404(db, book_id)


def get_chapter_or_404(
    db: Session, book_id: int, chapter_idx: int
) -> Chapter:
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.idx == int(chapter_idx))
        .first()
    )
    if not chapter:
        raise BusinessException(message="章节不存在", code=4040)
    return chapter


def safe_chapter(
    db: Session, book_id: int, chapter_idx: int, char_offset: int = 0
) -> int:
    """章级闸门：当前章即视为已读；char_offset 保留兼容，不再参与判断。"""
    get_chapter_or_404(db, book_id, chapter_idx)
    return max(0, int(chapter_idx))


def book_meta(book: Book) -> dict[str, Any]:
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "synopsis": book.synopsis,
        "protagonists": list(book.protagonists or []),
        "main_chapter_count": book.main_chapter_count,
    }


def chapter_meta(chapter: Chapter) -> dict[str, Any]:
    return {
        "idx": chapter.idx,
        "title": chapter.title,
        "kind": chapter.kind,
        "char_start": chapter.char_start,
        "char_end": chapter.char_end,
    }


def get_or_create_session(
    db: Session, book_id: int, session_id: int | None = None
) -> AgentSession:
    _book_or_404(db, book_id)
    if session_id is not None:
        session = db.get(AgentSession, int(session_id))
        if not session or session.book_id != book_id:
            raise BusinessException(message="会话不存在", code=4040)
        return session
    session = (
        db.query(AgentSession)
        .filter(AgentSession.book_id == book_id)
        .first()
    )
    if session:
        return session
    session = AgentSession(book_id=book_id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def history(
    db: Session, session_id: int, safe: int, limit: int = HISTORY_LIMIT
) -> list[dict[str, Any]]:
    """只带安全章及以前的对话，避免读者回跳时泄露后文。"""
    rows = (
        db.query(AgentMessage)
        .filter(
            AgentMessage.session_id == session_id,
            AgentMessage.role.in_(("user", "assistant")),
            AgentMessage.safe_chapter.isnot(None),
            AgentMessage.safe_chapter <= int(safe),
        )
        .order_by(AgentMessage.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {"role": row.role, "content": row.content}
        for row in reversed(rows)
    ]


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def list_messages(
    db: Session, session_id: int, limit: int = 50, safe: int | None = None, scope: str = "reading"
) -> list[dict[str, Any]]:
    query = db.query(AgentMessage).filter(AgentMessage.session_id == session_id)
    query = query.filter(func.coalesce(AgentMessage.meta["scope"].as_string(), "reading") == scope)
    if safe is not None:
        query = query.filter(AgentMessage.safe_chapter.isnot(None), AgentMessage.safe_chapter <= safe)
    rows = (
        query
        .order_by(AgentMessage.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "position_chapter": row.position_chapter,
            "position_offset": row.position_offset,
            "safe_chapter": row.safe_chapter,
            "meta": row.meta,
            "create_time": _iso(row.create_time),
        }
        for row in reversed(rows)
    ]


def save_message(
    db: Session,
    session_id: int,
    role: str,
    content: str,
    chapter_idx: int,
    char_offset: int,
    safe: int,
    meta: dict[str, Any] | None = None,
) -> AgentMessage:
    row = AgentMessage(
        session_id=session_id,
        role=role,
        content=content,
        position_chapter=chapter_idx,
        position_offset=char_offset,
        safe_chapter=safe,
        meta=meta,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def clear_session(db: Session, book_id: int) -> None:
    session = (
        db.query(AgentSession)
        .filter(AgentSession.book_id == book_id)
        .first()
    )
    if not session:
        return
    db.delete(session)
    db.commit()
