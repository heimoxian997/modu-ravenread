"""章节摘要 API 全流程测试。"""

from __future__ import annotations

import json

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.llm_provider import LlmProvider


def _payload(summary="API 摘要。"):
    return {
        "summary": {
            "summary": summary,
            "characters": ["林晚"],
            "cliffhanger": "有人敲门。",
            "key_events": ["林晚在旧楼发现旧案线索"],
        },
        "characters": {
            "mentions": [{"name": "林晚", "aliases": [], "role": "调查者"}],
            "card_deltas": [],
            "new_aliases": [],
        },
        "lore": [],
    }


def _create_book(db):
    titles = ["第1章 测试", "第2章 测试", "番外 测试"]
    bodies = ["第一章正文。", "第二章正文。", "番外正文。"]
    source_parts = []
    entries = []
    cursor = 0
    for index, (title, body) in enumerate(zip(titles, bodies)):
        text = f"{title}\n{body}\n"
        kind = "extra" if index == 2 else "main"
        entries.append((title, kind, cursor, cursor + len(text)))
        source_parts.append(text)
        cursor += len(text)
    source = "".join(source_parts)
    book = Book(
        title="摘要 API 测试书",
        author="作者",
        synopsis="简介",
        protagonists=["林晚"],
        front_matter="",
        filename="api-summary.txt",
        encoding="UTF-8",
        char_count=len(source),
        chapter_count=3,
        main_chapter_count=2,
    )
    db.add(book)
    db.flush()
    for idx, (title, kind, start, end) in enumerate(entries):
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=title,
                kind=kind,
                char_start=start,
                char_end=end,
                word_count=end - start,
            )
        )
    db.add(
        LlmProvider(
            name="API 本地模型",
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


def _fake_chat(monkeypatch, responses):
    calls = []

    def fake(provider, messages, **kwargs):
        calls.append(messages)
        raw = responses.pop(0)
        return {
            "content": raw,
            "latency_ms": 10,
            "status_code": 200,
            "error": None,
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    return calls


def test_summary_api_flow(client, monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)

    _fake_chat(
        monkeypatch, [json.dumps(_payload("API 生成摘要。"), ensure_ascii=False)]
    )
    extracted = client.post(f"/api/books/{book_id}/chapters/0/extract")
    assert extracted.status_code == 200
    body = extracted.json()
    assert body["code"] == 200
    assert body["data"]["status"] == "success"
    assert body["data"]["summary"]["summary"] == "API 生成摘要。"
    assert body["data"]["raw_content"] is None

    overview = client.get(f"/api/books/{book_id}/summaries/overview").json()[
        "data"
    ]
    assert overview == {
        "available_done": 1,
        "follow_done": 0,
        "follow_partial": 0,
        "next_available_number": 2,
        "total": 2,
        "done": 1,
        "failed": 0,
        "pending": 1,
        "next_chapter_idx": 1,
        "next_chapter_number": 2,
        "next_chapter_title": "第2章 测试",
    }

    listed = client.get(
        f"/api/books/{book_id}/summaries?page=1&page_size=10&status=all"
    ).json()["data"]
    assert listed["total"] == 2
    assert [item["status"] for item in listed["items"]] == [
        "done",
        "pending",
    ]
    assert listed["items"][0]["summary_preview"].startswith("API")

    detail = client.get(f"/api/books/{book_id}/summaries/0").json()["data"]
    assert detail["status"] == "done"
    assert detail["summary"]["summary"] == "API 生成摘要。"
    assert detail["extraction"]["parsed"]["lore"] == []
    assert detail["extraction"]["alias_check"]["checked"] == 0
    assert detail["extraction"]["alias_check"]["issues"] == []

    pending = client.get(f"/api/books/{book_id}/summaries/1").json()["data"]
    assert pending["status"] == "pending"
    assert pending["summary"] is None

    edited = client.put(
        f"/api/books/{book_id}/summaries/0",
        json={
            "summary": "人工修改后的摘要。",
            "key_events": ["修改后的事件"],
            "characters": ["林晚", "秦屿"],
            "cliffhanger": "新的钩子",
        },
    )
    assert edited.status_code == 200
    edited_data = edited.json()["data"]
    assert edited_data["summary"]["summary"] == "人工修改后的摘要。"
    assert edited_data["summary"]["key_events"] == ["修改后的事件"]
    assert edited_data["summary"]["edited"] is True
    assert "plot_threads" not in edited_data["summary"]
    assert "foreshadowing" not in edited_data["summary"]
    assert "reveals" not in edited_data["summary"]
    assert (
        edited_data["extraction"]["parsed"]["summary"]["summary"]
        == "API 生成摘要。"
    )
    assert edited_data["extraction"]["parsed"]["summary"]["key_events"] == [
        "林晚在旧楼发现旧案线索"
    ]

    invalid = client.put(
        f"/api/books/{book_id}/summaries/0",
        json={"summary": "长" * 401},
    ).json()
    assert invalid["code"] == 4000

    pending_edit = client.put(
        f"/api/books/{book_id}/summaries/1", json={"summary": "没有摘要"}
    ).json()
    assert pending_edit["code"] == 4000
    assert "还没有摘要" in pending_edit["message"]

    extra_extract = client.post(f"/api/books/{book_id}/chapters/2/extract").json()
    assert extra_extract["code"] == 4000
    assert "番外" in extra_extract["message"]

    missing = client.get("/api/books/99999/summaries/overview").json()
    assert missing["code"] == 4040


def test_summary_api_filter_and_page(client, monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
    _fake_chat(
        monkeypatch,
        [
            json.dumps(_payload("一。"), ensure_ascii=False),
            json.dumps(_payload("二。"), ensure_ascii=False),
        ],
    )
    assert (
        client.post(f"/api/books/{book_id}/chapters/0/extract").json()["data"][
            "status"
        ]
        == "success"
    )
    assert (
        client.post(f"/api/books/{book_id}/chapters/1/extract").json()["data"][
            "status"
        ]
        == "success"
    )
    pending = client.get(
        f"/api/books/{book_id}/summaries?status=pending"
    ).json()["data"]
    assert pending["total"] == 0
    done = client.get(f"/api/books/{book_id}/summaries?status=done").json()[
        "data"
    ]
    assert done["total"] == 2
    assert done["page_size"] == 20
    assert done["page"] == 1
