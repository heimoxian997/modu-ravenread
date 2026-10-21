"""书库、书籍详情与章节正文业务。"""

from __future__ import annotations

import shutil
from typing import Any

from sqlalchemy import asc
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.models.book import Book
from app.models.chapter import Chapter
from app.schemas.draft import BookPatch


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _chapter_stats(chapters: list[Chapter]) -> dict[str, Any]:
    if not chapters:
        return {
            "avg_word_count": 0,
            "min_word_count": 0,
            "max_word_count": 0,
            "shortest_chapter": None,
            "longest_chapter": None,
        }

    shortest = min(chapters, key=lambda item: item.word_count)
    longest = max(chapters, key=lambda item: item.word_count)
    return {
        "avg_word_count": round(
            sum(item.word_count for item in chapters) / len(chapters)
        ),
        "min_word_count": shortest.word_count,
        "max_word_count": longest.word_count,
        "shortest_chapter": {
            "idx": shortest.idx,
            "title": shortest.title,
            "word_count": shortest.word_count,
            "kind": shortest.kind,
        },
        "longest_chapter": {
            "idx": longest.idx,
            "title": longest.title,
            "word_count": longest.word_count,
            "kind": longest.kind,
        },
    }


def _book_summary(book: Book):
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "chapter_count": book.chapter_count,
        "main_chapter_count": book.main_chapter_count,
        "extra_chapter_count": max(0, book.chapter_count - book.main_chapter_count),
        "char_count": book.char_count,
        "create_time": book.create_time,
    }


def _book_detail(db: Session, book: Book):
    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book.id)
        .order_by(asc(Chapter.idx))
        .all()
    )
    return {
        "id": book.id,
        "title": book.title,
        "author": book.author,
        "synopsis": book.synopsis,
        "protagonists": book.protagonists or [],
        "filename": book.filename,
        "encoding": book.encoding,
        "char_count": book.char_count,
        "chapter_count": book.chapter_count,
        "main_chapter_count": book.main_chapter_count,
        "extra_chapter_count": max(0, book.chapter_count - book.main_chapter_count),
        "front_matter_chars": len(book.front_matter or ""),
        "create_time": book.create_time,
        "update_time": book.update_time,
        **_chapter_stats(chapters),
    }


def get_book_list(db: Session):
    books = db.query(Book).order_by(Book.create_time.desc(), Book.id.desc()).all()
    return [_book_summary(book) for book in books]


def get_book_detail(db: Session, book_id: int):
    book = _book_or_404(db, book_id)
    return _book_detail(db, book)


def get_chapter_list(db: Session, book_id: int):
    book = _book_or_404(db, book_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book.id)
        .order_by(asc(Chapter.idx))
        .all()
    )
    return [
        {
            "idx": item.idx,
            "title": item.title,
            "kind": item.kind,
            "word_count": item.word_count,
            "start": item.char_start,
            "end": item.char_end,
        }
        for item in chapters
    ]


def get_chapter_content(db: Session, book_id: int, idx: int):
    """按偏移读取章节正文，去掉首行标题并给出段落偏移。"""
    from app.services import text_window_service

    data = text_window_service.get_chapter_content(db, book_id, idx)
    return {
        "idx": data["chapter_idx"],
        "title": data["title"],
        "kind": data["kind"],
        "content": data["text"],
        "char_start": data["char_start"],
        "char_end": data["char_end"],
        "paragraphs": data["paragraphs"],
    }


def update_book(db: Session, book_id: int, payload: BookPatch):
    book = _book_or_404(db, book_id)
    data = payload.model_dump(exclude_unset=True)

    if "title" in data and data["title"] is not None:
        book.title = data["title"]
    if "author" in data:
        book.author = data["author"]
    if "synopsis" in data:
        book.synopsis = data["synopsis"]
    if "protagonists" in data:
        book.protagonists = data["protagonists"] or []

    db.commit()
    db.refresh(book)
    return _book_detail(db, book)


def delete_book(db: Session, book_id: int):
    """删除书籍、章节与源文件目录。"""
    book = _book_or_404(db, book_id)
    upload_dir = (UPLOADS_DIR / str(book.id)).resolve()
    if UPLOADS_DIR.resolve() not in upload_dir.parents:
        raise BusinessException(message="书籍文件路径异常", code=4100)

    db.delete(book)
    db.commit()
    shutil.rmtree(upload_dir, ignore_errors=True)
