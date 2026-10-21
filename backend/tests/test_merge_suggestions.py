"""人物合并建议评分测试（只建议，不自动合并）。"""

from __future__ import annotations

from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.services import entity_layer_service


def _create_book(db, title):
    book = Book(
        title=title,
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="merge.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=0,
        main_chapter_count=0,
    )
    db.add(book)
    db.commit()
    return book.id


def _add_extraction(db, book_id, idx, mentions):
    db.add(
        ChapterExtraction(
            book_id=book_id,
            chapter_idx=idx,
            title=f"第{idx + 1}章 merge测试",
            status="success",
            parsed={
                "summary": {},
                "characters": {
                    "mentions": mentions,
                    "card_deltas": [],
                    "new_aliases": [],
                },
                "lore": [],
            },
        )
    )
    db.commit()


def _warm_texts(book_id, start_idx, aliases=None):
    aliases = aliases or ["父亲", "靖平侯", "侯爷"]
    return (
        {"name": "江砚舟", "aliases": list(aliases), "role": ""},
        {"name": "江舟砚", "aliases": list(aliases), "role": ""},
    )


def test_transposition_and_canonical_alias_are_high():
    with SessionLocal() as db:
        book_id = _create_book(db, "high merge")
        _add_extraction(
            db, book_id, 0, [_warm_texts(book_id, 0, ["父亲"])[0]]
        )
        _add_extraction(
            db, book_id, 1, [_warm_texts(book_id, 0, ["父亲"])[1]]
        )
        _add_extraction(
            db,
            book_id,
            2,
            [
                {
                    "name": "安澜",
                    "aliases": ["世子", "定国公世子", "国公世子", "小世子"],
                    "role": "",
                }
            ],
        )
        _add_extraction(
            db,
            book_id,
            3,
            [
                {
                    "name": "定国公世子",
                    "aliases": ["世子", "安澜", "国公世子", "小世子"],
                    "role": "",
                }
            ],
        )
        entity_layer_service.build_entity_layer(db, book_id)
        before_chars = db.query(Character).filter_by(book_id=book_id).count()
        before_aliases = db.query(CharacterAlias).filter_by(book_id=book_id).count()

        suggestions = entity_layer_service.get_merge_suggestions(
            db, book_id, "high"
        )
        pairs = {tuple(sorted((x["character_a"], x["character_b"]))) for x in suggestions}
        assert ("江砚舟", "江舟砚") in pairs
        assert ("安澜", "定国公世子") in pairs
        warm = next(
            x
            for x in suggestions
            if {x["character_a"], x["character_b"]}
            == {"江砚舟", "江舟砚"}
        )
        assert warm["score"] >= 5
        assert warm["confidence"] == "high"
        assert "名称相邻换位或编辑距离≤1" in warm["reasons"]
        assert "父亲" in warm["shared_aliases"]

        assert db.query(Character).filter_by(book_id=book_id).count() == before_chars
        assert (
            db.query(CharacterAlias).filter_by(book_id=book_id).count()
            == before_aliases
        )


def test_co_occurrence_reduces_score():
    with SessionLocal() as db:
        book_id = _create_book(db, "co merge")
        _add_extraction(db, book_id, 0, list(_warm_texts(book_id, 0)))
        entity_layer_service.build_entity_layer(db, book_id)
        suggestions = entity_layer_service.get_merge_suggestions(
            db, book_id, "low"
        )
        warm = next(
            x
            for x in suggestions
            if {x["character_a"], x["character_b"]}
            == {"江砚舟", "江舟砚"}
        )
        assert warm["co_chapters"] == [0]
        assert warm["score"] == 4
        assert warm["confidence"] == "medium"


def test_no_suggestion_modifies_characters():
    with SessionLocal() as db:
        book_id = _create_book(db, "read only merge")
        _add_extraction(db, book_id, 0, [_warm_texts(book_id, 0)[0]])
        _add_extraction(db, book_id, 1, [_warm_texts(book_id, 0)[1]])
        entity_layer_service.build_entity_layer(db, book_id)
        character_ids = [
            row.id
            for row in db.query(Character)
            .filter_by(book_id=book_id)
            .order_by(Character.id)
            .all()
        ]
        entity_layer_service.get_merge_suggestions(db, book_id, "low")
        assert [
            row.id
            for row in db.query(Character)
            .filter_by(book_id=book_id)
            .order_by(Character.id)
            .all()
        ] == character_ids
