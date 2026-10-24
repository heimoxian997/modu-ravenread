"""阅读位置读写。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.reader_progress import ReaderProgress


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _chapter_or_404(
    db: Session, book_id: int, chapter_idx: int
) -> Chapter:
    chapter = (
        db.query(Chapter)
        .filter(
            Chapter.book_id == book_id,
            Chapter.idx == int(chapter_idx),
        )
        .first()
    )
    if not chapter:
        raise BusinessException(message="章节不存在", code=4040)
    return chapter


def _row(db: Session, book_id: int) -> ReaderProgress | None:
    return (
        db.query(ReaderProgress)
        .filter(ReaderProgress.book_id == book_id)
        .first()
    )


def _to_dict(
    db: Session, row: ReaderProgress | None, book: Book | None = None
) -> dict[str, Any]:
    if row is None:
        return {"available": False}
    book = book or db.get(Book, row.book_id)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == row.book_id, Chapter.idx == row.chapter_idx)
        .first()
    )
    return {
        "available": True,
        "book_id": row.book_id,
        "book_title": book.title if book else "",
        "chapter_idx": row.chapter_idx,
        "chapter_title": chapter.title if chapter else "",
        "char_offset": row.char_offset,
        "safe_chapter": row.chapter_idx,
        "update_time": row.update_time.isoformat(sep=" ", timespec="seconds")
        if row.update_time
        else None,
    }


def get(db: Session, book_id: int) -> dict[str, Any]:
    book = _book_or_404(db, book_id)
    return _to_dict(db, _row(db, book_id), book)


def put(
    db: Session, book_id: int, chapter_idx: int, char_offset: int
) -> dict[str, Any]:
    book = _book_or_404(db, book_id)
    chapter = _chapter_or_404(db, book_id, chapter_idx)
    offset = max(0, min(int(char_offset), max(0, chapter.char_end)))
    row = _row(db, book_id)
    if row is None:
        row = ReaderProgress(
            book_id=book_id,
            chapter_idx=int(chapter_idx),
            char_offset=offset,
        )
        db.add(row)
    else:
        row.chapter_idx = int(chapter_idx)
        row.char_offset = offset
    db.commit()
    db.refresh(row)
    return _to_dict(db, row, book)


def latest(db: Session) -> dict[str, Any]:
    row = (
        db.query(ReaderProgress)
        .order_by(ReaderProgress.update_time.desc(), ReaderProgress.id.desc())
        .first()
    )
    return _to_dict(db, row)
