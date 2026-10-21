"""章节正文与段落偏移测试。"""

from __future__ import annotations

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.services import text_window_service

SOURCE = (
    "第1章 开始\n"
    "正文第一段。\n"
    "第二段。\n"
    "第2章 继续\n"
    "第二章正文。"
)


def _make_book(db) -> int:
    text_window_service.clear_cache()
    book = Book(
        title="正文测试",
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="content.txt",
        encoding="UTF-8",
        char_count=len(SOURCE),
        chapter_count=2,
        main_chapter_count=2,
    )
    db.add(book)
    db.commit()
    path = UPLOADS_DIR / str(book.id) / "source.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE, encoding="utf-8")
    split = SOURCE.index("第2章")
    db.add(
        Chapter(
            book_id=book.id,
            idx=0,
            title="第1章 开始",
            kind="main",
            char_start=0,
            char_end=split,
            word_count=split,
        )
    )
    db.add(
        Chapter(
            book_id=book.id,
            idx=1,
            title="第2章 继续",
            kind="main",
            char_start=split,
            char_end=len(SOURCE),
            word_count=len(SOURCE) - split,
        )
    )
    db.commit()
    return book.id


def test_chapter_content_strips_title():
    with SessionLocal() as db:
        book_id = _make_book(db)
        data = text_window_service.get_chapter_content(db, book_id, 0)
        assert data["title"] == "第1章 开始"
        assert data["text"] == "正文第一段。\n第二段。"
        assert data["char_start"] == len("第1章 开始\n")
        assert data["char_end"] == SOURCE.index("第2章")
        assert [item["text"] for item in data["paragraphs"]] == [
            "正文第一段。",
            "第二段。",
        ]


def test_paragraph_offsets_match_source():
    with SessionLocal() as db:
        book_id = _make_book(db)
        for idx in (0, 1):
            data = text_window_service.get_chapter_content(db, book_id, idx)
            for paragraph in data["paragraphs"]:
                start = paragraph["offset"]
                end = start + len(paragraph["text"])
                assert SOURCE[start:end] == paragraph["text"]


def test_chapter_content_rejects_missing():
    with SessionLocal() as db:
        book_id = _make_book(db)
        try:
            text_window_service.get_chapter_content(db, book_id, 99)
        except Exception as exc:
            assert getattr(exc, "code", None) == 4040
        else:
            raise AssertionError("缺少章节应当报错")

        try:
            text_window_service.get_chapter_content(db, 99999, 0)
        except Exception as exc:
            assert getattr(exc, "code", None) == 4040
        else:
            raise AssertionError("缺少书籍应当报错")


def test_chapter_content_api(client):
    with SessionLocal() as db:
        book_id = _make_book(db)
    response = client.get(f"/api/books/{book_id}/chapters/1")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["idx"] == 1
    assert data["content"] == "第二章正文。"
    assert data["paragraphs"][0]["offset"] == SOURCE.index("第二章正文")

    missing = client.get(f"/api/books/{book_id}/chapters/99")
    assert missing.status_code == 200
    assert missing.json()["code"] == 4040
