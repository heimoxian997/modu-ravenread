"""人物增量事件、精确卡与 delta point 测试。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app import config
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.character_chapter_delta import CharacterChapterDelta
from app.services import embedding_service, entity_index_service, entity_layer_service, text_index_service


@pytest.fixture(autouse=True)
def qdrant_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "QDRANT_PATH", str(tmp_path / "qdrant"))
    token = uuid.uuid4().hex
    monkeypatch.setattr(
        config, "CHARACTER_INDEX_COLLECTION", f"char_delta_{token}"
    )
    monkeypatch.setattr(config, "LORE_INDEX_COLLECTION", f"lore_delta_{token}")
    monkeypatch.setattr(config, "CHARACTER_INDEX_VERSION", 2)
    monkeypatch.setattr(config, "LORE_INDEX_VERSION", 1)
    text_index_service.reset_client()
    yield
    text_index_service.reset_client()


@pytest.fixture()
def fake_embedding(monkeypatch):
    def fake_docs(texts, batch_size=None, max_length=None):
        return [
            embedding_service.EmbeddedDoc(
                dense=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                sparse={text.split("trait", 1)[1].split("】", 1)[0]: 1.0}
                if "trait" in text
                else {"neutral": 1.0},
            )
            for text in texts
        ]

    def fake_query(text):
        token = text.split("trait", 1)[1] if "trait" in text else "neutral"
        return embedding_service.EmbeddedDoc(
            dense=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
            sparse={token: 1.0},
        )

    monkeypatch.setattr(embedding_service, "encode_documents", fake_docs)
    monkeypatch.setattr(embedding_service, "encode_query", fake_query)


def _book(db, chapters=120):
    book = Book(
        title="120章主角",
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="deltas.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=chapters,
        main_chapter_count=chapters,
    )
    db.add(book)
    db.flush()
    for idx in range(chapters):
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=f"第{idx + 1}章",
                kind="main",
                char_start=0,
                char_end=10,
                word_count=10,
            )
        )
        db.add(
            ChapterExtraction(
                book_id=book.id,
                chapter_idx=idx,
                title=f"第{idx + 1}章",
                status="success",
                parsed={
                    "characters": {
                        "mentions": [],
                        "card_deltas": [
                            {
                                "character": "林晚",
                                "identity": f"身份{idx}",
                                "traits": [f"trait{idx}"],
                                "relations": [f"rel{idx}"],
                                "known_facts": [f"fact{idx}"],
                                "voice_sample": f"voice{idx}",
                                "arc": f"arc{idx}",
                            }
                        ],
                        "new_aliases": [],
                    },
                    "lore": [],
                },
            )
        )
    db.commit()
    return book.id


def test_120_chapters_deltas_card_caps_and_changes(fake_embedding):
    with SessionLocal() as db:
        book_id = _book(db)
        result = entity_layer_service.build_entity_layer(db, book_id)
        assert result["deltas"] == 120
        assert result["max_processed_chapter"] == 119
        assert db.query(CharacterChapterDelta).count() == 120
        assert db.execute(
            text(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='character_snapshots'"
            )
        ).fetchone() is None

        card = entity_layer_service.get_character_card(
            db, book_id, "林晚", 119
        )
        assert card["available"] is True
        assert card["capped"] is False
        assert card["as_of_chapter"] == 119
        assert len(card["traits"]) == 12
        assert len(card["relations"]) == 12
        assert len(card["known_facts"]) == 20
        assert card["traits"][-1]["text"] == "trait119"
        assert card["traits"][0]["text"] == "trait108"
        assert card["identity"]["text"] == "身份119"

        early = entity_layer_service.get_character_card(
            db, book_id, "林晚", 3
        )
        assert early["as_of_chapter"] == 3
        assert all(
            item["text"] != "fact5" for item in early["known_facts"]
        )
        assert all(
            item["last_seen_chapter"] <= 3 for item in early["traits"]
        )

        capped = entity_layer_service.get_character_card(
            db, book_id, "林晚", 999
        )
        assert capped["capped"] is True
        assert capped["as_of_chapter"] == 119

        changes = entity_layer_service.get_character_changes(
            db, book_id, "林晚", 4, 5
        )
        assert [row["chapter_idx"] for row in changes["chapters"]] == [4, 5]

        built = entity_index_service.build_entity_indexes(db, book_id)
        assert built["character_count"] == 120
        client = text_index_service.get_client()
        assert client.count(
            collection_name=config.CHARACTER_INDEX_COLLECTION,
            count_filter=entity_index_service._book_filter(
                book_id, "character"
            ),
            exact=True,
        ).count == 120

        rows = entity_index_service._read_character_rows(db, book_id)
        text10 = next(
            row["text"] for row in rows if row["chapter_idx"] == 10
        )
        assert "trait10" in text10
        assert "trait9" not in text10
        assert "【人物变化 · 林晚 · 第11章】" in text10

        hits3 = entity_index_service.search_character_changes(
            db, book_id, "trait3", 3, top_k=5
        )
        assert hits3
        assert hits3[0]["chapter_idx"] <= 3
        hits5 = entity_index_service.search_character_changes(
            db, book_id, "trait5", 5, top_k=5
        )
        assert hits5
        assert hits5[0]["chapter_idx"] == 5
        assert len({hit["character_id"] for hit in hits5}) == len(hits5)


def test_no_change_chapter_has_no_delta(fake_embedding):
    with SessionLocal() as db:
        book_id = _book(db, chapters=3)
        # 清空第二、三章的抽取再重放，只留第一章变化
        db.query(ChapterExtraction).filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.chapter_idx >= 1,
        ).delete(synchronize_session=False)
        db.commit()
        result = entity_layer_service.build_entity_layer(db, book_id)
        assert result["deltas"] == 1
        changes = entity_layer_service.get_character_changes(
            db, book_id, "林晚", 0, 2
        )
        assert [row["chapter_idx"] for row in changes["chapters"]] == [0]
        card = entity_layer_service.get_character_card(
            db, book_id, "林晚", 2
        )
        assert card["capped"] is True
        assert card["as_of_chapter"] == 0
