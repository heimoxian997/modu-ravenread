"""文本索引 API 测试：后台任务、4090、检索与删除。"""

import shutil
import time
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.disk_db

from app import config
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chunk import Chunk
from app.models.index_job import IndexJob
from app.services import embedding_service, text_index_job_service, text_index_service


@pytest.fixture()
def qdrant_env(monkeypatch, tmp_path):
    root = tmp_path / "resources"
    path = root / "storage"
    monkeypatch.setattr(config, "QDRANT_PATH", path)
    monkeypatch.setattr(config, "TEXT_INDEX_COLLECTION", "novel_text_api")
    monkeypatch.setattr(config, "TEXT_INDEX_VERSION", 1)
    text_index_service.reset_client()
    text_index_job_service._active_job_id = None
    try:
        yield path
    finally:
        text_index_service.reset_client()
        text_index_job_service._active_job_id = None
        pass  # 统一由会话资源管理器回收


def _fake_dense(text: str):
    vector = [0.0] * 8
    if "苹果" in text:
        vector[0] = 1.0
    else:
        vector[1] = 1.0
    return vector


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
        embedding_service, "ensure_model", lambda progress=None: Path("fake")
    )
    monkeypatch.setattr(
        embedding_service, "get_embedder", lambda progress=None: object()
    )
    monkeypatch.setattr(
        embedding_service,
        "encode_documents",
        lambda texts, batch_size=None, max_length=None: [
            embedding_service.EmbeddedDoc(
                dense=_fake_dense(text),
                sparse={"苹果": 1.0} if "苹果" in text else {"其他": 1.0},
            )
            for text in texts
        ],
    )
    monkeypatch.setattr(
        embedding_service,
        "encode_query",
        lambda text: embedding_service.EmbeddedDoc(
            dense=_fake_dense(text),
            sparse={"苹果": 1.0} if "苹果" in text else {"其他": 1.0},
        ),
    )


def _create_book(db, title="API索引书"):
    book = Book(
        title=title,
        author="测试",
        protagonists=[],
        front_matter="",
        filename=f"{title}.txt",
        encoding="UTF-8",
        char_count=1000,
        chapter_count=1,
        main_chapter_count=1,
    )
    db.add(book)
    db.flush()
    chapter = Chapter(
        book_id=book.id,
        idx=0,
        title="第1章 API",
        kind="main",
        char_start=0,
        char_end=800,
        word_count=800,
    )
    db.add(chapter)
    db.flush()
    parent = Chunk(
        book_id=book.id,
        version=1,
        chapter_idx=0,
        level="parent",
        parent_id=None,
        seq=0,
        text="苹果很甜。苹果园在山上。",
        char_start=0,
        char_end=12,
    )
    db.add(parent)
    db.flush()
    for seq, text in enumerate(["苹果很甜。", "苹果园在山上。"]):
        db.add(
            Chunk(
                book_id=book.id,
                version=1,
                chapter_idx=0,
                level="child",
                parent_id=parent.id,
                seq=seq,
                text=text,
                char_start=0 if seq == 0 else 5,
                char_end=5 if seq == 0 else 12,
            )
        )
    db.commit()
    return book.id


def _wait_done(client, book_id: int, timeout: float = 10.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        response = client.get(f"/api/books/{book_id}/text-index").json()
        assert response["code"] == 200, response
        last = response["data"]
        job = last.get("job")
        if (
            job
            and job["status"] in {"done", "failed", "interrupted"}
            and not last.get("busy")
        ):
            return last
        time.sleep(0.05)
    raise AssertionError(f"任务超时未结束：{last}")


def test_api_full_flow(qdrant_env, fake_embedding, client):
    with SessionLocal() as db:
        book_id = _create_book(db)

    started = client.post(f"/api/books/{book_id}/text-index")
    assert started.status_code == 200
    payload = started.json()
    assert payload["code"] == 200
    assert payload["data"]["status"] in {"queued", "running"}

    status = _wait_done(client, book_id)
    assert status["built"] is True
    assert status["available"] is True
    assert status["point_count"] == 2
    assert status["dim"] == 8
    assert status["job"]["status"] == "done"
    assert status["job"]["progress"] == 100

    searched = client.post(
        f"/api/books/{book_id}/search",
        json={"query": "苹果", "top_k": 3},
    ).json()
    assert searched["code"] == 200
    assert searched["data"]
    assert "苹果" in searched["data"][0]["text"]
    assert searched["data"][0]["dense_score"] > 0
    assert searched["data"][0]["sparse_score"] > 0

    deleted = client.delete(f"/api/books/{book_id}/text-index").json()
    assert deleted["code"] == 200
    assert deleted["data"]["deleted"] == 2
    after = client.get(f"/api/books/{book_id}/text-index").json()["data"]
    assert after["built"] is False
    assert after["point_count"] == 0


def test_api_busy_returns_4090(qdrant_env, fake_embedding, client):
    with SessionLocal() as db:
        book_id = _create_book(db)
        db.add(
            IndexJob(
                book_id=book_id,
                kind="text_vector",
                status="running",
                stage="encoding",
                progress=35,
                message="编码中",
            )
        )
        db.commit()

    start = client.post(f"/api/books/{book_id}/text-index").json()
    assert start["code"] == 4090
    search = client.post(
        f"/api/books/{book_id}/search", json={"query": "苹果"}
    ).json()
    assert search["code"] == 4090
    deleted = client.delete(f"/api/books/{book_id}/text-index").json()
    assert deleted["code"] == 4090
    status = client.get(f"/api/books/{book_id}/text-index").json()["data"]
    assert status["busy"] is True


def test_api_validation_and_missing_book(qdrant_env, fake_embedding, client):
    with SessionLocal() as db:
        book_id = _create_book(db)

    blank = client.post(
        f"/api/books/{book_id}/search", json={"query": "   "}
    )
    assert blank.status_code == 422
    too_many = client.post(
        f"/api/books/{book_id}/search", json={"query": "苹果", "top_k": 21}
    )
    assert too_many.status_code == 422
    zero = client.post(
        f"/api/books/{book_id}/search", json={"query": "苹果", "top_k": 0}
    )
    assert zero.status_code == 422

    missing = 99999
    assert client.get(f"/api/books/{missing}/text-index").json()["code"] == 4040
    assert client.post(f"/api/books/{missing}/text-index").json()["code"] == 4040
    assert (
        client.post(
            f"/api/books/{missing}/search", json={"query": "苹果"}
        ).json()["code"]
        == 4040
    )
    assert (
        client.delete(f"/api/books/{missing}/text-index").json()["code"] == 4040
    )


def test_search_with_rerank_returns_two_orders(
    qdrant_env, fake_embedding, client, monkeypatch
):
    with SessionLocal() as db:
        book_id = _create_book(db)
    client.post(f"/api/books/{book_id}/text-index")
    _wait_done(client, book_id)

    monkeypatch.setattr(
        "app.services.rerank_service.rerank",
        lambda query, documents: (
            [0.1 * (index + 1) for index in range(len(documents))],
            None,
        ),
    )
    monkeypatch.setattr(
        "app.services.rerank_service.get_last_run_info",
        lambda: {"latency_ms": 9},
    )
    data = client.post(
        f"/api/books/{book_id}/search",
        json={"query": "苹果", "top_k": 2, "rerank": True},
    ).json()["data"]

    assert isinstance(data, dict)
    assert data["rerank_applied"] is True
    assert data["rerank_latency_ms"] == 9
    assert len(data["rrf_items"]) == 2
    # 分数是递增的，重排后顺序应当与 RRF 相反
    assert data["rerank_items"][0]["chunk_id"] == data["rrf_items"][1]["chunk_id"]
    assert data["rerank_items"][0]["rrf_rank"] == 2
    assert data["rerank_items"][0]["rank_delta"] == 1


def test_search_rerank_degrades_to_rrf(qdrant_env, fake_embedding, client, monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
    client.post(f"/api/books/{book_id}/text-index")
    _wait_done(client, book_id)

    monkeypatch.setattr(
        "app.services.rerank_service.rerank",
        lambda query, documents: (None, "重排模型缺失：/nope"),
    )
    data = client.post(
        f"/api/books/{book_id}/search",
        json={"query": "苹果", "top_k": 2, "rerank": True},
    ).json()["data"]

    assert data["rerank_applied"] is False
    assert "重排模型缺失" in data["rerank_error"]
    assert data["rerank_items"] == data["rrf_items"]


def test_search_without_rerank_keeps_list_shape(
    qdrant_env, fake_embedding, client
):
    with SessionLocal() as db:
        book_id = _create_book(db)
    client.post(f"/api/books/{book_id}/text-index")
    _wait_done(client, book_id)
    data = client.post(
        f"/api/books/{book_id}/search", json={"query": "苹果", "top_k": 2}
    ).json()["data"]
    assert isinstance(data, list)
