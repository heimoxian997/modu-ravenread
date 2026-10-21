"""人物卡/世界观 override 稳定键与脏标记测试。"""

from __future__ import annotations

from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.lore_fact import LoreFact
from app.services import (
    entity_index_service,
    entity_layer_service,
    entity_override_service,
)


def _book(db, title="override书"):
    book = Book(
        title=title,
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="override.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=3,
        main_chapter_count=3,
    )
    db.add(book)
    db.commit()
    return book.id


def _character(db, book_id):
    character = Character(
        book_id=book_id,
        canonical_name="林晚",
        first_seen_chapter=0,
        last_seen_chapter=1,
        mention_count=2,
        is_protagonist=True,
        source="extracted",
    )
    db.add(character)
    db.flush()
    db.add(
        CharacterChapterDelta(
            book_id=book_id,
            character_id=character.id,
            chapter_idx=0,
            identity="旧身份",
            traits_add=["旧特质"],
            relations_add=["旧关系"],
            known_facts_add=["旧事实"],
            voice_sample="旧台词",
            arc="旧弧光",
        )
    )
    db.add(
        CharacterChapterDelta(
            book_id=book_id,
            character_id=character.id,
            chapter_idx=1,
            identity="基础身份",
            traits_add=["基础特质"],
            relations_add=["基础关系"],
            known_facts_add=["基础事实"],
            voice_sample="基础台词",
            arc="基础弧光",
        )
    )
    db.add(
        ChapterExtraction(
            book_id=book_id,
            chapter_idx=1,
            title="第2章",
            status="success",
            parsed={},
        )
    )
    db.add(
        CharacterAlias(
            book_id=book_id,
            character_id=character.id,
            alias="晚晚",
            first_seen_chapter=0,
            last_seen_chapter=1,
            occurrence_count=2,
            source="mention",
        )
    )
    db.commit()
    return character.id


def _lore(db, book_id):
    row = LoreFact(
        book_id=book_id,
        canonical_name="LAX机场",
        category="location",
        aliases=["旧别名"],
        summary="旧概述",
        details=["旧细节"],
        entities=["旧实体"],
        chapter_idx=1,
    )
    db.add(row)
    db.commit()
    return row.id


def test_character_override_merge_delete_and_dirty():
    with SessionLocal() as db:
        book_id = _book(db)
        character_id = _character(db, book_id)
        detail = entity_override_service.get_character_detail(
            db, book_id, character_id
        )
        assert detail["latest_snapshot"]["identity"] == "基础身份"
        assert detail["edited"] is False

        entity_override_service.upsert_character_override(
            db,
            book_id,
            "林晚",
            identity="修正身份",
            traits=["修正特质"],
            relations=["修正关系"],
            known_facts=["修正事实"],
            voice_sample="修正台词",
            arc="修正弧光",
        )
        detail = entity_override_service.get_character_detail(
            db, book_id, character_id
        )
        assert detail["effective"]["identity"] == "修正身份"
        assert detail["effective"]["traits"] == ["修正特质"]
        assert detail["latest_snapshot"]["identity"] == "基础身份"
        assert detail["edited"] is True
        assert entity_override_service.is_dirty(
            db, book_id, "character"
        )

        entity_override_service.delete_character_override(
            db, book_id, "林晚"
        )
        detail = entity_override_service.get_character_detail(
            db, book_id, character_id
        )
        assert detail["effective"]["identity"] == "基础身份"
        assert detail["edited"] is False
        assert entity_override_service.is_dirty(
            db, book_id, "character"
        )


def test_lore_override_merge_delete_and_orphan():
    with SessionLocal() as db:
        book_id = _book(db)
        lore_id = _lore(db, book_id)
        entity_override_service.upsert_lore_override(
            db,
            book_id,
            "LAX机场",
            1,
            category="organization",
            aliases=["新别名"],
            summary="新概述",
            details=["新细节"],
            entities=["新实体"],
        )
        detail = entity_override_service.get_lore_detail(
            db, book_id, lore_id
        )
        assert detail["effective"]["category"] == "organization"
        assert detail["effective"]["summary"] == "新概述"
        assert detail["edited"] is True
        assert entity_override_service.is_dirty(db, book_id, "lore")

        entity_override_service.upsert_lore_override(
            db,
            book_id,
            "孤儿条目",
            99,
            summary="孤儿",
        )
        assert entity_override_service.get_lore_override(
            db, book_id, "孤儿条目", 99
        )
        assert entity_override_service.get_lore_detail(
            db, book_id, lore_id
        )["effective"]["summary"] == "新概述"

        entity_override_service.delete_lore_override(
            db, book_id, "LAX机场", 1
        )
        detail = entity_override_service.get_lore_detail(
            db, book_id, lore_id
        )
        assert detail["effective"]["summary"] == "旧概述"
        assert detail["edited"] is False


def test_index_rows_are_incremental_and_lore_override_applies():
    with SessionLocal() as db:
        book_id = _book(db)
        _character(db, book_id)
        lore_id = _lore(db, book_id)
        entity_override_service.upsert_lore_override(
            db, book_id, "LAX机场", 1, summary="索引新概述"
        )
        rows = entity_index_service._read_character_rows(db, book_id)
        assert len(rows) == 2
        latest = next(row for row in rows if row["chapter_idx"] == 1)
        older = next(row for row in rows if row["chapter_idx"] == 0)
        assert "基础身份" in latest["text"]
        assert "新增性格：基础特质" in latest["text"]
        assert "旧特质" not in latest["text"]
        assert "旧身份" in older["text"]
        lore = entity_index_service._read_lore_rows(db, book_id)
        assert lore[0]["summary"] == "索引新概述"
        assert "索引新概述" in lore[0]["text"]
        assert lore_id


def test_build_entity_layer_keeps_override_and_marks_dirty():
    with SessionLocal() as db:
        book_id = _book(db)
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=0,
                title="第1章",
                status="success",
                parsed={
                    "characters": {
                        "mentions": [
                            {"name": "林晚", "aliases": ["晚晚"], "role": ""}
                        ],
                        "card_deltas": [
                            {
                                "character": "林晚",
                                "identity": "基础身份",
                                "traits": ["基础特质"],
                                "relations": [],
                                "known_facts": [],
                                "voice_sample": "台词",
                                "arc": "弧光",
                            }
                        ],
                        "new_aliases": [],
                    },
                    "lore": [
                        {
                            "name": "LAX机场",
                            "category": "location",
                            "aliases": [],
                            "summary": "旧概述",
                            "details": [],
                            "entities": [],
                        }
                    ],
                },
            )
        )
        db.commit()
        entity_layer_service.build_entity_layer(db, book_id)
        character = db.query(Character).filter_by(book_id=book_id).one()
        lore = db.query(LoreFact).filter_by(book_id=book_id).one()
        entity_override_service.upsert_character_override(
            db, book_id, "林晚", identity="持久修正"
        )
        entity_override_service.upsert_lore_override(
            db, book_id, "LAX机场", lore.chapter_idx, summary="持久概述"
        )

        entity_layer_service.build_entity_layer(db, book_id)
        detail = entity_override_service.get_character_detail(
            db, book_id,
            db.query(Character).filter_by(book_id=book_id).one().id,
        )
        assert detail["effective"]["identity"] == "持久修正"
        assert detail["edited"] is True
        lore_detail = entity_override_service.get_lore_detail(
            db, book_id,
            db.query(LoreFact).filter_by(book_id=book_id).one().id,
        )
        assert lore_detail["effective"]["summary"] == "持久概述"
        assert entity_override_service.is_dirty(
            db, book_id, "character"
        )
        assert entity_override_service.is_dirty(db, book_id, "lore")

def _character_two_chapters(db, book_id):
    """0-based 第 3 章与第 5 章各一条 delta，抽取覆盖到第 5 章。"""
    character = Character(
        book_id=book_id,
        canonical_name="林晚",
        first_seen_chapter=2,
        last_seen_chapter=4,
        mention_count=2,
        is_protagonist=True,
        source="extracted",
    )
    db.add(character)
    db.flush()
    for chapter_idx, identity, trait, fact in (
        (2, "第3章身份", "第3章特质", "第3章事实"),
        (4, "第5章身份", "第5章特质", "第5章事实"),
    ):
        db.add(
            CharacterChapterDelta(
                book_id=book_id,
                character_id=character.id,
                chapter_idx=chapter_idx,
                identity=identity,
                traits_add=[trait],
                relations_add=[f"第{chapter_idx + 1}章关系"],
                known_facts_add=[fact],
                voice_sample=f"第{chapter_idx + 1}章台词",
                arc=f"第{chapter_idx + 1}章弧光",
            )
        )
    for chapter_idx in (2, 3, 4):
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=chapter_idx,
                title=f"第{chapter_idx + 1}章",
                status="success",
                parsed={},
            )
        )
    db.commit()
    return character.id


def test_character_card_is_chapter_scoped():
    with SessionLocal() as db:
        book_id = _book(db)
        character_id = _character_two_chapters(db, book_id)
        early = entity_layer_service.get_character_card(
            db, book_id, character_id, 2
        )
        late = entity_layer_service.get_character_card(
            db, book_id, character_id, 4
        )
        assert early["identity"]["text"] == "第3章身份"
        assert late["identity"]["text"] == "第5章身份"
        assert [item["text"] for item in early["traits"]] == ["第3章特质"]
        assert [item["text"] for item in late["traits"]] == [
            "第3章特质",
            "第5章特质",
        ]
        assert "第5章事实" not in [
            item["text"] for item in early["known_facts"]
        ]


def test_character_detail_follows_query_chapter_and_clamps():
    with SessionLocal() as db:
        book_id = _book(db)
        character_id = _character_two_chapters(db, book_id)

        latest = entity_override_service.get_character_detail(
            db, book_id, character_id
        )
        assert latest["query_chapter"] == 4
        assert latest["effective"]["identity"] == "第5章身份"

        early = entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=2
        )
        assert early["query_chapter"] == 2
        assert early["effective"]["identity"] == "第3章身份"
        assert early["latest_snapshot"]["identity"] == "第3章身份"

        too_big = entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=999
        )
        assert too_big["query_chapter"] == 4
        assert too_big["max_processed_chapter"] == 4

        negative = entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=-3
        )
        assert negative["query_chapter"] == 0
        assert negative["effective"]["identity"] is None


def test_override_from_chapter_gates_detail():
    with SessionLocal() as db:
        book_id = _book(db)
        character_id = _character_two_chapters(db, book_id)
        entity_override_service.upsert_character_override(
            db,
            book_id,
            "林晚",
            identity="人工修正身份",
            traits=["人工特质"],
            known_facts=["人工事实"],
            from_chapter=4,
        )

        early = entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=2
        )
        assert early["effective"]["identity"] == "第3章身份"
        assert early["edited"] is False
        assert early["override_from_chapter"] == 4

        late = entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=4
        )
        assert late["effective"]["identity"] == "人工修正身份"
        assert late["edited"] is True

        latest = entity_override_service.get_character_detail(
            db, book_id, character_id
        )
        assert latest["effective"]["identity"] == "人工修正身份"
        assert latest["effective"]["traits"] == ["人工特质"]


def test_ensure_override_schema_adds_column_and_backfills():
    from app.database import engine

    with SessionLocal() as db:
        book_id = _book(db)
        _character(db, book_id)  # 产生第 2 章（idx=1）成功抽取

    with engine.begin() as conn:
        conn.exec_driver_sql(
            "ALTER TABLE character_card_overrides DROP COLUMN from_chapter"
        )
        conn.exec_driver_sql(
            "INSERT INTO character_card_overrides "
            "(book_id, character_name, identity, traits, relations, known_facts, "
            "create_time, update_time) "
            "VALUES (?, '林晚', '旧修正', '[]', '[]', '[]', "
            "datetime('now'), datetime('now'))",
            (book_id,),
        )

    entity_override_service.ensure_override_schema()

    with engine.begin() as conn:
        columns = [
            row[1]
            for row in conn.exec_driver_sql(
                "PRAGMA table_info(character_card_overrides)"
            ).fetchall()
        ]
        assert "from_chapter" in columns
        value = conn.exec_driver_sql(
            "SELECT from_chapter FROM character_card_overrides"
        ).scalar()
        assert value == 1

    # 再调用一次应直接返回，不重复回填
    entity_override_service.ensure_override_schema()
    with engine.begin() as conn:
        assert (
            conn.exec_driver_sql(
                "SELECT from_chapter FROM character_card_overrides"
            ).scalar()
            == 1
        )


def _lore_two_chapters(db, book_id):
    """0-based 第 3 章与第 5 章各一条同名 lore，抽取覆盖到第 5 章。"""
    for chapter_idx in (2, 3, 4):
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=chapter_idx,
                title=f"第{chapter_idx + 1}章",
                status="success",
                parsed={},
            )
        )
    db.add_all(
        [
            LoreFact(
                book_id=book_id,
                canonical_name="青玉牌",
                category="item",
                aliases=["鱼符"],
                summary="第3章：丢失的信物",
                details=["禁军搜查随身物品"],
                entities=["顾青珩"],
                chapter_idx=2,
            ),
            LoreFact(
                book_id=book_id,
                canonical_name="青玉牌",
                category="item",
                aliases=["兵符"],
                summary="第5章：可调兵",
                details=["藏于玲珑匣"],
                entities=["陆知节"],
                chapter_idx=4,
            ),
        ]
    )
    db.commit()
    rows = (
        db.query(LoreFact)
        .order_by(LoreFact.chapter_idx.asc())
        .all()
    )
    return [row.id for row in rows]


def test_lore_card_is_chapter_scoped():
    with SessionLocal() as db:
        book_id = _book(db)
        _lore_two_chapters(db, book_id)

        early = entity_layer_service.get_lore_card(
            db, book_id, "青玉牌", 2
        )
        assert early["available"] is True
        assert early["as_of_chapter"] == 2
        assert early["summary"] == "第3章：丢失的信物"
        assert early["details"] == ["禁军搜查随身物品"]
        assert early["aliases"] == ["鱼符"]
        assert early["occurrences"] == [2]

        late = entity_layer_service.get_lore_card(
            db, book_id, "青玉牌", 4
        )
        assert late["as_of_chapter"] == 4
        assert late["summary"] == "第5章：可调兵"
        assert late["details"] == ["藏于玲珑匣"]
        assert late["aliases"] == ["鱼符", "兵符"]
        assert late["entities"] == ["顾青珩", "陆知节"]
        assert late["occurrences"] == [2, 4]

        before = entity_layer_service.get_lore_card(
            db, book_id, "青玉牌", 0
        )
        assert before["available"] is False
        assert before["reason"] == "not_yet"
        assert before["as_of_chapter"] is None

        capped = entity_layer_service.get_lore_card(
            db, book_id, "青玉牌", 99
        )
        assert capped["as_of_chapter"] == 4
        assert capped["capped"] is True


def test_lore_detail_follows_query_chapter_and_override():
    with SessionLocal() as db:
        book_id = _book(db)
        early_id, late_id = _lore_two_chapters(db, book_id)

        early = entity_override_service.get_lore_detail(
            db, book_id, early_id, chapter_idx=2
        )
        assert early["query_chapter"] == 2
        assert early["as_of_chapter"] == 2
        assert early["effective"]["summary"] == "第3章：丢失的信物"
        assert early["occurrences"] == [2]

        late = entity_override_service.get_lore_detail(
            db, book_id, early_id, chapter_idx=4
        )
        assert late["as_of_chapter"] == 4
        assert late["lore"]["id"] == late_id
        assert late["effective"]["summary"] == "第5章：可调兵"

        raw = entity_override_service.get_lore_detail(
            db, book_id, early_id
        )
        assert raw["as_of_chapter"] == 2
        assert raw["effective"]["summary"] == "第3章：丢失的信物"

        entity_override_service.upsert_lore_override(
            db,
            book_id,
            "青玉牌",
            4,
            category="item",
            aliases=["兵符"],
            summary="人工修正：可调兵（已核实）",
            details=["藏于玲珑匣"],
            entities=["陆知节"],
        )
        untouched = entity_override_service.get_lore_detail(
            db, book_id, early_id, chapter_idx=2
        )
        assert untouched["edited"] is False
        fixed = entity_override_service.get_lore_detail(
            db, book_id, early_id, chapter_idx=4
        )
        assert fixed["edited"] is True
        assert fixed["effective"]["summary"] == "人工修正：可调兵（已核实）"
