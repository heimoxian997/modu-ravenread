"""本地 Qdrant 文本索引测试：不加载真实模型、不联网。"""

import shutil
import uuid
from pathlib import Path

import pytest
from qdrant_client import models

from app import config
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chunk import Chunk
from app.services import embedding_service, text_index_service


@pytest.fixture()
def qdrant_env(monkeypatch):
    root = Path(__file__).parent / ".pytest-data" / "qdrant" / uuid.uuid4().hex
    path = root / "storage"
    monkeypatch.setattr(config, "QDRANT_PATH", path)
    monkeypatch.setattr(config, "TEXT_INDEX_COLLECTION", "novel_text_test")
    monkeypatch.setattr(config, "TEXT_INDEX_VERSION", 1)
    text_index_service.reset_client()
    try:
        yield path
    finally:
        text_index_service.reset_client()
        shutil.rmtree(root, ignore_errors=True)


def _fake_dense(text: str, dim: int = 8) -> list[float]:
    vector = [0.0] * dim
    if "苹果" in text:
        vector[0] = 1.0
    elif "汽车" in text:
        vector[1] = 1.0
    else:
        vector[2] = 1.0
    return vector


def _fake_sparse(text: str) -> dict[str, float]:
    if "苹果" in text:
        return {"苹果": 1.0, "水果": 0.5}
    if "汽车" in text:
        return {"汽车": 1.0, "发动机": 0.5}
    return {"其他": 1.0}


def _fake_encode_documents(texts, batch_size=None, max_length=None):
    return [
        embedding_service.EmbeddedDoc(
            dense=_fake_dense(text),
            sparse=_fake_sparse(text),
        )
        for text in texts
    ]


def _fake_encode_query(text):
    return embedding_service.EmbeddedDoc(
        dense=_fake_dense(text),
        sparse=_fake_sparse(text),
    )


@pytest.fixture()
def fake_embedding(monkeypatch):
    monkeypatch.setattr(
        embedding_service,
        "get_status",
        lambda: {
            "model": "BAAI/bge-m3",
            "device": "cpu",
            "dim": 8,
            "batch_size": 8,
        },
    )
    monkeypatch.setattr(
        embedding_service, "ensure_model", lambda progress=None: Path("fake-model")
    )
    monkeypatch.setattr(
        embedding_service, "get_embedder", lambda progress=None: object()
    )
    monkeypatch.setattr(
        embedding_service, "encode_documents", _fake_encode_documents
    )
    monkeypatch.setattr(embedding_service, "encode_query", _fake_encode_query)


def _create_book(db, title: str, chapter_texts: list[list[str]]):
    book = Book(
        title=title,
        author="测试作者",
        synopsis="",
        protagonists=[],
        front_matter="",
        filename=f"{title}.txt",
        encoding="UTF-8",
        char_count=1000 * len(chapter_texts),
        chapter_count=len(chapter_texts),
        main_chapter_count=len(chapter_texts),
    )
    db.add(book)
    db.flush()

    child_ids = []
    child_chapter_indexes = []
    for chapter_index, texts in enumerate(chapter_texts):
        chapter_start = chapter_index * 1000
        chapter_end = chapter_start + 900
        chapter = Chapter(
            book_id=book.id,
            idx=chapter_index,
            title=f"第{chapter_index + 1}章 测试",
            kind="main",
            char_start=chapter_start,
            char_end=chapter_end,
            word_count=900,
        )
        db.add(chapter)
        db.flush()

        parent_text = "".join(texts)
        parent = Chunk(
            book_id=book.id,
            version=1,
            chapter_idx=chapter_index,
            level="parent",
            parent_id=None,
            seq=0,
            text=parent_text,
            char_start=chapter_start,
            char_end=chapter_start + len(parent_text),
        )
        db.add(parent)
        db.flush()

        cursor = chapter_start
        for seq, text in enumerate(texts):
            child = Chunk(
                book_id=book.id,
                version=1,
                chapter_idx=chapter_index,
                level="child",
                parent_id=parent.id,
                seq=seq,
                text=text,
                char_start=cursor,
                char_end=cursor + len(text),
            )
            db.add(child)
            db.flush()
            child_ids.append(child.id)
            child_chapter_indexes.append(chapter_index)
            cursor += len(text)

    db.commit()
    return book.id, child_ids, child_chapter_indexes


def _apple_book(db):
    return _create_book(
        db,
        "苹果书",
        [
            ["苹果很甜。", "苹果园在山上。"],
            ["汽车发动机很响。", "汽车停在路边。"],
        ],
    )


def test_build_text_index_payload_and_parent_exclusion(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, child_ids, chapter_indexes = _apple_book(db)
        result = text_index_service.build_text_index(db, book_id, batch_size=2)

        assert result["book_id"] == book_id
        assert result["point_count"] == len(child_ids)
        assert result["dim"] == 8
        assert result["batch_size"] == 2
        assert result["load_seconds"] >= 0
        assert result["encode_seconds"] >= 0
        assert result["upsert_seconds"] >= 0
        assert result["total_seconds"] >= result["encode_seconds"]

        client = text_index_service.get_client()
        collection = text_index_service.get_collection_name()
        assert client.count(collection, exact=True).count == len(child_ids)

        points, _ = client.scroll(
            collection_name=collection,
            limit=100,
            with_payload=True,
            with_vectors=False,
        )
        payloads = [point.payload for point in points]
        assert {payload["chunk_id"] for payload in payloads} == set(child_ids)
        assert {payload["chapter_idx"] for payload in payloads} == set(
            chapter_indexes
        )
        assert all(payload["book_id"] == book_id for payload in payloads)
        assert all(payload["index_version"] == 1 for payload in payloads)
        assert all("text" not in payload for payload in payloads)


def test_build_is_idempotent(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, child_ids, _ = _apple_book(db)
        first = text_index_service.build_text_index(db, book_id, batch_size=2)
        second = text_index_service.build_text_index(db, book_id, batch_size=2)

        assert first["point_count"] == len(child_ids)
        assert second["point_count"] == len(child_ids)
        client = text_index_service.get_client()
        assert (
            client.count(text_index_service.get_collection_name(), exact=True).count
            == len(child_ids)
        )


def test_search_returns_text_scores_and_parent(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, child_ids, _ = _apple_book(db)
        text_index_service.build_text_index(db, book_id, batch_size=2)

        results = text_index_service.search_text_index(
            db, book_id, "苹果好吃吗", top_k=3, recall=10
        )
        assert results
        top = results[0]
        assert top["chunk_id"] in child_ids
        assert "苹果" in top["text"]
        assert top["chapter_title"] == "第1章 测试"
        assert top["parent_text"] == "苹果很甜。苹果园在山上。"
        assert top["dense_score"] > 0
        assert top["sparse_score"] > 0
        assert top["rrf_score"] > 0
        assert top["char_start"] < top["char_end"]


def test_search_filters_by_book(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book1_id, child_ids_1, _ = _apple_book(db)
        book2_id, child_ids_2, _ = _create_book(
            db,
            "另一本书",
            [
                ["苹果也在另一本书里。", "汽车也在另一本书里。"],
                ["别的内容。", "更多内容。"],
            ],
        )
        text_index_service.build_text_index(db, book1_id, batch_size=2)
        text_index_service.build_text_index(db, book2_id, batch_size=2)

        results = text_index_service.search_text_index(
            db, book1_id, "苹果", top_k=10, recall=20
        )
        assert results
        assert all(item["chunk_id"] in child_ids_1 for item in results)
        assert all(item["chunk_id"] not in child_ids_2 for item in results)


def test_build_empty_book_raises(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book = Book(
            title="空书",
            protagonists=[],
            filename="empty.txt",
            encoding="UTF-8",
            char_count=0,
            chapter_count=0,
            main_chapter_count=0,
        )
        db.add(book)
        db.commit()
        with pytest.raises(BusinessException) as exc_info:
            text_index_service.build_text_index(db, book.id)
        assert "先完成正文切块" in exc_info.value.message


def test_search_missing_collection_raises(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, _, _ = _apple_book(db)
        with pytest.raises(BusinessException) as exc_info:
            text_index_service.search_text_index(db, book_id, "苹果")
        assert "先运行" in exc_info.value.message


def test_search_empty_collection_raises(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, _, _ = _apple_book(db)
        client = text_index_service.get_client()
        client.create_collection(
            collection_name=text_index_service.get_collection_name(),
            vectors_config={
                text_index_service.DENSE_VECTOR_NAME: models.VectorParams(
                    size=8, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                text_index_service.SPARSE_VECTOR_NAME: models.SparseVectorParams()
            },
        )
        with pytest.raises(BusinessException) as exc_info:
            text_index_service.search_text_index(db, book_id, "苹果")
        assert "索引为空" in exc_info.value.message


def test_dimension_mismatch_keeps_old_points(qdrant_env, monkeypatch):
    with SessionLocal() as db:
        book_id, child_ids, _ = _apple_book(db)
        monkeypatch.setattr(
            embedding_service, "encode_documents", _fake_encode_documents
        )
        monkeypatch.setattr(
            embedding_service,
            "get_status",
            lambda: {"model": "BAAI/bge-m3", "device": "cpu", "dim": 8},
        )
        monkeypatch.setattr(
            embedding_service, "ensure_model", lambda progress=None: Path("fake")
        )
        monkeypatch.setattr(
            embedding_service, "get_embedder", lambda progress=None: object()
        )
        text_index_service.build_text_index(db, book_id, batch_size=2)

        def _fake_16(texts, batch_size=None, max_length=None):
            return [
                embedding_service.EmbeddedDoc(
                    dense=[1.0] + [0.0] * 15,
                    sparse={"苹果": 1.0},
                )
                for _ in texts
            ]

        monkeypatch.setattr(embedding_service, "encode_documents", _fake_16)
        with pytest.raises(BusinessException) as exc_info:
            text_index_service.build_text_index(db, book_id, batch_size=2)
        assert "请删除 data/qdrant 后重建" in exc_info.value.message

        client = text_index_service.get_client()
        assert (
            client.count(text_index_service.get_collection_name(), exact=True).count
            == len(child_ids)
        )


def test_reset_client_releases_local_qdrant(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, _, _ = _apple_book(db)
        text_index_service.build_text_index(db, book_id, batch_size=2)
        client = text_index_service.get_client()
        assert client is not None

    text_index_service.reset_client()
    assert text_index_service._client is None
