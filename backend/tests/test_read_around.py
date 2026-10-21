"""原文窗口与扫名测试。"""

from __future__ import annotations

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.chunk import Chunk
from app.services import text_window_service
from app.services.chunk_service import CHUNK_VERSION

TITLE = "第1章 开始"
PARAGRAPHS = [
    "韩砚清走进院子。姐姐在缝衣。",
    "她抬头看了一眼。韩砚清没有说话。",
    "姐姐放下针线。",
]
SOURCE = TITLE + "\n" + "\n".join(PARAGRAPHS)


def _make_book(db, with_chunks: bool = True) -> int:
    text_window_service.clear_cache()
    book = Book(
        title="窗口测试",
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="window.txt",
        encoding="UTF-8",
        char_count=len(SOURCE),
        chapter_count=1,
        main_chapter_count=1,
    )
    db.add(book)
    db.commit()
    path = UPLOADS_DIR / str(book.id) / "source.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE, encoding="utf-8")
    db.add(
        Chapter(
            book_id=book.id,
            idx=0,
            title=TITLE,
            kind="main",
            char_start=0,
            char_end=len(SOURCE),
            word_count=len(SOURCE),
        )
    )
    if with_chunks:
        cursor = len(TITLE) + 1
        for seq, text in enumerate(PARAGRAPHS):
            db.add(
                Chunk(
                    book_id=book.id,
                    version=CHUNK_VERSION,
                    chapter_idx=0,
                    level="child",
                    seq=seq,
                    text=text,
                    char_start=cursor,
                    char_end=cursor + len(text),
                )
            )
            cursor += len(text) + 1
    warm = Character(
        book_id=book.id, canonical_name="韩砚清", is_protagonist=True, source="extracted"
    )
    other = Character(book_id=book.id, canonical_name="顾青珩", source="extracted")
    db.add_all([warm, other])
    db.commit()
    db.add_all(
        [
            CharacterAlias(
                book_id=book.id,
                character_id=warm.id,
                alias="灼瑾",
                occurrence_count=1,
                source="mention",
            ),
            CharacterAlias(
                book_id=book.id,
                character_id=other.id,
                alias="姐姐",
                occurrence_count=1,
                source="mention",
            ),
            CharacterAlias(
                book_id=book.id,
                character_id=other.id,
                alias="她",
                occurrence_count=1,
                source="mention",
            ),
        ]
    )
    db.commit()
    return book.id


def test_read_around_by_position():
    with SessionLocal() as db:
        book_id = _make_book(db)
        start = len(TITLE) + 1
        data = text_window_service.read_around(
            db, book_id, 0, char_offset=start + 3, radius=0
        )
        assert data["mode"] == "position"
        assert len(data["segments"]) == 1
        assert data["segments"][0]["seq"] == 0
        assert data["char_offset"] == start + 3


def test_read_around_expands_when_anchor_missing():
    with SessionLocal() as db:
        book_id = _make_book(db)
        second_start = len(TITLE) + 1 + len(PARAGRAPHS[0]) + 1
        data = text_window_service.read_around(
            db, book_id, 0, anchor="姐姐", char_offset=second_start, radius=0
        )
        assert data["mode"] == "position"
        seqs = {item["seq"] for item in data["segments"]}
        assert 0 in seqs
        assert any(item["anchors"] for item in data["segments"])


def test_read_around_by_anchor_only():
    with SessionLocal() as db:
        book_id = _make_book(db)
        data = text_window_service.read_around(db, book_id, 0, anchor="姐姐")
        assert data["mode"] == "anchor"
        assert {item["seq"] for item in data["segments"]} == {0, 2}
        first = data["segments"][0]["anchors"][0]
        assert first["pos"] == PARAGRAPHS[0].index("姐姐")
        names = {item["name"] for item in first["names_nearby"]}
        assert "姐姐" in names


def test_read_around_whole_chapter():
    with SessionLocal() as db:
        book_id = _make_book(db)
        data = text_window_service.read_around(db, book_id, 0)
        assert data["mode"] == "whole_chapter"
        assert len(data["segments"]) == 3
        assert data["truncated"] is False


def test_scan_names_prefers_longest_match():
    with SessionLocal() as db:
        book_id = _make_book(db)
        hits = text_window_service.scan_names(db, book_id, PARAGRAPHS[0])
        names = [(hit["start"], hit["name"]) for hit in hits]
        assert names[0] == (0, "韩砚清")
        assert all(name != "灼瑾" for _, name in names)
        assert all(name != "她" for _, name in names)


def test_scan_names_positions_are_exact():
    with SessionLocal() as db:
        book_id = _make_book(db)
        for paragraph in PARAGRAPHS:
            for hit in text_window_service.scan_names(db, book_id, paragraph):
                assert paragraph[hit["start"] : hit["end"]] == hit["name"]


def test_read_around_falls_back_without_chunks():
    with SessionLocal() as db:
        book_id = _make_book(db, with_chunks=False)
        data = text_window_service.read_around(db, book_id, 0)
        assert data["mode"] == "whole_chapter"
        assert data["segments"]
        assert data["segments"][0]["char_start"] == len(TITLE) + 1
        assert "韩砚清" in data["segments"][0]["text"]


def test_read_around_rejects_missing_chapter():
    with SessionLocal() as db:
        book_id = _make_book(db)
        try:
            text_window_service.read_around(db, book_id, 9)
        except Exception as exc:
            assert getattr(exc, "code", None) == 4040
        else:
            raise AssertionError("缺少章节应当报错")


def test_chapter_content_does_not_depend_on_chunks():
    """正文从源文件切，不经过切块表，没切块也要能读且不重复。"""
    with SessionLocal() as db:
        book_id = _make_book(db, with_chunks=False)
        data = text_window_service.get_chapter_content(db, book_id, 0)
        assert data["text"] == "\n".join(PARAGRAPHS)
        assert data["text"].count("姐姐") == 2
        assert data["text"].count("韩砚清") == 2


def test_read_around_api(client):
    with SessionLocal() as db:
        book_id = _make_book(db)
    response = client.get(
        f"/api/books/{book_id}/chapters/0/around", params={"anchor": "姐姐"}
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["mode"] == "anchor"
    assert data["segments"]
    first = data["segments"][0]["anchors"][0]
    assert first["pos"] == PARAGRAPHS[0].index("姐姐")
    assert first["names_nearby"][0]["character_ids"]

    missing = client.get(f"/api/books/{book_id}/chapters/9/around")
    assert missing.status_code == 200
    assert missing.json()["code"] == 4040
