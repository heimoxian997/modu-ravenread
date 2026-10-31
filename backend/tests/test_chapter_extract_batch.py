"""批量规划、断点与逐章失败继续测试（fake LLM，不联网）。"""

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
from app.models.llm_provider import LlmProvider
from app.models.summary_issue import SummaryIssue
from app.services import chapter_extract_service
from test_chapter_extract_service import (  # noqa: F401
    _create_book as _create_book_with_bodies,
    _payload as _extract_payload,
)


def _payload(text="批量摘要。"):
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


def _create_book(db, count=5, empty_idx=None):
    source_parts = []
    entries = []
    cursor = 0
    for idx in range(count):
        title = f"第{idx + 1}章 批量测试"
        body = "" if idx == empty_idx else f"第{idx + 1}章正文。"
        text = f"{title}\n{body}\n"
        entries.append((idx, title, cursor, cursor + len(text)))
        source_parts.append(text)
        cursor += len(text)
    source = "".join(source_parts)
    book = Book(
        title=f"批量书{count}{empty_idx}",
        author="作者",
        synopsis="简介",
        protagonists=["林晚"],
        front_matter="",
        filename="batch.txt",
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
            name=f"批量本地模型{book.id}",
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


def _seed_success(db, book_id, idx, title, edited=False):
    db.add(
        ChapterExtraction(
            book_id=book_id,
            chapter_idx=idx,
            title=title,
            status="success",
            prompt_version="chapter_extract_v1",
            provider_name="seed",
            model="seed",
            parsed=_payload("旧摘要。"),
        )
    )
    db.add(
        ChapterSummary(
            book_id=book_id,
            chapter_idx=idx,
            title=title,
            summary="旧摘要。",
            key_events=[],
            characters=[],
            cliffhanger="",
            plot_threads=[],
            foreshadowing=[],
            reveals=[],
            edited=edited,
        )
    )
    db.commit()


def _fake_chat(monkeypatch, responses):
    calls = []

    def fake(provider, messages, **kwargs):
        calls.append(messages)
        value = responses.pop(0)
        if isinstance(value, dict):
            return value
        return {
            "content": value,
            "latency_ms": 5,
            "status_code": 200,
            "error": None,
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    return calls


def test_plan_batch_selection_and_next():
    with SessionLocal() as db:
        book_id = _create_book(db, count=5)
        _seed_success(db, book_id, 0, "第1章 批量测试")
        _seed_success(db, book_id, 1, "第2章 批量测试")
        db.add(
            SummaryIssue(
                book_id=book_id,
                chapter_idx=3,
                error="old failure",
            )
        )
        db.commit()

        plan = chapter_extract_service.plan_batch(db, book_id)
        assert plan["candidates"] == [2, 3, 4]
        assert plan["planned"] == 5 - 2
        assert plan["done"] == 2
        assert plan["failed"] == 1
        assert plan["next_chapter_idx"] == 2
        assert plan["next_chapter_number"] == 3
        assert plan["next_chapter_title"] == "第3章 批量测试"

        limited = chapter_extract_service.plan_batch(db, book_id, limit=2)
        assert limited["candidates"] == [2, 3]

        stopped = chapter_extract_service.plan_batch(db, book_id, stop_at=3)
        assert stopped["candidates"] == [2]

        forced = chapter_extract_service.plan_batch(db, book_id, force=True)
        assert forced["candidates"] == [0, 1, 2, 3, 4]

        retried = chapter_extract_service.plan_batch(
            db, book_id, retry_failed=True
        )
        assert retried["candidates"] == [2, 3, 4]

        with pytest.raises(BusinessException) as mutex:
            chapter_extract_service.plan_batch(
                db, book_id, limit=1, stop_at=2
            )
        assert mutex.value.code == 4000
        assert "只能选一个" in mutex.value.message


def test_extract_batch_resumes_and_does_not_repeat(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=5)
    _fake_chat(
        monkeypatch,
        [json.dumps(_payload("一。"), ensure_ascii=False) for _ in range(5)],
    )
    with SessionLocal() as db:
        first = chapter_extract_service.extract_batch(db, book_id, limit=2)
        assert first["generated"] == 2
        assert first["failed"] == 0
        assert first["next_chapter_idx"] == 2
        assert db.query(ChapterSummary).count() == 2

        second = chapter_extract_service.extract_batch(db, book_id, limit=2)
        assert second["generated"] == 2
        assert second["next_chapter_idx"] == 4
        assert db.query(ChapterSummary).count() == 4
        third = chapter_extract_service.extract_batch(db, book_id, limit=2)
        assert third["generated"] == 1
        assert third["next_chapter_idx"] is None
        assert db.query(ChapterSummary).count() == 5


def test_stop_at_and_force_resets_edited(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=4)
        _seed_success(db, book_id, 0, "第1章 批量测试", edited=True)
    _fake_chat(
        monkeypatch,
        [json.dumps(_payload("重跑。"), ensure_ascii=False) for _ in range(5)],
    )
    with SessionLocal() as db:
        stopped = chapter_extract_service.extract_batch(
            db, book_id, stop_at=2
        )
        assert stopped["generated"] == 1
        assert stopped["next_chapter_idx"] == 2
        assert db.query(ChapterSummary).filter_by(chapter_idx=0).one().edited is True

        forced = chapter_extract_service.extract_batch(db, book_id, force=True)
        assert forced["generated"] == 4
        assert db.query(ChapterSummary).filter_by(chapter_idx=0).one().edited is False


def test_batch_continues_after_model_failure(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
    _fake_chat(
        monkeypatch,
        [
            json.dumps(_payload("一。"), ensure_ascii=False),
            {
                "content": None,
                "latency_ms": 5,
                "status_code": 429,
                "error": "模型服务返回 HTTP 429",
            },
            json.dumps(_payload("三。"), ensure_ascii=False),
        ],
    )
    with SessionLocal() as db:
        result = chapter_extract_service.extract_batch(db, book_id, limit=3)
        assert result["generated"] == 2
        assert result["failed"] == 1
        assert result["next_chapter_idx"] == 1
        assert db.query(SummaryIssue).filter_by(chapter_idx=1).one()
        assert db.query(ChapterSummary).count() == 2


def test_empty_chapter_writes_issue_and_continues(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3, empty_idx=1)
    calls = _fake_chat(
        monkeypatch,
        [
            json.dumps(_payload("一。"), ensure_ascii=False),
            json.dumps(_payload("三。"), ensure_ascii=False),
        ],
    )
    with SessionLocal() as db:
        result = chapter_extract_service.extract_batch(db, book_id, limit=3)
        assert len(calls) == 2
        assert result["generated"] == 2
        assert result["failed"] == 1
        assert db.query(SummaryIssue).filter_by(chapter_idx=1).one()
        assert db.query(ChapterSummary).count() == 2


def test_4100_aborts_batch_without_issue(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
    calls = _fake_chat(monkeypatch, [])
    with SessionLocal() as db:
        def broken(db_session, book, chapter):
            raise BusinessException(message="源文件丢失", code=4100)

        monkeypatch.setattr(
            chapter_extract_service, "_read_chapter_text", broken
        )
        with pytest.raises(BusinessException) as exc_info:
            chapter_extract_service.extract_batch(db, book_id, limit=3)
        assert exc_info.value.code == 4100
        assert calls == []
        assert db.query(SummaryIssue).count() == 0


def test_no_provider_fails_before_calls(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=2)
        db.query(LlmProvider).update({LlmProvider.is_active: False})
        db.commit()
    calls = _fake_chat(monkeypatch, [])
    with SessionLocal() as db:
        with pytest.raises(BusinessException) as exc_info:
            chapter_extract_service.extract_batch(db, book_id)
        assert "尚未配置模型服务" in exc_info.value.message
        assert calls == []


def test_should_stop_after_two_chapters(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=4)
    _fake_chat(
        monkeypatch,
        [json.dumps(_payload("摘要。"), ensure_ascii=False) for _ in range(2)],
    )
    processed = []
    with SessionLocal() as db:
        def progress(done, total, chapter_idx, result):
            processed.append(chapter_idx)

        result = chapter_extract_service.extract_batch(
            db,
            book_id,
            limit=4,
            progress=progress,
            should_stop=lambda: len(processed) >= 2,
        )
        assert result["generated"] == 2
        assert result["stopped"] is True
        assert result["next_chapter_idx"] == 2
        assert processed == [0, 1]


def test_check_circuit_breaker_across_chapters(monkeypatch):
    """检查模型连续两章空返回后，后续章节不再调用。"""
    with SessionLocal() as db:
        book_id = _create_book_with_bodies(
            db, bodies=("姐姐来了。", "姐姐走了。", "姐姐又来了。")
        )
        provider_id = db.query(LlmProvider).one().id

    calls = {"generate": 0, "check": 0}
    payload = _extract_payload("摘要。")
    payload["characters"]["mentions"] = [
        {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
        {"name": "林晚", "aliases": [], "role": ""},
    ]

    def fake(provider, messages, **kwargs):
        if provider.get("disable_thinking"):
            calls["check"] += 1
            return {
                "content": "",
                "latency_ms": 1,
                "status_code": 200,
                "error": None,
                "finish_reason": "length",
            }
        calls["generate"] += 1
        return {
            "content": json.dumps(payload, ensure_ascii=False),
            "latency_ms": 1,
            "status_code": 200,
            "error": None,
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    with SessionLocal() as db:
        result = chapter_extract_service.extract_batch(
            db, book_id, limit=3, check_provider_id=provider_id
        )

    assert result["generated"] == 3
    assert result["check_disabled"] is True
    assert calls["generate"] == 3
    # 前两章各一次首调 + 一次重试；第三章熔断后一次都不发。
    assert calls["check"] == 4


def test_overview_and_next_pending_fields():
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
        _seed_success(db, book_id, 0, "第1章 批量测试")
        overview = chapter_extract_service.get_overview(db, book_id)
        assert overview == {
            "total": 3,
            "done": 1,
            "available_done": 1,
            "follow_done": 0,
            "follow_partial": 0,
            "next_available_number": 2,
            "failed": 0,
            "pending": 2,
            "next_chapter_idx": 1,
            "next_chapter_number": 2,
            "next_chapter_title": "第2章 批量测试",
        }
        next_info = chapter_extract_service.get_next_pending(db, book_id)
        assert next_info["chapter_idx"] == 1
        assert next_info["chapter_number"] == 2
