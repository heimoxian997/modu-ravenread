"""摘要向量索引测试：fake 8 维 embedding，不加载真模型。"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import models

from app import config
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_summary import ChapterSummary
from app.services import embedding_service, summary_index_service, text_index_service


@pytest.fixture(autouse=True)
def summary_collection(monkeypatch):
    name = f"novel_summary_test_{uuid.uuid4().hex}"
    monkeypatch.setattr(config, "SUMMARY_INDEX_COLLECTION", name)
    monkeypatch.setattr(config, "SUMMARY_INDEX_VERSION", 1)
    text_index_service.reset_client()
    yield name
    text_index_service.reset_client()


def _dense(text: str) -> list[float]:
    lowered = text.lower()
    if "alpha" in lowered or "开灯" in lowered:
        return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    if "beta" in lowered or "关门" in lowered:
        return [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    return [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]


def _sparse(text: str) -> dict[str, float]:
    lowered = text.lower()
    if "alpha" in lowered:
        return {"alpha": 1.0}
    if "beta" in lowered:
        return {"beta": 1.0}
    return {"neutral": 1.0}


@pytest.fixture()
def fake_embedding(monkeypatch):
    calls = {"documents": 0, "query": 0}

    def fake_documents(texts, batch_size=None, max_length=None):
        calls["documents"] += 1
        return [
            embedding_service.EmbeddedDoc(dense=_dense(text), sparse=_sparse(text))
            for text in texts
        ]

    def fake_query(text):
        calls["query"] += 1
        return embedding_service.EmbeddedDoc(dense=_dense(text), sparse=_sparse(text))

    monkeypatch.setattr(
        embedding_service, "encode_documents", fake_documents
    )
    monkeypatch.setattr(embedding_service, "encode_query", fake_query)
    return calls


def _create_book(db, title="摘要索引测试", count=3):
    book = Book(
        title=title,
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="summary-index.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=count,
        main_chapter_count=count,
    )
    db.add(book)
    db.flush()
    for idx in range(count):
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
    db.commit()
    return book.id


def _add_summary(db, book_id, idx, text, title=None):
    db.add(
        ChapterSummary(
            book_id=book_id,
            chapter_idx=idx,
            title=title or f"第{idx + 1}章 摘要索引测试",
            summary=text,
            key_events=[f"{text}-事件"],
            characters=["林晚"],
            cliffhanger="章末钩子",
            plot_threads=[],
            foreshadowing=[],
            reveals=[],
        )
    )
    db.commit()


def _point_count(collection_name):
    client = text_index_service.get_client()
    return client.count(collection_name=collection_name, exact=True).count


def test_partial_gate_status_and_allow_partial(fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha 开灯")
        _add_summary(db, book_id, 1, "beta 关门")
        with pytest.raises(BusinessException) as blocked:
            summary_index_service.build_summary_index(db, book_id)
        assert blocked.value.code == 4000
        assert "22/105" not in blocked.value.message
        assert "--allow-partial" in blocked.value.message
        assert fake_embedding["documents"] == 0

        before = summary_index_service.get_summary_index_status(db, book_id)
        assert before["total_main"] == 3
        assert before["summary_count"] == 2
        assert before["indexed_count"] == 0
        assert before["complete"] is False
        assert before["needs_rebuild"] is True
        assert before["dim"] == 1024

        result = summary_index_service.build_summary_index(
            db, book_id, allow_partial=True
        )
        assert result["indexed_count"] == 2
        assert result["summary_count"] == 2
        assert result["total_main"] == 3
        assert result["complete"] is False
        assert result["dim"] == 8
        assert fake_embedding["documents"] == 1

        after = summary_index_service.get_summary_index_status(db, book_id)
        assert after["indexed_count"] == 2
        assert after["complete"] is False
        assert after["needs_rebuild"] is False
        assert _point_count(config.SUMMARY_INDEX_COLLECTION) == 2


def test_complete_rebuild_and_idempotent(fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha 开灯")
        _add_summary(db, book_id, 1, "beta 关门")
        summary_index_service.build_summary_index(
            db, book_id, allow_partial=True
        )
        _add_summary(db, book_id, 2, "gamma 中性")
        stale = summary_index_service.get_summary_index_status(db, book_id)
        assert stale["summary_count"] == 3
        assert stale["indexed_count"] == 2
        assert stale["needs_rebuild"] is True

        result = summary_index_service.build_summary_index(db, book_id)
        assert result["complete"] is True
        assert result["indexed_count"] == 3
        assert _point_count(config.SUMMARY_INDEX_COLLECTION) == 3

        again = summary_index_service.build_summary_index(db, book_id)
        assert again["indexed_count"] == 3
        assert _point_count(config.SUMMARY_INDEX_COLLECTION) == 3


def test_search_two_directions_and_scores(fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha 开灯")
        _add_summary(db, book_id, 1, "beta 关门")
        _add_summary(db, book_id, 2, "gamma 中性")
        summary_index_service.build_summary_index(db, book_id)

        alpha = summary_index_service.search_summary_index(
            db, book_id, "alpha", top_k=1
        )
        assert len(alpha) == 1
        assert alpha[0]["chapter_idx"] == 0
        assert alpha[0]["dense_score"] > 0
        assert alpha[0]["sparse_score"] > 0
        assert alpha[0]["rrf_score"] > 0
        assert alpha[0]["key_events"]
        assert alpha[0]["characters"] == ["林晚"]

        beta = summary_index_service.search_summary_index(
            db, book_id, "beta", top_k=1
        )
        assert len(beta) == 1
        assert beta[0]["chapter_idx"] == 1


def test_two_books_are_filtered(fake_embedding):
    with SessionLocal() as db:
        first = _create_book(db, title="书一", count=2)
        second = _create_book(db, title="书二", count=2)
        _add_summary(db, first, 0, "alpha 开灯")
        _add_summary(db, first, 1, "beta 关门")
        _add_summary(db, second, 0, "alpha 开灯")
        _add_summary(db, second, 1, "beta 关门")
        summary_index_service.build_summary_index(db, first)
        summary_index_service.build_summary_index(db, second)

        first_hits = summary_index_service.search_summary_index(
            db, first, "alpha", top_k=2
        )
        second_hits = summary_index_service.search_summary_index(
            db, second, "alpha", top_k=2
        )
        assert first_hits and first_hits[0]["chapter_idx"] == 0
        assert second_hits and second_hits[0]["chapter_idx"] == 0
        assert all(hit["summary_id"] != second_hits[0]["summary_id"] for hit in first_hits)


def test_empty_summary_and_empty_collection(fake_embedding):
    with SessionLocal() as db:
        empty = _create_book(db, title="空摘要")
        with pytest.raises(BusinessException) as no_summary:
            summary_index_service.build_summary_index(db, empty)
        assert "还没有摘要" in no_summary.value.message

        book_id = _create_book(db, title="未建库")
        _add_summary(db, book_id, 0, "alpha 开灯")
        with pytest.raises(BusinessException) as no_index:
            summary_index_service.search_summary_index(db, book_id, "alpha")
        assert "摘要索引不存在" in no_index.value.message


def test_dimension_mismatch(fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db, title="维度冲突")
        _add_summary(db, book_id, 0, "alpha 开灯")
        client = text_index_service.get_client()
        client.create_collection(
            collection_name=config.SUMMARY_INDEX_COLLECTION,
            vectors_config={
                summary_index_service.DENSE_VECTOR_NAME: models.VectorParams(
                    size=4, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                summary_index_service.SPARSE_VECTOR_NAME: models.SparseVectorParams()
            },
        )
        with pytest.raises(BusinessException) as mismatch:
            summary_index_service.build_summary_index(
                db, book_id, allow_partial=True
            )
        assert "维度" in mismatch.value.message


def test_novel_text_is_untouched(fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db, title="文本索引隔离", count=2)
        _add_summary(db, book_id, 0, "alpha 开灯")
        _add_summary(db, book_id, 1, "beta 关门")
        client = text_index_service.get_client()
        text_collection = config.TEXT_INDEX_COLLECTION
        client.create_collection(
            collection_name=text_collection,
            vectors_config={
                summary_index_service.DENSE_VECTOR_NAME: models.VectorParams(
                    size=8, distance=models.Distance.COSINE
                )
            },
            sparse_vectors_config={
                summary_index_service.SPARSE_VECTOR_NAME: models.SparseVectorParams()
            },
        )
        client.upsert(
            collection_name=text_collection,
            points=[
                models.PointStruct(
                    id=str(uuid.uuid4()),
                    vector={summary_index_service.DENSE_VECTOR_NAME: [1, 0, 0, 0, 0, 0, 0, 0]},
                    payload={"book_id": book_id, "index_version": 1},
                )
            ],
            wait=True,
        )
        before = client.count(collection_name=text_collection, exact=True).count
        summary_index_service.build_summary_index(db, book_id)
        after = client.count(collection_name=text_collection, exact=True).count
        assert before == 1
        assert after == 1
