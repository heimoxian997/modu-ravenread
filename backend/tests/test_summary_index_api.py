"""摘要索引 API 测试：fake 8 维 embedding，不加载真模型。"""

from __future__ import annotations

import uuid

import pytest

from app import config
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_summary import ChapterSummary
from app.models.extract_job import ExtractJob
from app.services import embedding_service, summary_index_service, text_index_service


@pytest.fixture(autouse=True)
def summary_collection(monkeypatch):
    name = f"novel_summary_api_{uuid.uuid4().hex}"
    monkeypatch.setattr(config, "SUMMARY_INDEX_COLLECTION", name)
    monkeypatch.setattr(config, "SUMMARY_INDEX_VERSION", 1)
    text_index_service.reset_client()
    yield name
    text_index_service.reset_client()


@pytest.fixture()
def fake_embedding(monkeypatch):
    def dense(text: str):
        lowered = text.lower()
        if "alpha" in lowered:
            return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        if "beta" in lowered:
            return [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        return [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    def sparse(text: str):
        lowered = text.lower()
        if "alpha" in lowered:
            return {"alpha": 1.0}
        if "beta" in lowered:
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


def _create_book(db, title="API摘要索引", count=3):
    book = Book(
        title=title,
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="api-summary-index.txt",
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


def _add_summary(db, book_id, idx, text):
    db.add(
        ChapterSummary(
            book_id=book_id,
            chapter_idx=idx,
            title=f"第{idx + 1}章 API摘要索引",
            summary=text,
            key_events=[f"{text}-事件"],
            characters=["林晚"],
            cliffhanger="钩子",
            plot_threads=[],
            foreshadowing=[],
            reveals=[],
        )
    )
    db.commit()


def test_status_no_partial_complete_and_needs_rebuild(client, fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha")
        _add_summary(db, book_id, 1, "beta")

    empty = client.get(f"/api/books/{book_id}/summary-index").json()
    assert empty["code"] == 200
    assert empty["data"]["available"] is True
    assert empty["data"]["summary_count"] == 2
    assert empty["data"]["indexed_count"] == 0
    assert empty["data"]["needs_rebuild"] is True
    assert empty["data"]["complete"] is False

    partial = client.post(
        f"/api/books/{book_id}/summary-index",
        json={"allow_partial": True},
    ).json()
    assert partial["code"] == 200
    assert partial["data"]["indexed_count"] == 2

    status = client.get(f"/api/books/{book_id}/summary-index").json()
    assert status["data"]["indexed_count"] == 2
    assert status["data"]["needs_rebuild"] is False
    assert status["data"]["complete"] is False

    with SessionLocal() as db:
        _add_summary(db, book_id, 2, "gamma")
    stale = client.get(f"/api/books/{book_id}/summary-index").json()
    assert stale["data"]["summary_count"] == 3
    assert stale["data"]["indexed_count"] == 2
    assert stale["data"]["needs_rebuild"] is True

    complete = client.post(
        f"/api/books/{book_id}/summary-index", json={}
    ).json()
    assert complete["code"] == 200
    assert complete["data"]["indexed_count"] == 3
    assert complete["data"]["complete"] is True
    final = client.get(f"/api/books/{book_id}/summary-index").json()
    assert final["data"]["needs_rebuild"] is False
    assert final["data"]["complete"] is True


def test_status_qdrant_unavailable(client, monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha")

    def broken():
        raise BusinessException(message="本地 Qdrant 不可用")

    monkeypatch.setattr(text_index_service, "get_client", broken)
    response = client.get(f"/api/books/{book_id}/summary-index")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["available"] is False
    assert data["indexed_count"] == 0
    assert data["summary_count"] == 0
    assert "Qdrant" in data["error"]


def test_build_incomplete_lock_and_batch_conflict(client, fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha")
        _add_summary(db, book_id, 1, "beta")

    blocked = client.post(
        f"/api/books/{book_id}/summary-index", json={}
    ).json()
    assert blocked["code"] == 4000
    assert "allow-partial" in blocked["message"]

    assert summary_index_service._build_lock.acquire(blocking=False)
    try:
        busy = client.post(
            f"/api/books/{book_id}/summary-index",
            json={"allow_partial": True},
        ).json()
        assert busy["code"] == 4090
    finally:
        summary_index_service._build_lock.release()

    with SessionLocal() as db:
        db.add(
            ExtractJob(
                book_id=book_id,
                status="running",
                stage="running",
                planned=3,
            )
        )
        db.commit()
    batch_busy = client.post(
        f"/api/books/{book_id}/summary-index",
        json={"allow_partial": True},
    ).json()
    assert batch_busy["code"] == 4090
    assert "批量生成" in batch_busy["message"]

    with SessionLocal() as db:
        db.query(ExtractJob).delete()
        db.commit()
    ok = client.post(
        f"/api/books/{book_id}/summary-index",
        json={"allow_partial": True},
    ).json()
    assert ok["code"] == 200
    assert ok["data"]["indexed_count"] == 2


def test_search_results_and_errors(client, fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha")
        _add_summary(db, book_id, 1, "beta")
        _add_summary(db, book_id, 2, "gamma")
    client.post(f"/api/books/{book_id}/summary-index", json={})

    search = client.post(
        f"/api/books/{book_id}/summary-search",
        json={"query": "alpha", "top_k": 1},
    ).json()
    assert search["code"] == 200
    assert len(search["data"]) == 1
    assert search["data"][0]["chapter_idx"] == 0
    assert search["data"][0]["dense_score"] > 0
    assert search["data"][0]["sparse_score"] > 0

    empty = client.post(
        f"/api/books/{book_id}/summary-search",
        json={"query": "   "},
    ).json()
    assert empty["code"] == 422

    assert summary_index_service._build_lock.acquire(blocking=False)
    try:
        busy = client.post(
            f"/api/books/{book_id}/summary-search",
            json={"query": "alpha"},
        ).json()
        assert busy["code"] == 4090
    finally:
        summary_index_service._build_lock.release()

def test_search_empty_collection(client, fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db, title="空索引")
        _add_summary(db, book_id, 0, "alpha")
    no_index = client.post(
        f"/api/books/{book_id}/summary-search", json={"query": "alpha"}
    ).json()
    assert no_index["code"] == 4000
    assert "先重建" in no_index["message"]


def _build_index(client, book_id):
    return client.post(
        f"/api/books/{book_id}/summary-index", json={"allow_partial": True}
    ).json()


def test_search_with_rerank_returns_two_orders(client, fake_embedding, monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha")
        _add_summary(db, book_id, 1, "beta")
        _add_summary(db, book_id, 2, "gamma")
    assert _build_index(client, book_id)["code"] == 200

    scores = {"alpha": 0.1, "beta": 0.9, "gamma": 0.5}
    monkeypatch.setattr(
        "app.services.rerank_service.rerank",
        lambda query, documents: ([scores[doc] for doc in documents], None),
    )
    monkeypatch.setattr(
        "app.services.rerank_service.get_last_run_info",
        lambda: {"latency_ms": 12},
    )

    data = client.post(
        f"/api/books/{book_id}/summary-search",
        json={"query": "alpha", "top_k": 3, "rerank": True},
    ).json()["data"]

    assert data["rerank_applied"] is True
    assert data["rerank_error"] is None
    assert data["rerank_latency_ms"] == 12
    # RRF 把 alpha 排第一，重排按分数把 beta 顶上来
    assert data["rrf_items"][0]["chapter_idx"] == 0
    assert data["rerank_items"][0]["chapter_idx"] == 1
    assert data["rerank_items"][0]["rerank_score"] == 0.9
    assert data["rerank_items"][0]["rank_delta"] > 0


def test_search_degrades_to_rrf_when_rerank_unavailable(
    client, fake_embedding, monkeypatch
):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha")
        _add_summary(db, book_id, 1, "beta")
    assert _build_index(client, book_id)["code"] == 200

    monkeypatch.setattr(
        "app.services.rerank_service.rerank",
        lambda query, documents: (None, "重排模型缺失：/nope"),
    )
    data = client.post(
        f"/api/books/{book_id}/summary-search",
        json={"query": "alpha", "top_k": 2, "rerank": True},
    ).json()["data"]

    assert data["rerank_applied"] is False
    assert "重排模型缺失" in data["rerank_error"]
    assert data["rerank_items"] == data["rrf_items"]


def test_search_without_rerank_keeps_list_shape(client, fake_embedding):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _add_summary(db, book_id, 0, "alpha")
    assert _build_index(client, book_id)["code"] == 200
    data = client.post(
        f"/api/books/{book_id}/summary-search", json={"query": "alpha"}
    ).json()["data"]
    assert isinstance(data, list)
