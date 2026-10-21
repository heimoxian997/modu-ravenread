"""文本索引任务服务测试：不加载真实模型、不联网。"""

import shutil
import uuid
from pathlib import Path

import pytest

from app import config
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chunk import Chunk
from app.models.index_job import IndexJob
from app.services import embedding_service, text_index_job_service, text_index_service


@pytest.fixture()
def qdrant_env(monkeypatch):
    root = Path(__file__).parent / ".pytest-data" / "jobs" / uuid.uuid4().hex
    path = root / "storage"
    monkeypatch.setattr(config, "QDRANT_PATH", path)
    monkeypatch.setattr(config, "TEXT_INDEX_COLLECTION", "novel_text_jobs")
    monkeypatch.setattr(config, "TEXT_INDEX_VERSION", 1)
    text_index_service.reset_client()
    text_index_job_service._active_job_id = None
    try:
        yield path
    finally:
        text_index_service.reset_client()
        text_index_job_service._active_job_id = None
        shutil.rmtree(root, ignore_errors=True)


def _fake_dense(text: str):
    vector = [0.0] * 8
    if "苹果" in text:
        vector[0] = 1.0
    elif "汽车" in text:
        vector[1] = 1.0
    else:
        vector[2] = 1.0
    return vector


def _fake_sparse(text: str):
    if "苹果" in text:
        return {"苹果": 1.0}
    if "汽车" in text:
        return {"汽车": 1.0}
    return {"其他": 1.0}


def _fake_encode_documents(texts, batch_size=None, max_length=None):
    return [
        embedding_service.EmbeddedDoc(
            dense=_fake_dense(text), sparse=_fake_sparse(text)
        )
        for text in texts
    ]


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
        embedding_service, "encode_documents", _fake_encode_documents
    )
    monkeypatch.setattr(
        embedding_service,
        "encode_query",
        lambda text: embedding_service.EmbeddedDoc(
            dense=_fake_dense(text), sparse=_fake_sparse(text)
        ),
    )


def _create_book(db, title="任务测试书"):
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
        title="第1章 测试",
        kind="main",
        char_start=0,
        char_end=900,
        word_count=900,
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
        text="苹果很甜。汽车很快。",
        char_start=0,
        char_end=10,
    )
    db.add(parent)
    db.flush()
    child_ids = []
    for seq, text in enumerate(["苹果很甜。", "汽车很快。"]):
        child = Chunk(
            book_id=book.id,
            version=1,
            chapter_idx=0,
            level="child",
            parent_id=parent.id,
            seq=seq,
            text=text,
            char_start=sum(len(item) for item in ["苹果很甜。", "汽车很快。"][:seq]),
            char_end=sum(len(item) for item in ["苹果很甜。", "汽车很快。"][: seq + 1]),
        )
        db.add(child)
        db.flush()
        child_ids.append(child.id)
    db.commit()
    return book.id, child_ids


def test_run_job_success_and_progress(qdrant_env, fake_embedding, monkeypatch):
    with SessionLocal() as db:
        book_id, child_ids = _create_book(db)
        job = text_index_job_service.create_job(db, book_id)
        job_id = job.id

    updates = []
    original_update = text_index_job_service._update_progress

    def capture(job_id, stage, percent, message):
        updates.append((stage, percent, message))
        original_update(job_id, stage, percent, message)

    monkeypatch.setattr(text_index_job_service, "_update_progress", capture)
    text_index_job_service.run_job(job_id)

    with SessionLocal() as db:
        job = db.get(IndexJob, job_id)
        assert job.status == "done"
        assert job.stage == "done"
        assert job.progress == 100
        assert job.point_count == len(child_ids)
        assert job.error is None
        assert job.finish_time is not None

        status = text_index_job_service.get_status(db, book_id)
        assert status["built"] is True
        assert status["available"] is True
        assert status["point_count"] == len(child_ids)
        assert status["dim"] == 8
        assert status["busy"] is False
        assert status["job"]["status"] == "done"

    stages = {stage for stage, _, _ in updates}
    assert {"preparing", "loading", "encoding", "upserting"} <= stages
    assert max(percent for _, percent, _ in updates) >= 90


def test_busy_blocks_create_search_delete(client, qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, _ = _create_book(db)
        running = IndexJob(
            book_id=book_id,
            kind="text_vector",
            status="running",
            stage="encoding",
            progress=40,
            message="编码中",
        )
        db.add(running)
        db.commit()

        with pytest.raises(BusinessException) as create_exc:
            text_index_job_service.create_job(db, book_id)
        assert create_exc.value.code == 4090

        with pytest.raises(BusinessException) as search_exc:
            text_index_job_service.search(db, book_id, "苹果")
        assert search_exc.value.code == 4090

        with pytest.raises(BusinessException) as delete_exc:
            text_index_job_service.delete_index(db, book_id)
        assert delete_exc.value.code == 4090

        status = text_index_job_service.get_status(db, book_id)
        assert status["busy"] is True


def test_run_job_failure_sets_failed(qdrant_env, fake_embedding, monkeypatch):
    with SessionLocal() as db:
        book_id, _ = _create_book(db)
        job = text_index_job_service.create_job(db, book_id)
        job_id = job.id

    def broken_encode(texts, batch_size=None, max_length=None):
        raise RuntimeError("fake encode failure")

    monkeypatch.setattr(embedding_service, "encode_documents", broken_encode)
    text_index_job_service.run_job(job_id)

    with SessionLocal() as db:
        job = db.get(IndexJob, job_id)
        assert job.status == "failed"
        assert job.error
        assert "fake encode failure" in job.error
        assert job.finish_time is not None


def test_mark_interrupted_jobs_preserves_stage(qdrant_env):
    with SessionLocal() as db:
        book_id, _ = _create_book(db)
        job = IndexJob(
            book_id=book_id,
            kind="text_vector",
            status="running",
            stage="upserting",
            progress=80,
            message="写入向量",
        )
        db.add(job)
        db.commit()
        job_id = job.id

        count = text_index_job_service.mark_interrupted_jobs(db)
        assert count == 1
        refreshed = db.get(IndexJob, job_id)
        assert refreshed.status == "interrupted"
        assert refreshed.stage == "upserting"
        assert refreshed.message == "服务重启，任务已中断"
        assert refreshed.finish_time is not None


def test_delete_index_returns_zero_and_status_unbuilt(
    qdrant_env, fake_embedding
):
    with SessionLocal() as db:
        book_id, child_ids = _create_book(db)
        job = text_index_job_service.create_job(db, book_id)
        job_id = job.id

    text_index_job_service.run_job(job_id)

    with SessionLocal() as db:
        deleted = text_index_job_service.delete_index(db, book_id)
        assert deleted == len(child_ids)
        assert text_index_service.delete_book_index(book_id) == 0
        status = text_index_job_service.get_status(db, book_id)
        assert status["built"] is False
        assert status["point_count"] == 0


def test_status_qdrant_missing_and_open_failure(
    qdrant_env, fake_embedding, monkeypatch
):
    with SessionLocal() as db:
        book_id, _ = _create_book(db)
        status = text_index_job_service.get_status(db, book_id)
        assert status["available"] is True
        assert status["built"] is False
        assert status["point_count"] == 0
        assert status["dim"] == 0

        def fail_get_client():
            raise BusinessException(message="Qdrant 打开失败")

        monkeypatch.setattr(text_index_service, "get_client", fail_get_client)
        failed = text_index_job_service.get_status(db, book_id)
        assert failed["available"] is False
        assert failed["built"] is False
        assert "Qdrant 打开失败" in failed["error"]


def test_status_retries_transient_qdrant_error(
    qdrant_env, fake_embedding, monkeypatch
):
    with SessionLocal() as db:
        book_id, _ = _create_book(db)
        calls = []

        def flaky_stats(_book_id):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("index out of bounds")
            return {
                "available": True,
                "exists": True,
                "point_count": 0,
                "dim": 8,
            }

        monkeypatch.setattr(
            text_index_service, "get_index_stats", flaky_stats
        )
        status = text_index_job_service.get_status(db, book_id)
        assert status["available"] is True
        assert len(calls) == 2
        assert "error" not in status


def test_status_hides_persistent_internal_qdrant_error(
    qdrant_env, fake_embedding, monkeypatch
):
    with SessionLocal() as db:
        book_id, _ = _create_book(db)

        def broken_stats(_book_id):
            raise RuntimeError("index out of bounds")

        monkeypatch.setattr(
            text_index_service, "get_index_stats", broken_stats
        )
        status = text_index_job_service.get_status(db, book_id)
        assert status["available"] is False
        assert "out of bounds" not in status["error"]
        assert status["error"] == "本地 Qdrant 暂时不可用，请刷新重试。"


def test_latest_failed_upserting_sets_warning(qdrant_env, fake_embedding):
    with SessionLocal() as db:
        book_id, _ = _create_book(db)
        job = IndexJob(
            book_id=book_id,
            kind="text_vector",
            status="failed",
            stage="upserting",
            progress=85,
            message="建库失败",
            error="Qdrant error",
        )
        db.add(job)
        db.commit()

        status = text_index_job_service.get_status(db, book_id)
        assert status["warning"] == "索引可能不完整，请重新建库"
        assert status["job"]["error"] == "Qdrant error"
