"""人物/世界观向量索引测试（fake 8 维 embedding，不加载真模型）。"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import models

from app import config
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.lore_fact import LoreFact
from app.services import (
    embedding_service,
    entity_index_service,
    entity_override_service,
    text_index_service,
)


@pytest.fixture(autouse=True)
def qdrant_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "QDRANT_PATH", str(tmp_path / "qdrant"))
    token = uuid.uuid4().hex
    monkeypatch.setattr(
        config, "CHARACTER_INDEX_COLLECTION", f"char_test_{token}"
    )
    monkeypatch.setattr(config, "LORE_INDEX_COLLECTION", f"lore_test_{token}")
    monkeypatch.setattr(config, "CHARACTER_INDEX_VERSION", 2)
    monkeypatch.setattr(config, "LORE_INDEX_VERSION", 1)
    text_index_service.reset_client()
    yield
    text_index_service.reset_client()


@pytest.fixture()
def fake_embedding(monkeypatch):
    calls = {"documents": 0, "query": 0}

    def dense(text: str):
        lowered = text.lower()
        if "alpha" in lowered or "林晚" in lowered:
            return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if "beta" in lowered or "秦屿" in lowered:
            return [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if "机场" in lowered or "lax" in lowered:
            return [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]

    def sparse(text: str):
        lowered = text.lower()
        if "alpha" in lowered or "林晚" in lowered:
            return {"alpha": 1.0}
        if "beta" in lowered or "秦屿" in lowered:
            return {"beta": 1.0}
        if "机场" in lowered or "lax" in lowered:
            return {"airport": 1.0}
        return {"neutral": 1.0}

    def fake_documents(texts, batch_size=None, max_length=None):
        calls["documents"] += 1
        return [
            embedding_service.EmbeddedDoc(dense=dense(text), sparse=sparse(text))
            for text in texts
        ]

    def fake_query(text):
        calls["query"] += 1
        return embedding_service.EmbeddedDoc(dense=dense(text), sparse=sparse(text))

    monkeypatch.setattr(embedding_service, "encode_documents", fake_documents)
    monkeypatch.setattr(embedding_service, "encode_query", fake_query)
    return calls


def _create_book(db, title="实体索引书", chapter_count=3, success_count=2):
    book = Book(
        title=title,
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="entity-index.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=chapter_count,
        main_chapter_count=chapter_count,
    )
    db.add(book)
    db.flush()
    for idx in range(chapter_count):
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=f"第{idx + 1}章 {title}",
                kind="main",
                char_start=0,
                char_end=10,
                word_count=10,
            )
        )
    for idx in range(success_count):
        db.add(
            ChapterExtraction(
                book_id=book.id,
                chapter_idx=idx,
                title=f"第{idx + 1}章 {title}",
                status="success",
                parsed={},
            )
        )
    db.commit()
    return book.id


def _add_character(db, book_id, name, first, deltas, aliases):
    character = Character(
        book_id=book_id,
        canonical_name=name,
        first_seen_chapter=first,
        last_seen_chapter=first + len(deltas) - 1,
        mention_count=len(deltas),
        is_protagonist=False,
        source="extracted",
    )
    db.add(character)
    db.flush()
    for offset, identity in enumerate(deltas):
        db.add(
            CharacterChapterDelta(
                book_id=book_id,
                character_id=character.id,
                chapter_idx=first + offset,
                identity=identity,
                traits_add=["alpha" if "alpha" in identity else "beta"],
                relations_add=["关系"],
                known_facts_add=["事实"],
                voice_sample="台词",
                arc="弧光",
            )
        )
    for alias, count in aliases:
        db.add(
            CharacterAlias(
                book_id=book_id,
                character_id=character.id,
                alias=alias,
                first_seen_chapter=first,
                last_seen_chapter=first,
                occurrence_count=count,
                source="mention",
            )
        )
    db.commit()
    return character.id


def _add_lore(db, book_id, name, chapter, category, summary, details):
    row = LoreFact(
        book_id=book_id,
        canonical_name=name,
        category=category,
        aliases=["别名"],
        summary=summary,
        details=details,
        entities=["实体"],
        chapter_idx=chapter,
    )
    db.add(row)
    db.commit()
    return row.id


def _source_book(db):
    book_id = _create_book(db)
    _add_character(
        db,
        book_id,
        "林晚",
        0,
        ["alpha 侦探", "alpha 侦探升级"],
        [("晚晚", 3), ("甄姑娘", 1)],
    )
    _add_character(db, book_id, "秦屿", 1, ["beta 警察"], [("言队", 2)])
    _add_lore(
        db,
        book_id,
        "LAX机场",
        1,
        "location",
        "机场",
        ["跑道", "航站楼"],
    )
    _add_lore(
        db,
        book_id,
        "青云宗",
        2,
        "organization",
        "宗门",
        ["山门"],
    )
    return book_id


def _counts(book_id):
    client = text_index_service.get_client()
    result = {}
    for layer, collection in entity_index_service.get_collection_names().items():
        result[layer] = (
            client.count(
                collection_name=collection,
                count_filter=entity_index_service._book_filter(book_id, layer),
                exact=True,
            ).count
            if client.collection_exists(collection)
            else 0
        )
    return result


def test_status_and_partial_gate(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        status = entity_index_service.get_entity_index_status(db, book_id)
        assert status["total_main"] == 3
        assert status["chapters_processed"] == 2
        assert status["complete"] is False
        assert status["character_snapshots"] == 3
        assert status["lore_facts"] == 2
        assert status["character_indexed"] == 0
        assert status["lore_indexed"] == 0
        assert status["character_needs_rebuild"] is True
        assert status["lore_needs_rebuild"] is True

        with pytest.raises(BusinessException) as blocked:
            entity_index_service.build_entity_indexes(db, book_id)
        assert blocked.value.code == 4000
        assert "32/105" not in blocked.value.message
        assert "--allow-partial" in blocked.value.message
        assert fake_embedding["documents"] == 0

        result = entity_index_service.build_entity_indexes(
            db, book_id, allow_partial=True
        )
        assert result["complete"] is False
        assert result["character_count"] == 3
        assert result["lore_count"] == 2
        assert _counts(book_id) == {"character": 3, "lore": 2}
        after = entity_index_service.get_entity_index_status(db, book_id)
        assert after["character_indexed"] == 3
        assert after["lore_indexed"] == 2
        assert after["character_needs_rebuild"] is False
        assert after["lore_needs_rebuild"] is False


def test_complete_and_idempotent(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=2,
                title="第3章补齐",
                status="success",
                parsed={},
            )
        )
        db.commit()
        result = entity_index_service.build_entity_indexes(db, book_id)
        assert result["complete"] is True
        assert _counts(book_id) == {"character": 3, "lore": 2}
        again = entity_index_service.build_entity_indexes(db, book_id)
        assert again["character_count"] == 3
        assert again["lore_count"] == 2
        assert _counts(book_id) == {"character": 3, "lore": 2}


def test_entity_reprocess_with_same_counts_marks_stale(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        entity_index_service.build_entity_indexes(
            db, book_id, allow_partial=True
        )
        status = entity_index_service.get_entity_index_status(db, book_id)
        assert status["character_needs_rebuild"] is False
        assert status["lore_needs_rebuild"] is False

        old_deltas = (
            db.query(CharacterChapterDelta)
            .filter(CharacterChapterDelta.book_id == book_id)
            .all()
        )
        delta_values = [
            {
                "id": row.id + 1000,
                "book_id": row.book_id,
                "character_id": row.character_id,
                "chapter_idx": row.chapter_idx,
                "identity": row.identity,
                "traits_add": list(row.traits_add or []),
                "relations_add": list(row.relations_add or []),
                "known_facts_add": list(row.known_facts_add or []),
                "voice_sample": row.voice_sample,
                "arc": row.arc,
            }
            for row in old_deltas
        ]
        for row in old_deltas:
            db.delete(row)
        db.flush()
        for values in delta_values:
            db.add(CharacterChapterDelta(**values))
        db.commit()

        stale = entity_index_service.get_entity_index_status(db, book_id)
        assert (
            stale["character_indexed"]
            == stale["character_deltas"]
            == 3
        )
        assert stale["character_needs_rebuild"] is True


def test_character_dedup_and_lore_search(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        entity_index_service.build_entity_indexes(
            db, book_id, allow_partial=True
        )
        characters = entity_index_service.search_character_index(
            db, book_id, "alpha", top_k=5
        )
        assert len(characters) == 1
        assert characters[0]["character"] == "林晚"
        assert characters[0]["as_of_chapter"] == 1
        assert characters[0]["identity"] == "alpha 侦探升级"
        assert characters[0]["traits"] == ["alpha"]
        assert characters[0]["dense_score"] > 0
        assert characters[0]["sparse_score"] > 0

        lore = entity_index_service.search_lore_index(
            db, book_id, "机场", top_k=5
        )
        assert lore
        assert lore[0]["canonical_name"] == "LAX机场"
        assert lore[0]["chapter_idx"] == 1
        assert lore[0]["summary"] == "机场"


def test_two_books_filter_and_empty_source(fake_embedding):
    with SessionLocal() as db:
        first = _source_book(db)
        second = _create_book(db, title="第二本", chapter_count=3, success_count=3)
        _add_character(
            db, second, "另一个alpha", 0, ["alpha 另一个人"], [("别名", 1)]
        )
        _add_lore(
            db, second, "另一个机场", 0, "location", "机场", ["跑道"]
        )
        entity_index_service.build_entity_indexes(db, first, allow_partial=True)
        entity_index_service.build_entity_indexes(db, second)
        hits = entity_index_service.search_character_index(
            db, first, "alpha", top_k=5
        )
        assert len(hits) == 1
        assert hits[0]["character"] == "林晚"
        assert _counts(first)["character"] == 3
        assert _counts(second)["character"] == 1

        empty = _create_book(
            db, title="空实体", chapter_count=2, success_count=2
        )
        result = entity_index_service.build_entity_indexes(
            db, empty, layer="lore"
        )
        assert result["lore_count"] == 0
        assert result["character_count"] == 0


def test_empty_collection_and_dimension_mismatch(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        with pytest.raises(BusinessException) as no_index:
            entity_index_service.search_character_index(db, book_id, "alpha")
        assert no_index.value.code == 4000
        assert "build_entity_indexes.py" in no_index.value.message

        client = text_index_service.get_client()
        client.create_collection(
            collection_name=config.CHARACTER_INDEX_COLLECTION,
            vectors_config={
                entity_index_service.DENSE_VECTOR_NAME: models.VectorParams(
                    size=4, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                entity_index_service.SPARSE_VECTOR_NAME: models.SparseVectorParams()
            },
        )
        with pytest.raises(BusinessException) as mismatch:
            entity_index_service.build_entity_indexes(
                db, book_id, layer="character", allow_partial=True
            )
        assert "维度" in mismatch.value.message


def test_text_and_summary_collections_are_untouched(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        assert not text_index_service.get_client().collection_exists("novel_text")
        assert not text_index_service.get_client().collection_exists(
            "novel_summary"
        )
        entity_index_service.build_entity_indexes(
            db, book_id, allow_partial=True
        )
        client = text_index_service.get_client()
        assert not client.collection_exists("novel_text")
        assert not client.collection_exists("novel_summary")


def test_build_lock_blocks_second_build(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        assert entity_index_service._build_lock.acquire(blocking=False)
        try:
            with pytest.raises(BusinessException) as busy:
                entity_index_service.build_entity_indexes(
                    db, book_id, allow_partial=True
                )
            assert busy.value.code == 4090
        finally:
            entity_index_service._build_lock.release()

def test_search_character_index_respects_chapter(fake_embedding):
    with SessionLocal() as db:
        book_id = _source_book(db)
        entity_index_service.build_entity_indexes(db, book_id, allow_partial=True)

        latest = entity_index_service.search_character_index(
            db, book_id, "alpha", top_k=5
        )
        assert latest[0]["identity"] == "alpha 侦探升级"
        assert latest[0]["query_chapter"] == 1
        assert latest[0]["edited"] is False

        early = entity_index_service.search_character_index(
            db, book_id, "alpha", top_k=5, chapter_idx=0
        )
        assert early[0]["identity"] == "alpha 侦探"
        assert early[0]["query_chapter"] == 0

        # 秦屿第 2 章才出现，查第 1 章检索不到
        assert (
            entity_index_service.search_character_index(
                db, book_id, "beta", top_k=5, chapter_idx=0
            )
            == []
        )

        entity_override_service.upsert_character_override(
            db,
            book_id,
            "林晚",
            identity="人工修正",
            traits=["人工"],
            known_facts=["人工事实"],
            from_chapter=1,
        )
        still_old = entity_index_service.search_character_index(
            db, book_id, "alpha", top_k=5, chapter_idx=0
        )
        assert still_old[0]["identity"] == "alpha 侦探"
        assert still_old[0]["edited"] is False

        after = entity_index_service.search_character_index(
            db, book_id, "alpha", top_k=5, chapter_idx=1
        )
        assert after[0]["identity"] == "人工修正"
        assert after[0]["edited"] is True


def test_lore_search_aggregates_by_chapter(fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db, chapter_count=5, success_count=5)
        _add_lore(
            db, book_id, "金鱼符", 1, "item", "第2章：丢失的信物", ["禁军搜查"]
        )
        _add_lore(
            db, book_id, "金鱼符", 3, "item", "第4章：可调兵", ["藏于玲珑匣"]
        )
        entity_index_service.build_entity_indexes(
            db, book_id, allow_partial=True
        )

        early = entity_index_service.search_lore_index(
            db, book_id, "金鱼符", top_k=5, chapter_idx=2
        )
        assert len(early) == 1
        assert early[0]["as_of_chapter"] == 1
        assert early[0]["query_chapter"] == 2
        assert early[0]["summary"] == "第2章：丢失的信物"
        assert early[0]["occurrences"] == [1]
        assert "藏于玲珑匣" not in early[0]["details"]

        late = entity_index_service.search_lore_index(
            db, book_id, "金鱼符", top_k=5, chapter_idx=4
        )
        assert len(late) == 1
        assert late[0]["as_of_chapter"] == 3
        assert late[0]["summary"] == "第4章：可调兵"
        assert late[0]["occurrences"] == [1, 3]
        assert late[0]["details"] == ["藏于玲珑匣"]
