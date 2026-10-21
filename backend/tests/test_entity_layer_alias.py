"""多义别名候选索引测试。"""

from __future__ import annotations

from sqlalchemy import text

from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_alias_observation import CharacterAliasObservation
from app.models.character_mention import CharacterMention
from app.services import entity_layer_service


def _create_book(db, protagonists=None):
    book = Book(
        title="alias测试",
        author="作者",
        protagonists=protagonists or [],
        synopsis="",
        front_matter="",
        filename="alias.txt",
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
            title=f"第{idx + 1}章 alias测试",
            status="success",
            parsed=parsed,
        )
    )
    db.commit()


def _payload(mentions=None, aliases=None):
    return {
        "summary": {},
        "characters": {
            "mentions": mentions or [],
            "card_deltas": [],
            "new_aliases": aliases or [],
        },
        "lore": [],
    }


def test_multi_valued_father_alias_and_candidates():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[
                    {"name": "江砚舟", "aliases": ["父亲"], "role": "父亲"}
                ]
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(
                mentions=[
                    {"name": "江舟砚", "aliases": ["父亲"], "role": "父亲"}
                ]
            ),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)

        assert result["chapters_processed"] == 2
        assert result["characters"] == 2
        assert result["alias_rows"] == 2
        assert result["alias_observations"] == 2
        assert result["multi_alias_count"] == 1
        assert db.query(CharacterAlias).filter_by(alias="父亲").count() == 2
        assert db.query(CharacterAliasObservation).filter_by(alias="父亲").count() == 2

        all_candidates = entity_layer_service.get_alias_candidates(
            db, book_id, "父亲"
        )
        assert len(all_candidates["candidates"]) == 2
        assert all_candidates["candidates"][0]["display_name"] == "江舟砚"

        chapter_one = entity_layer_service.get_alias_candidates(
            db, book_id, "父亲", 0
        )
        assert len(chapter_one["candidates"]) == 1
        assert chapter_one["candidates"][0]["display_name"] == "江砚舟"
        assert chapter_one["candidates"][0]["occurrence_count"] == 1
        assert chapter_one["candidates"][0]["chapters"] == [0]

        chapter_two = entity_layer_service.get_alias_candidates(
            db, book_id, "父亲", 1
        )
        assert len(chapter_two["candidates"]) == 2


def test_sister_alias_is_not_filtered():
    with SessionLocal() as db:
        book_id = _create_book(db)
        for idx in (0, 1):
            _add_extraction(
                db,
                book_id,
                idx,
                _payload(
                    mentions=[
                        {
                            "name": "韩砚清",
                            "aliases": ["姐姐"],
                            "role": "姐姐",
                        }
                    ]
                ),
            )
        result = entity_layer_service.build_entity_layer(db, book_id)
        alias = db.query(CharacterAlias).filter_by(alias="姐姐").one()
        assert alias.occurrence_count == 2
        assert alias.first_seen_chapter == 0
        assert alias.last_seen_chapter == 1
        assert (
            db.query(CharacterAliasObservation)
            .filter_by(alias="姐姐")
            .count()
            == 2
        )
        assert result["multi_alias_count"] == 0


def test_pronouns_are_skipped():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[
                    {"name": "江砚舟", "aliases": ["他", "父亲"], "role": ""}
                ],
                aliases=[{"alias": "他", "canonical": "江砚舟"}],
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(mentions=[{"name": "他", "aliases": [], "role": ""}]),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)

        assert result["pronouns_skipped"] >= 3
        assert db.query(CharacterAlias).filter_by(alias="他").count() == 0
        assert db.query(CharacterAlias).filter_by(alias="父亲").count() == 1
        assert db.query(Character).filter_by(canonical_name="他").count() == 0


def test_new_alias_only_applies_to_current_chapter():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[{"name": "阿行", "aliases": [], "role": ""}],
                aliases=[{"alias": "阿行", "canonical": "孙悟空"}],
            ),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(mentions=[{"name": "阿行", "aliases": [], "role": ""}]),
        )
        result = entity_layer_service.build_entity_layer(db, book_id)

        names = {row.canonical_name for row in db.query(Character).all()}
        assert names == {"孙悟空", "阿行"}
        assert result["characters"] == 2
        assert db.query(CharacterAlias).filter_by(alias="阿行").count() == 1


def test_rebuild_observations_are_idempotent():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(
                mentions=[{"name": "江砚舟", "aliases": ["父亲"], "role": ""}]
            ),
        )
        one = entity_layer_service.build_entity_layer(db, book_id)
        two = entity_layer_service.build_entity_layer(db, book_id)
        assert one["characters"] == two["characters"] == 1
        assert one["alias_rows"] == two["alias_rows"] == 1
        assert one["alias_observations"] == two["alias_observations"] == 1
        assert db.query(CharacterAliasObservation).count() == 1


def test_old_alias_table_is_migrated():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(mentions=[{"name": "甲", "aliases": [], "role": ""}]),
        )
        db.execute(text("DROP TABLE character_alias_observations"))
        db.execute(text("DROP TABLE character_aliases"))
        db.execute(
            text(
                """
                CREATE TABLE character_aliases (
                    id INTEGER PRIMARY KEY,
                    book_id INTEGER NOT NULL,
                    character_id INTEGER NOT NULL,
                    alias VARCHAR(255) NOT NULL,
                    first_seen_chapter INTEGER,
                    source VARCHAR(20) NOT NULL,
                    create_time DATETIME,
                    update_time DATETIME,
                    UNIQUE(book_id, alias)
                )
                """
            )
        )
        db.commit()

        entity_layer_service.build_entity_layer(db, book_id)
        columns = {
            row[1]
            for row in db.execute(
                text("PRAGMA table_info(character_aliases)")
            ).fetchall()
        }
        assert "occurrence_count" in columns
        assert "last_seen_chapter" in columns
        assert db.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='character_alias_observations'"
            )
        ).fetchone()


def test_alias_gate_hides_later_chapters():
    """第 N 章查询不能出现后文才有的称呼或统计。"""
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(mentions=[{"name": "甲", "aliases": ["姐姐"], "role": ""}]),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(mentions=[{"name": "乙", "aliases": ["姐姐"], "role": ""}]),
        )
        _add_extraction(
            db,
            book_id,
            2,
            _payload(mentions=[{"name": "甲", "aliases": ["姐姐"], "role": ""}]),
        )
        entity_layer_service.build_entity_layer(db, book_id)

        early = entity_layer_service.get_alias_candidates(db, book_id, "姐姐", 0)
        assert early["total_candidates"] == 1
        assert early["ambiguous"] is False
        assert early["candidates"][0]["display_name"] == "甲"
        assert early["candidates"][0]["occurrence_count"] == 1
        assert early["candidates"][0]["chapters"] == [0]

        mid = entity_layer_service.get_alias_candidates(db, book_id, "姐姐", 1)
        assert mid["total_candidates"] == 2
        assert mid["ambiguous"] is True
        assert {item["display_name"] for item in mid["candidates"]} == {"甲", "乙"}

        later = entity_layer_service.get_alias_candidates(db, book_id, "姐姐", 2)
        assert later["total_candidates"] == 2
        assert later["candidates"][0]["display_name"] == "甲"
        assert later["candidates"][0]["occurrence_count"] == 2
        assert later["candidates"][0]["chapters"] == [0, 2]


def test_alias_limit_truncates_alternatives():
    with SessionLocal() as db:
        book_id = _create_book(db)
        for idx, name in ((0, "甲"), (1, "乙"), (2, "丙")):
            _add_extraction(
                db,
                book_id,
                idx,
                _payload(mentions=[{"name": name, "aliases": ["姐姐"], "role": ""}]),
            )
        entity_layer_service.build_entity_layer(db, book_id)

        full = entity_layer_service.get_alias_candidates(
            db, book_id, "姐姐", 2, limit=0
        )
        assert full["total_candidates"] == 3
        assert len(full["alternatives"]) == 2
        assert full["truncated"] is False

        slim = entity_layer_service.get_alias_candidates(
            db, book_id, "姐姐", 2, limit=1
        )
        assert len(slim["alternatives"]) == 0
        assert slim["truncated"] is True

        default = entity_layer_service.get_alias_candidates(db, book_id, "姐姐", 2)
        assert len(default["alternatives"]) == 2
        assert default["truncated"] is False

        whole = entity_layer_service.get_alias_candidates(db, book_id, "姐姐")
        assert whole["chapter_idx"] is None
        assert whole["total_candidates"] == 3
        assert whole["reason"].startswith("全书")


def test_alias_without_observations():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(mentions=[{"name": "甲", "aliases": ["姐姐"], "role": ""}]),
        )
        entity_layer_service.build_entity_layer(db, book_id)

        result = entity_layer_service.get_alias_candidates(db, book_id, "妹妹", 0)
        assert result["best"] is None
        assert result["candidates"] == []
        assert result["ambiguous"] is False
        assert result["reason"] == "截至第1章没有「妹妹」的观测"


def test_display_name_prefers_most_used_alias():
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_extraction(
            db,
            book_id,
            0,
            _payload(mentions=[{"name": "韩砚清", "aliases": ["阿姐"], "role": ""}]),
        )
        _add_extraction(
            db,
            book_id,
            1,
            _payload(
                mentions=[
                    {"name": "韩砚清", "aliases": ["阿姐", "姐姐"], "role": ""}
                ]
            ),
        )
        entity_layer_service.build_entity_layer(db, book_id)

        result = entity_layer_service.get_alias_candidates(db, book_id, "姐姐", 1)
        item = result["candidates"][0]
        assert item["display_name"] == "阿姐"
        assert item["display_is_canonical"] is False
        assert "canonical_name" not in item


def test_alias_candidates_api(client):
    with SessionLocal() as db:
        book_id = _create_book(db)
        for idx, name in ((0, "甲"), (1, "乙")):
            _add_extraction(
                db,
                book_id,
                idx,
                _payload(mentions=[{"name": name, "aliases": ["姐姐"], "role": ""}]),
            )
        entity_layer_service.build_entity_layer(db, book_id)

    url = f"/api/books/{book_id}/entities/aliases/姐姐/candidates"
    response = client.get(url, params={"chapter_idx": 0})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["chapter_idx"] == 0
    assert data["total_candidates"] == 1
    assert data["ambiguous"] is False
    assert data["best"]["display_name"] == "甲"

    full = client.get(url, params={"chapter_idx": 1, "limit": 0}).json()["data"]
    assert full["total_candidates"] == 2
    assert len(full["alternatives"]) == 1
    assert full["ambiguous"] is True
    assert full["truncated"] is False

    missing = client.get(
        "/api/books/999999/entities/aliases/姐姐/candidates"
    )
    assert missing.status_code == 200
    assert missing.json()["code"] == 4040
