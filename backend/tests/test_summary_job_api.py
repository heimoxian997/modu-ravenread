"""摘要批量任务 API 测试（fake LLM，不联网）。"""

from __future__ import annotations

import json
import threading
import time

import pytest

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.extract_job import ExtractJob
from app.models.llm_provider import LlmProvider
from app.services import summary_job_service


@pytest.fixture(autouse=True)
def reset_job_state():
    summary_job_service._active_job_id = None
    summary_job_service._stop_events.clear()
    yield
    summary_job_service._active_job_id = None
    summary_job_service._stop_events.clear()


def _payload(text="API 任务摘要。"):
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


def _create_book(db, count=3):
    source_parts = []
    entries = []
    cursor = 0
    for idx in range(count):
        title = f"第{idx + 1}章 API任务"
        text = f"{title}\n正文{idx + 1}。\n"
        entries.append((idx, title, cursor, cursor + len(text)))
        source_parts.append(text)
        cursor += len(text)
    source = "".join(source_parts)
    book = Book(
        title=f"API任务书{count}",
        author="作者",
        protagonists=["林晚"],
        synopsis="",
        front_matter="",
        filename="api-job.txt",
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
            name=f"API任务模型{book.id}",
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
            "latency_ms": 2,
            "status_code": 200,
            "error": None,
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    return calls


def _wait_status(client, book_id, statuses, timeout=10):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        data = client.get(
            f"/api/books/{book_id}/summaries/jobs/latest"
        ).json()["data"]
        last = data
        if data and data["status"] in statuses:
            return data
        time.sleep(0.05)
    raise AssertionError(f"任务未到目标状态：{last}")


def test_job_api_success_and_get(client, monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
    _fake_chat(
        monkeypatch,
        [json.dumps(_payload("一。"), ensure_ascii=False) for _ in range(2)],
    )
    started = client.post(
        f"/api/books/{book_id}/summaries/jobs", json={"limit": 2}
    )
    assert started.status_code == 200
    assert started.json()["code"] == 200
    job_id = started.json()["data"]["id"]
    assert started.json()["data"]["status"] in {"queued", "running"}

    done = _wait_status(client, book_id, {"done", "failed", "stopped"})
    assert done["status"] == "done"
    assert done["planned"] == 2
    assert done["generated"] == 2
    assert done["failed"] == 0
    assert done["next_chapter_idx"] == 2
    assert done["progress"] == 100

    latest = client.get(
        f"/api/books/{book_id}/summaries/jobs/latest"
    ).json()["data"]
    assert latest["id"] == job_id
    by_id = client.get(
        f"/api/books/{book_id}/summaries/jobs/{job_id}"
    ).json()["data"]
    assert by_id["id"] == job_id
    stop = client.post(f"/api/books/{book_id}/summaries/jobs/stop").json()
    assert stop["code"] == 4000


def test_job_api_stop_after_current_chapter(client, monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db, count=3)
    gate = threading.Event()
    _fake_chat(
        monkeypatch,
        [json.dumps(_payload("一。"), ensure_ascii=False)],
        gate=gate,
    )
    started = client.post(
        f"/api/books/{book_id}/summaries/jobs", json={"limit": 3}
    ).json()
    assert started["code"] == 200
    job_id = started["data"]["id"]
    running = _wait_status(client, book_id, {"running"})
    assert running["id"] == job_id

    stopping = client.post(
        f"/api/books/{book_id}/summaries/jobs/stop"
    ).json()
    assert stopping["code"] == 200
    assert stopping["data"]["status"] == "stopping"
    gate.set()
    stopped = _wait_status(client, book_id, {"stopped", "done", "failed"})
    assert stopped["status"] == "stopped"
    assert stopped["generated"] == 1
    assert stopped["next_chapter_idx"] == 1


def test_job_api_errors(client):
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

    busy = client.post(
        f"/api/books/{book_id}/summaries/jobs", json={"limit": 1}
    ).json()
    assert busy["code"] == 4090

    with SessionLocal() as db:
        db.query(ExtractJob).delete()
        db.commit()
    mutex = client.post(
        f"/api/books/{book_id}/summaries/jobs",
        json={"limit": 1, "stop_at": 2},
    ).json()
    assert mutex["code"] == 4000

    with SessionLocal() as db:
        db.query(LlmProvider).update({LlmProvider.is_active: False})
        db.commit()
    no_provider = client.post(
        f"/api/books/{book_id}/summaries/jobs", json={"limit": 1}
    ).json()
    assert no_provider["code"] == 4000

    no_stop = client.post(f"/api/books/{book_id}/summaries/jobs/stop").json()
    assert no_stop["code"] == 4000
    missing = client.get(
        "/api/books/99999/summaries/jobs/latest"
    ).json()
    assert missing["code"] == 4040
    wrong = client.get(
        f"/api/books/{book_id}/summaries/jobs/99999"
    ).json()
    assert wrong["code"] == 4040
