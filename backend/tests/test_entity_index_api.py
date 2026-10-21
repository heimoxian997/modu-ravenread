"""人物/世界观索引与人工修正 API 测试。"""

from __future__ import annotations

import uuid

import pytest

from app import config
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.lore_fact import LoreFact
from app.services import embedding_service, entity_index_service, text_index_service


@pytest.fixture(autouse=True)
def qdrant_env(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "QDRANT_PATH", str(tmp_path / "qdrant"))
    token = uuid.uuid4().hex
    monkeypatch.setattr(
        config, "CHARACTER_INDEX_COLLECTION", f"char_api_{token}"
    )
    monkeypatch.setattr(config, "LORE_INDEX_COLLECTION", f"lore_api_{token}")
    monkeypatch.setattr(config, "CHARACTER_INDEX_VERSION", 2)
    monkeypatch.setattr(config, "LORE_INDEX_VERSION", 1)
    text_index_service.reset_client()
    yield
    text_index_service.reset_client()


@pytest.fixture()
def fake_embedding(monkeypatch):
    def dense(text: str):
        lowered = text.lower()
        if "机场" in lowered or "lax" in lowered:
            return [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if "alpha" in lowered or "林晚" in lowered:
            return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if "beta" in lowered or "秦屿" in lowered:
            return [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]

    def sparse(text: str):
        lowered = text.lower()
        if "机场" in lowered or "lax" in lowered:
            return {"airport": 1.0}
        if "alpha" in lowered or "林晚" in lowered:
            return {"alpha": 1.0}
        if "beta" in lowered or "秦屿" in lowered:
            return {"beta": 1.0}
        return {"neutral": 1.0}

    def fake_documents(texts, batch_size=None, max_length=None):
        return [
            embedding_service.EmbeddedDoc(dense=dense(text), sparse=sparse(text))
            for text in texts
        ]

    def fake_query(text):
        return embedding_service.EmbeddedDoc(dense=dense(text), sparse=sparse(text))

    monkeypatch.setattr(embedding_service, "encode_documents", fake_documents)
    monkeypatch.setattr(embedding_service, "encode_query", fake_query)


def _create_book(db, title="实体API", chapters=3, success=2):
    book = Book(
        title=title,
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="entity-api.txt",
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
    for idx in range(success):
        db.add(
            ChapterExtraction(
                book_id=book.id,
                chapter_idx=idx,
                title=f"第{idx + 1}章",
                status="success",
                parsed={},
            )
        )
    db.commit()
    return book.id


def _source(db):
    book_id = _create_book(db)
    first = Character(
        book_id=book_id,
        canonical_name="林晚",
        first_seen_chapter=0,
        last_seen_chapter=1,
        mention_count=2,
        is_protagonist=True,
        source="extracted",
    )
    second = Character(
        book_id=book_id,
        canonical_name="秦屿",
        first_seen_chapter=1,
        last_seen_chapter=1,
        mention_count=1,
        is_protagonist=False,
        source="extracted",
    )
    db.add_all([first, second])
    db.flush()
    for chapter_idx, identity in ((0, "alpha 旧卡"), (1, "alpha 最新卡")):
        db.add(
            CharacterChapterDelta(
                book_id=book_id,
                character_id=first.id,
                chapter_idx=chapter_idx,
                identity=identity,
                traits_add=["traits"],
                relations_add=["关系"],
                known_facts_add=["事实"],
                voice_sample="台词",
                arc="弧光",
            )
        )
    db.add(
        CharacterChapterDelta(
            book_id=book_id,
            character_id=second.id,
            chapter_idx=1,
            identity="beta 警察",
            traits_add=["beta"],
            relations_add=[],
            known_facts_add=[],
            voice_sample="",
            arc="",
        )
    )
    db.add(
        CharacterAlias(
            book_id=book_id,
            character_id=first.id,
            alias="晚晚",
            first_seen_chapter=0,
            last_seen_chapter=1,
            occurrence_count=2,
            source="mention",
        )
    )
    lore = LoreFact(
        book_id=book_id,
        canonical_name="LAX机场",
        category="location",
        aliases=["别名"],
        summary="机场概述",
        details=["跑道"],
        entities=["林晚"],
        chapter_idx=1,
    )
    db.add(lore)
    db.commit()
    return book_id, first.id, lore.id


def _api(response):
    body = response.json()
    assert body["code"] == 200, body
    return body["data"]


def _points(book_id, layer):
    client = text_index_service.get_client()
    name = entity_index_service.get_collection_names()[layer]
    if not client.collection_exists(name):
        return 0
    return client.count(
        collection_name=name,
        count_filter=entity_index_service._book_filter(book_id, layer),
        exact=True,
    ).count


def test_status_partial_gate_and_build(client, fake_embedding):
    with SessionLocal() as db:
        book_id, _, _ = _source(db)
    status = _api(client.get(f"/api/books/{book_id}/entity-index"))
    assert status["available"] is True
    assert status["total_main"] == 3
    assert status["chapters_processed"] == 2
    assert status["complete"] is False
    assert status["character_snapshots"] == 3
    assert status["character_indexed"] == 0
    assert status["character_dirty"] is False
    assert status["character_needs_rebuild"] is True
    assert status["error"] is None

    blocked = client.post(
        f"/api/books/{book_id}/entity-index",
        json={"layer": "all", "allow_partial": False},
    ).json()
    assert blocked["code"] == 4000
    assert "32/105" not in blocked["message"]
    assert "勾选允许部分索引" in blocked["message"]

    built = _api(
        client.post(
            f"/api/books/{book_id}/entity-index",
            json={"layer": "all", "allow_partial": True},
        )
    )
    assert built["character_count"] == 3
    assert built["lore_count"] == 1
    assert built["complete"] is False
    assert _points(book_id, "character") == 3
    assert _points(book_id, "lore") == 1
    after = _api(client.get(f"/api/books/{book_id}/entity-index"))
    assert after["character_dirty"] is False
    assert after["character_needs_rebuild"] is False


def test_complete_and_build_lock(client, fake_embedding):
    with SessionLocal() as db:
        book_id, _, _ = _source(db)
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=2,
                title="第3章",
                status="success",
                parsed={},
            )
        )
        db.commit()
    built = _api(
        client.post(
            f"/api/books/{book_id}/entity-index",
            json={"layer": "all", "allow_partial": False},
        )
    )
    assert built["complete"] is True

    assert entity_index_service._build_lock.acquire(blocking=False)
    try:
        busy = client.post(
            f"/api/books/{book_id}/entity-index",
            json={"layer": "all", "allow_partial": True},
        ).json()
        assert busy["code"] == 4090
        search_busy = client.post(
            f"/api/books/{book_id}/character-search",
            json={"query": "alpha", "top_k": 5},
        ).json()
        assert search_busy["code"] == 4090
    finally:
        entity_index_service._build_lock.release()


def test_character_override_search_and_rebuild(client, fake_embedding):
    with SessionLocal() as db:
        book_id, character_id, _ = _source(db)
    _api(
        client.post(
            f"/api/books/{book_id}/entity-index",
            json={"layer": "all", "allow_partial": True},
        )
    )
    detail = _api(
        client.get(f"/api/books/{book_id}/characters/{character_id}")
    )
    assert detail["edited"] is False
    assert detail["latest_snapshot"]["identity"] == "alpha 最新卡"
    assert detail["character"]["aliases"] == ["晚晚"]

    saved = _api(
        client.put(
            f"/api/books/{book_id}/characters/{character_id}/card",
            json={
                "identity": "alpha 人工修正",
                "traits": ["修正特质"],
                "relations": ["修正关系"],
                "known_facts": ["修正事实"],
                "voice_sample": "修正台词",
                "arc": "修正弧光",
            },
        )
    )
    assert saved["effective"]["identity"] == "alpha 人工修正"
    assert saved["latest_snapshot"]["identity"] == "alpha 最新卡"
    assert saved["edited"] is True
    dirty = _api(client.get(f"/api/books/{book_id}/entity-index"))
    assert dirty["character_dirty"] is True
    assert dirty["character_needs_rebuild"] is True

    hit = _api(
        client.post(
            f"/api/books/{book_id}/character-search",
            json={"query": "alpha", "top_k": 5},
        )
    )[0]
    assert hit["edited"] is True
    assert hit["identity"] == "alpha 人工修正"

    _api(
        client.post(
            f"/api/books/{book_id}/entity-index",
            json={"layer": "character", "allow_partial": True},
        )
    )
    clean = _api(client.get(f"/api/books/{book_id}/entity-index"))
    assert clean["character_dirty"] is False
    hit = _api(
        client.post(
            f"/api/books/{book_id}/character-search",
            json={"query": "alpha", "top_k": 5},
        )
    )[0]
    assert hit["identity"] == "alpha 人工修正"
    assert hit["edited"] is True

    reset = _api(
        client.delete(
            f"/api/books/{book_id}/characters/{character_id}/card"
        )
    )
    assert reset["edited"] is False
    assert reset["effective"]["identity"] == "alpha 最新卡"
    dirty = _api(client.get(f"/api/books/{book_id}/entity-index"))
    assert dirty["character_dirty"] is True


def test_lore_override_and_orphan(client, fake_embedding):
    with SessionLocal() as db:
        book_id, _, lore_id = _source(db)
    _api(
        client.post(
            f"/api/books/{book_id}/entity-index",
            json={"layer": "all", "allow_partial": True},
        )
    )
    detail = _api(client.get(f"/api/books/{book_id}/lore/{lore_id}"))
    assert detail["edited"] is False
    saved = _api(
        client.put(
            f"/api/books/{book_id}/lore/{lore_id}",
            json={
                "category": "organization",
                "aliases": ["新别名"],
                "summary": "人工概述",
                "details": ["人工细节"],
                "entities": ["人工实体"],
            },
        )
    )
    assert saved["effective"]["summary"] == "人工概述"
    assert saved["edited"] is True
    hit = _api(
        client.post(
            f"/api/books/{book_id}/lore-search",
            json={"query": "机场", "top_k": 5},
        )
    )[0]
    assert hit["edited"] is True
    assert hit["summary"] == "人工概述"
    reset = _api(client.delete(f"/api/books/{book_id}/lore/{lore_id}"))
    assert reset["edited"] is False
    assert reset["effective"]["summary"] == "机场概述"


def test_rebuild_entity_layer_endpoint(client, fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db, chapters=1, success=1)
        extraction = db.query(ChapterExtraction).filter_by(book_id=book_id).one()
        extraction.parsed = {
            "characters": {
                "mentions": [
                    {"name": "林晚", "aliases": ["晚晚"], "role": ""}
                ],
                "card_deltas": [
                    {
                        "character": "林晚",
                        "identity": "alpha 法医",
                        "traits": ["敏锐"],
                        "relations": [],
                        "known_facts": [],
                        "voice_sample": "台词",
                        "arc": "弧光",
                    }
                ],
                "new_aliases": [],
            },
            "lore": [],
        }
        db.commit()
    result = _api(client.post(f"/api/books/{book_id}/entity-layer"))
    assert result["chapters_processed"] == 1
    assert result["characters"] == 1
    status = _api(client.get(f"/api/books/{book_id}/entity-index"))
    assert status["character_snapshots"] == 1
    assert status["character_dirty"] is True
    assert status["character_needs_rebuild"] is True


def test_empty_collection_unavailable_and_missing_book(client, fake_embedding, monkeypatch):
    with SessionLocal() as db:
        book_id, _, _ = _source(db)
    empty = client.post(
        f"/api/books/{book_id}/character-search",
        json={"query": "alpha", "top_k": 5},
    ).json()
    assert empty["code"] == 4000
    assert "build_entity_indexes.py" in empty["message"]

    def broken_client():
        raise RuntimeError("qdrant unavailable")

    monkeypatch.setattr(
        text_index_service, "get_client", broken_client
    )
    unavailable = _api(client.get(f"/api/books/{book_id}/entity-index"))
    assert unavailable["available"] is False
    assert "qdrant unavailable" in unavailable["error"]
    assert unavailable["character_snapshots"] == 3

    missing = client.get("/api/books/999999/entity-index").json()
    assert missing["code"] == 4040

def test_character_detail_and_search_by_chapter(client, fake_embedding):
    with SessionLocal() as db:
        book_id, character_id, _ = _source(db)
        entity_index_service.build_entity_indexes(
            db, book_id, allow_partial=True
        )

    early = _api(
        client.get(
            f"/api/books/{book_id}/characters/{character_id}",
            params={"chapter_idx": 0},
        )
    )
    assert early["query_chapter"] == 0
    assert early["effective"]["identity"] == "alpha 旧卡"
    assert early["edited"] is False

    saved = _api(
        client.put(
            f"/api/books/{book_id}/characters/{character_id}/card",
            json={
                "identity": "alpha 人工修正",
                "traits": ["修正特质"],
                "relations": [],
                "known_facts": [],
                "from_chapter": 1,
                "chapter_idx": 1,
            },
        )
    )
    assert saved["query_chapter"] == 1
    assert saved["effective"]["identity"] == "alpha 人工修正"
    assert saved["edited"] is True
    assert saved["override_from_chapter"] == 1

    still_old = _api(
        client.get(
            f"/api/books/{book_id}/characters/{character_id}",
            params={"chapter_idx": 0},
        )
    )
    assert still_old["effective"]["identity"] == "alpha 旧卡"
    assert still_old["edited"] is False

    clamped = _api(
        client.get(
            f"/api/books/{book_id}/characters/{character_id}",
            params={"chapter_idx": 999},
        )
    )
    assert clamped["query_chapter"] == 1

    early_hit = _api(
        client.post(
            f"/api/books/{book_id}/character-search",
            json={"query": "alpha", "top_k": 5, "chapter_idx": 0},
        )
    )[0]
    assert early_hit["identity"] == "alpha 旧卡"
    assert early_hit["edited"] is False
    assert early_hit["query_chapter"] == 0

    late_hit = _api(
        client.post(
            f"/api/books/{book_id}/character-search",
            json={"query": "alpha", "top_k": 5, "chapter_idx": 1},
        )
    )[0]
    assert late_hit["identity"] == "alpha 人工修正"
    assert late_hit["edited"] is True

    reset = _api(
        client.delete(
            f"/api/books/{book_id}/characters/{character_id}/card",
            params={"chapter_idx": 0},
        )
    )
    assert reset["edited"] is False
    assert reset["effective"]["identity"] == "alpha 旧卡"
    assert reset["query_chapter"] == 0
