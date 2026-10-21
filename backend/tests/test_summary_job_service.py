"""摘要批量任务服务测试（fake LLM，不联网）。"""

from __future__ import annotations

import json

import pytest

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.chapter_summary import ChapterSummary
from app.models.extract_job import ExtractJob
from app.models.llm_provider import LlmProvider
from app.models.summary_issue import SummaryIssue
from app.services import summary_job_service


@pytest.fixture(autouse=True)
def reset_job_state():
    summary_job_service._active_job_id = None
    summary_job_service._stop_events.clear()
    yield
    summary_job_service._active_job_id = None
    summary_job_service._stop_events.clear()


def _payload(text="任务摘要。"):
    return {
        "summary": {
            "summary": text,
            "characters": ["林晚"],
            "cliffhanger": "",
            "key_events": ["林晚在旧楼发现旧案线索"],
        },
        "characters": {
            "mentions": [{"name": "林晚", "aliases": [], "role": "角色"}],
            "card_deltas": [],
            "new_aliases": [],
        },
        "lore": [],
    }


def _create_book(db, count=5):
    source_parts = []
    entries = []
    cursor = 0
    for idx in range(count):
        title = f"第{idx + 1}章 任务测试"
        text = f"{title}\n第{idx + 1}章正文。\n"
        entries.append((idx, title, cursor, cursor + len(text)))
        source_parts.append(text)
        cursor += len(text)
    source = "".join(source_parts)
    book = Book(
        title=f"任务书{count}",
        author="作者",
        synopsis="简介",
        protagonists=["林晚"],
        front_matter="",
        filename="job.txt",
        encoding="UTF-8",
        char_count=len(source),
        chapter_count=count,
        main_chapter_count=count,
    )
    db.add(book)
    db.flush()
    for idx, title, start, end in entries:
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=title,
                kind="main",
                char_start=start,
                char_end=end,
                word_count=end - start,
            )
        )
    db.add(
        LlmProvider(
            name=f"任务模型{book.id}",
            preset_key="custom",
            base_url="http://127.0.0.1:11434/v1",
            model="mock-model",
            is_active=True,
        )
    )
    db.commit()
    source_dir = UPLOADS_DIR / str(book.id)
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.txt").write_text(source, encoding="utf-8")
    return book.id


def _seed_success(db, book_id, idx, title):
    db.add(
        ChapterExtraction(
            book_id=book_id,
            chapter_idx=idx,
            title=title,
            status="success",
            prompt_version="chapter_extract_v1",
            parsed=_payload("旧。"),
        )
    )
    db.add(
        ChapterSummary(
            book_id=book_id,
            chapter_idx=idx,
            title=title,
            summary="旧。",
            key_events=[],
            characters=[],
            cliffhanger="",
            plot_threads=[],
            foreshadowing=[],
            reveals=[],
        )
    )
    db.commit()


def _fake_chat(monkeypatch, responses, gate=None):
    calls = []

    def fake(provider, messages, **kwargs):
        calls.append(messages)
        if gate is not None:
            gate.wait(10)
        value = responses.pop(0) if responses else _payload()
        if isinstance(value, dict):
            return value
        return {
            "content": value,
            "latency_ms": 3,
            "status_code": 200,
            "error": None,
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    return calls


def test_create_job_validation_and_planned_zero():
    with SessionLocal() as db:
        book_id = _create_book(db, count=1)
        _seed_success(db, book_id, 0, "第1章 任务测试")
        with pytest.raises(BusinessException) as empty:
            summary_job_service.create_job(db, book_id)
        assert "没有待处理章节" in empty.value.message

        with pytest.raises(BusinessException) as both:
            summary_job_service.create_job(db, book_id, limit=1, stop_at=2)
        assert both.value.code == 4000

        second = _create_book(db, count=2)
        db.query(LlmProvider).update({LlmProvider.is_active: False})
        db.commit()
        with pytest.raises(BusinessException) as no_provider:
            summary_job_service.create_job(db, second)
        assert "尚未配置模型服务" in no_provider.value.message


def test_create_job_rejects_existing_active():
    with SessionLocal() as db:
        book_id = _create_book(db, count=2)
        db.add(
            ExtractJob(
                book_id=book_id,
                status="running",
                stage="running",
                planned=2,
            )
        )
        db.commit()
        with pytest.raises(BusinessException) as busy:
            summary_job_service.create_job(db, book_id)
        assert busy.value.code == 4090


def test_run_job_success_and_next(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=5)
        _seed_success(db, book_id, 0, "第1章 任务测试")
        _seed_success(db, book_id, 1, "第2章 任务测试")
        job = summary_job_service.create_job(db, book_id)
        job_id = job.id
        assert job.planned == 3
        assert job.next_chapter_idx == 2

    _fake_chat(
        monkeypatch,
        [json.dumps(_payload("新。"), ensure_ascii=False) for _ in range(3)],
    )
    summary_job_service.run_job(job_id)
    with SessionLocal() as db:
        job = db.get(ExtractJob, job_id)
        assert job.status == "done"
        assert job.stage == "done"
        assert job.progress == 100
        assert job.generated == 3
        assert job.failed == 0
        assert job.next_chapter_idx is None
        assert job.finish_time is not None
        assert db.query(ChapterSummary).count() == 5
        assert summary_job_service.get_latest_job(db, book_id).id == job_id


def test_run_job_failure_continues(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
        job = summary_job_service.create_job(db, book_id, limit=3)
        job_id = job.id
    _fake_chat(
        monkeypatch,
        [
            json.dumps(_payload("一。"), ensure_ascii=False),
            {
                "content": None,
                "latency_ms": 1,
                "status_code": 429,
                "error": "HTTP 429",
            },
            json.dumps(_payload("三。"), ensure_ascii=False),
        ],
    )
    summary_job_service.run_job(job_id)
    with SessionLocal() as db:
        job = db.get(ExtractJob, job_id)
        assert job.status == "done"
        assert job.generated == 2
        assert job.failed == 1
        assert job.next_chapter_idx == 1
        assert db.query(SummaryIssue).filter_by(chapter_idx=1).one()


def test_request_stop_and_stopped(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
        job = summary_job_service.create_job(db, book_id, limit=3)
        job_id = job.id
        stopped = summary_job_service.request_stop(db, book_id)
        assert stopped.status == "stopping"
        assert stopped.stage == "stopping"
    _fake_chat(monkeypatch, [json.dumps(_payload("一。"), ensure_ascii=False)])
    summary_job_service.run_job(job_id)
    with SessionLocal() as db:
        job = db.get(ExtractJob, job_id)
        assert job.status == "stopped"
        assert job.stage == "stopped"
        assert job.generated == 1
        assert job.next_chapter_idx == 1
        with pytest.raises(BusinessException) as no_active:
            summary_job_service.request_stop(db, book_id)
        assert "当前没有" in no_active.value.message


def test_mark_interrupted_jobs():
    with SessionLocal() as db:
        book_id = _create_book(db, count=2)
        db.add(
            ExtractJob(book_id=book_id, status="running", stage="running")
        )
        db.add(
            ExtractJob(book_id=book_id, status="stopping", stage="stopping")
        )
        db.commit()
        count = summary_job_service.mark_interrupted_jobs(db)
        assert count == 2
        assert (
            db.query(ExtractJob)
            .filter(ExtractJob.status == "interrupted")
            .count()
            == 2
        )
        job = db.query(ExtractJob).first()
        assert "服务重启" in job.message
        assert job.finish_time is not None


def test_done_with_failures_message_and_failed_chapters(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
        db.add(SummaryIssue(book_id=book_id, chapter_idx=1, error="HTTP 429"))
        db.add(SummaryIssue(book_id=book_id, chapter_idx=2, error="超时"))
        db.commit()
        job = summary_job_service.create_job(db, book_id, limit=3)
        job_id = job.id

    def fake_batch(db, book_id, **kwargs):
        return {
            "book_id": book_id,
            "planned": 3,
            "generated": 1,
            "failed": 2,
            "stopped": False,
            "next_chapter_idx": None,
            "next_chapter_number": None,
            "next_chapter_title": None,
            "elapsed_seconds": 1.0,
            "provider": "本地测试模型",
            "alias_dropped": 2,
            "alias_flagged": 1,
            "check_disabled": True,
        }

    monkeypatch.setattr(
        "app.services.chapter_extract_service.extract_batch", fake_batch
    )
    summary_job_service.run_job(job_id)

    with SessionLocal() as db:
        job = db.get(ExtractJob, job_id)
        assert job.status == "done"
        assert "2 章失败" in job.message
        assert "修正 2 条称谓" in job.message
        assert "1 条称谓待核对" in job.message
        assert "已跳过后续复核" in job.message
        data = summary_job_service.job_to_dict(job, db)
        assert data["has_errors"] is True
        assert [row["chapter_idx"] for row in data["failed_chapters"]] == [1, 2]
        assert data["failed_chapters"][1]["error"] == "超时"

        clean = summary_job_service.job_to_dict(job)
        assert "failed_chapters" not in clean
        assert clean["has_errors"] is True
