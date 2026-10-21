"""人物/别名/快照/世界观确定性重放测试。"""

from __future__ import annotations

import pytest

from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_mention import CharacterMention
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.lore_fact import LoreFact
from app.services import entity_layer_service


def _create_book(db, protagonists=None):
    book = Book(
        title="实体层测试",
        author="作者",
        protagonists=protagonists or [],
        synopsis="",
        front_matter="",
        filename="entity.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=0,
        main_chapter_count=0,
    )
    db.add(book)
    db.commit()
    return book.id


def _add_extraction(db, book_id, idx, parsed):
    db.add(
        ChapterExtraction(
            book_id=book_id,
            chapter_idx=idx,
            title=f"第{idx + 1}章 实体层测试",
            status="success",
            parsed=parsed,
        )
    )
    db.commit()


def _payload(mentions=None, deltas=None, aliases=None, lore=None):
    return {
        "summary": {},
        "characters": {
            "mentions": mentions or [],
            "card_deltas": deltas or [],
            "new_aliases": aliases or [],
        },
        "lore": lore or [],
    }


def test_three_chapter_alias_mentions_and_deltas():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[
                    {"name": "行者孙", "aliases": ["悟空"], "role": "猴王"}
                ],
                deltas=[
                    {
                        "character": "孙悟空",
                        "identity": "齐天大圣",
                        "traits": ["桀骜"],
                        "relations": [],
                        "known_facts": ["大闹天宫"],
                        "voice_sample": "俺老孙",
                        "arc": "从妖王到取经人",
                    }
                ],
                aliases=[{"alias": "行者孙", "canonical": "孙悟空"}],
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(
                mentions=[{"name": "孙悟空", "aliases": [], "role": "斗战胜佛"}],
                deltas=[
                    {
                        "character": "孙悟空",
                        "traits": ["桀骜", "桀骜", "勇敢"],
                        "known_facts": ["大闹天宫", "保唐僧取经"],
                        "voice_sample": "俺老孙来也",
                    }
                ],
            ),
        )
        _add_extraction(
            db,
            book_id,
            2,
            _payload(
                mentions=[{"name": "悟空", "aliases": [], "role": ""}],
                aliases=[{"alias": "悟空", "canonical": "孙悟空"}],
            ),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)

        assert result["characters"] == 1
        assert result["deltas"] == 2
        assert result["alias_rows"] == 2
        assert db.query(CharacterMention).count() == 3
        assert db.query(CharacterChapterDelta).count() == 2
        character = db.query(Character).one()
        assert character.canonical_name == "孙悟空"
        assert character.mention_count == 3
        assert character.first_seen_chapter == 0
        assert character.last_seen_chapter == 2
        assert character.is_protagonist is False
        assert {row.alias for row in db.query(CharacterAlias).all()} == {
            "行者孙",
            "悟空",
        }
        deltas = (
            db.query(CharacterChapterDelta)
            .order_by(CharacterChapterDelta.chapter_idx)
            .all()
        )
        assert [delta.chapter_idx for delta in deltas] == [0, 1]
        assert deltas[0].traits_add == ["桀骜"]
        assert deltas[0].identity == "齐天大圣"
        assert deltas[1].traits_add == ["勇敢"]
        assert deltas[1].known_facts_add == ["保唐僧取经"]
        assert deltas[1].voice_sample == "俺老孙来也"
        assert db.query(CharacterMention).count() == 3
        assert db.query(CharacterMention).filter_by(chapter_idx=0).one().aliases == [
            "悟空"
        ]


def test_chapter_alias_scope_is_local_only():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[{"name": "AA", "aliases": [], "role": ""}],
                aliases=[
                    {"alias": "AA", "canonical": "BB"},
                    {"alias": "BB", "canonical": "CC"},
                ],
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(mentions=[{"name": "AA", "aliases": [], "role": ""}]),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)

        names = {row.canonical_name for row in db.query(Character).all()}
        assert {"CC", "AA"}.issubset(names)
        assert db.query(CharacterAlias).count() == 2
        cc = db.query(Character).filter_by(canonical_name="CC").one()
        aa = db.query(Character).filter_by(canonical_name="AA").one()
        assert cc.mention_count == 1
        assert aa.mention_count == 1
        assert result["alias_observations"] == 2


def test_card_merge_into_one_delta_per_changed_chapter():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                deltas=[
                    {
                        "character": "林晚",
                        "identity": "少年",
                        "traits": ["A", "A"],
                        "known_facts": ["f1"],
                        "voice_sample": "v1",
                    }
                ]
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(
                deltas=[
                    {
                        "character": "林晚",
                        "identity": "少年",
                        "traits": ["A"],
                        "known_facts": ["f1"],
                    }
                ]
            ),
        )
        _add_extraction(
            db,
            book_id,
            2,
            _payload(
                deltas=[
                    {
                        "character": "林晚",
                        "identity": "少年时期",
                        "traits": ["B"],
                        "known_facts": ["f2"],
                        "voice_sample": "v2",
                    },
                    {
                        "character": "林晚",
                        "identity": "少年",
                        "traits": ["C"],
                        "known_facts": ["f2"],
                        "arc": "arc2",
                    },
                ]
            ),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)
        assert result["deltas"] == 2
        assert db.query(CharacterChapterDelta).count() == 2
        character = db.query(Character).one()
        assert character.mention_count == 0
        deltas = (
            db.query(CharacterChapterDelta)
            .order_by(CharacterChapterDelta.chapter_idx)
            .all()
        )
        assert [row.chapter_idx for row in deltas] == [0, 2]
        last = deltas[-1]
        assert last.identity == "少年时期"
        assert last.traits_add == ["B", "C"]
        assert last.known_facts_add == ["f2"]
        assert last.voice_sample == "v2"
        assert last.arc == "arc2"
        card = entity_layer_service.get_character_card(
            db, book_id, "林晚", 2
        )
        assert card["identity"]["text"] == "少年时期"
        assert [item["text"] for item in card["traits"]] == ["A", "B", "C"]
        assert [item["text"] for item in card["known_facts"]] == ["f1", "f2"]


def test_protagonist_parentheses_match():
    with SessionLocal() as db:
        book_id = _create_book(db, protagonists=["林晚（阿晚）", "秦屿"])
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[
                    {"name": "林晚", "aliases": ["阿晚"], "role": ""},
                    {"name": "路人", "aliases": [], "role": ""},
                ]
            ),
        )
        entity_layer_service.build_entity_layer(db, book_id)
        rows = {row.canonical_name: row for row in db.query(Character).all()}
        assert rows["林晚"].is_protagonist is True
        assert rows["路人"].is_protagonist is False


def test_lore_merge_cross_chapter_and_details_limit():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                lore=[
                    {
                        "name": "青云宗",
                        "category": "location",
                        "aliases": ["青云"],
                        "summary": "s1",
                        "details": [f"d{i}" for i in range(12)],
                        "entities": ["a"],
                    },
                    {
                        "name": "青云",
                        "category": "organization",
                        "aliases": ["青云门"],
                        "summary": "s2",
                        "details": ["d1", "d2"],
                        "entities": ["b"],
                    },
                ]
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(
                lore=[
                    {
                        "name": "青云宗",
                        "category": "",
                        "aliases": ["青云"],
                        "summary": "s3",
                        "details": ["d3"],
                        "entities": ["c"],
                    }
                ]
            ),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)
        assert db.query(LoreFact).count() == 2
        rows = (
            db.query(LoreFact)
            .order_by(LoreFact.chapter_idx)
            .all()
        )
        assert len(rows) == 2
        first = rows[0]
        assert first.canonical_name == "青云宗"
        assert len(first.details) == 10
        assert first.details[:3] == ["d0", "d1", "d2"]
        assert first.aliases == ["青云", "青云门"]
        assert first.summary == "s2"
        assert first.category == "organization"
        assert first.entities == ["a", "b"]
        assert rows[1].chapter_idx == 1
        assert rows[1].summary == "s3"


def test_rebuild_is_idempotent_and_books_are_isolated():
    with SessionLocal() as db:
        first = _create_book(db)
        second = _create_book(db)
        _add_extraction(
            db,
            first,
            0,
            _payload(
                mentions=[{"name": "甲", "aliases": [], "role": ""}],
                lore=[
                    {
                        "name": "城",
                        "category": "location",
                        "aliases": [],
                        "summary": "s",
                        "details": [],
                        "entities": [],
                    }
                ],
            ),
        )
        _add_extraction(
            db,
            second,
            0,
            _payload(mentions=[{"name": "乙", "aliases": [], "role": ""}]),
        )
        one = entity_layer_service.build_entity_layer(db, first)
        two = entity_layer_service.build_entity_layer(db, first)
        assert one["characters"] == two["characters"] == 1
        assert db.query(LoreFact).filter_by(book_id=first).count() == 1
        assert db.query(Character).filter_by(book_id=first).count() == 1
        assert db.query(Character).filter_by(book_id=second).count() == 0
        entity_layer_service.build_entity_layer(db, second)
        assert db.query(Character).filter_by(book_id=second).count() == 1
        assert db.query(Character).filter_by(book_id=first).count() == 1


def test_no_successful_extractions():
    with SessionLocal() as db:
        book_id = _create_book(db)
        with pytest.raises(BusinessException) as exc_info:
            entity_layer_service.build_entity_layer(db, book_id)
        assert exc_info.value.code == 4000
        with pytest.raises(BusinessException) as missing:
            entity_layer_service.build_entity_layer(db, 99999)
        assert missing.value.code == 4040


def test_lore_matching_character_is_dropped():
    """人物名不落 lore：本章已登记的和后文才登记的都要拦。"""
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[{"name": "安平郡主", "aliases": [], "role": "宗室"}],
                lore=[
                    {
                        "name": "安平郡主",
                        "category": "organization",
                        "aliases": [],
                        "summary": "本章已登记的人物",
                        "details": [],
                        "entities": [],
                    },
                    {
                        "name": "陆知节",
                        "category": "item",
                        "aliases": [],
                        "summary": "本章只写进 lore，第2章才登记为人物",
                        "details": [],
                        "entities": [],
                    },
                    {
                        "name": "金鱼符",
                        "category": "item",
                        "aliases": [],
                        "summary": "真正的世界观条目",
                        "details": [],
                        "entities": [],
                    },
                ],
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(
                mentions=[{"name": "陆知节", "aliases": [], "role": "状元"}]
            ),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)
        names = [
            row.canonical_name
            for row in db.query(LoreFact).order_by(LoreFact.canonical_name)
        ]
        assert names == ["金鱼符"]
        assert result["lore_dropped_as_character"] == 2
        assert result["lore_facts"] == 1

