"""抽取结果人工编辑测试。"""

from __future__ import annotations

from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.schemas.summary import ExtractionParsedPatch
from app.services import chapter_extract_service, entity_override_service

PARSED = {
    "summary": {
        "summary": "本章摘要",
        "characters": ["甲"],
        "key_events": ["事件一"],
        "cliffhanger": "悬念",
    },
    "characters": {
        "mentions": [{"name": "甲", "aliases": ["阿甲"], "role": "兄长"}],
        "card_deltas": [],
        "new_aliases": [],
    },
    "lore": [{"name": "大食国", "summary": "西域国家"}],
}


def _make_book(db) -> int:
    book = Book(
        title="编辑测试",
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="edit.txt",
        encoding="UTF-8",
        char_count=10,
        chapter_count=1,
        main_chapter_count=1,
    )
    db.add(book)
    db.commit()
    db.add(
        Chapter(
            book_id=book.id,
            idx=0,
            title="第1章 起",
            kind="main",
            char_start=0,
            char_end=10,
            word_count=10,
        )
    )
    db.add(
        ChapterExtraction(
            book_id=book.id,
            chapter_idx=0,
            title="第1章 起",
            status="success",
            parsed=PARSED,
        )
    )
    db.commit()
    return book.id


def test_patch_only_touches_parsed_blocks():
    with SessionLocal() as db:
        book_id = _make_book(db)
        result = chapter_extract_service.update_extraction_parsed(
            db,
            book_id,
            0,
            ExtractionParsedPatch(lore=[{"name": "大食国", "summary": "改过的"}]),
        )
        parsed = result["parsed"]
        assert parsed["lore"] == [{"name": "大食国", "summary": "改过的"}]
        assert parsed["characters"]["mentions"][0]["name"] == "甲"
        assert parsed["summary"]["summary"] == "本章摘要"
        assert parsed["summary"]["key_events"] == ["事件一"]

        assert entity_override_service.is_dirty(db, book_id, "character") is True
        assert entity_override_service.is_dirty(db, book_id, "lore") is True


def test_patch_characters_replaces_whole_block():
    with SessionLocal() as db:
        book_id = _make_book(db)
        payload = ExtractionParsedPatch(
            characters={
                "mentions": [{"name": "乙", "aliases": [], "role": ""}],
                "card_deltas": [],
                "new_aliases": [],
            }
        )
        result = chapter_extract_service.update_extraction_parsed(
            db, book_id, 0, payload
        )
        assert result["parsed"]["characters"]["mentions"][0]["name"] == "乙"
        assert result["parsed"]["lore"] == PARSED["lore"]


def test_patch_rejects_bad_shapes():
    with SessionLocal() as db:
        book_id = _make_book(db)
        cases = [
            ExtractionParsedPatch(lore={"name": "不是数组"}),
            ExtractionParsedPatch(lore=["不是对象"]),
            ExtractionParsedPatch(characters=["不是对象"]),
            ExtractionParsedPatch(characters={"mentions": "不是数组"}),
            ExtractionParsedPatch(
                characters={"mentions": [], "unknown_key": []}
            ),
        ]
        for payload in cases:
            try:
                chapter_extract_service.update_extraction_parsed(
                    db, book_id, 0, payload
                )
            except Exception as exc:
                assert getattr(exc, "code", None) == 4000, payload
            else:
                raise AssertionError(f"非法结构应当被拒绝：{payload}")


def test_patch_missing_extraction():
    with SessionLocal() as db:
        book_id = _make_book(db)
        try:
            chapter_extract_service.update_extraction_parsed(
                db, book_id, 9, ExtractionParsedPatch(lore=[])
            )
        except Exception as exc:
            assert getattr(exc, "code", None) == 4040
        else:
            raise AssertionError("缺少章节应当报错")


def test_patch_extraction_api(client):
    with SessionLocal() as db:
        book_id = _make_book(db)
    response = client.patch(
        f"/api/books/{book_id}/chapters/0/extraction",
        json={"lore": [{"name": "大食国", "summary": "接口改的"}]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["data"]["parsed"]["lore"][0]["summary"] == "接口改的"

    bad = client.patch(
        f"/api/books/{book_id}/chapters/0/extraction",
        json={"lore": {"not": "list"}},
    )
    assert bad.status_code == 200
    assert bad.json()["code"] == 4000

    detail = client.get(f"/api/books/{book_id}/summaries/0").json()["data"]
    assert detail["extraction"]["parsed"]["lore"][0]["summary"] == "接口改的"
