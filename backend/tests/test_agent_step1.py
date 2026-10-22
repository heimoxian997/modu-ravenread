"""伴读 agent Step 1：LangGraph、当前章工具、记忆与 API 测试。"""

from __future__ import annotations

import json

import pytest

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.agent_message import AgentMessage
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.services import (
    agent_context_service,
    agent_service,
    agent_tool_service,
    text_window_service,
)


def _book(db, bodies=("第一段甲某走进院子。\n第二段乙某在檐下缝衣。", "第三段丙某在门口站着。")):
    titles = [f"第{index + 1}章 测试" for index in range(len(bodies))]
    entries = []
    parts = []
    cursor = 0
    for title, body in zip(titles, bodies):
        text = f"{title}\n{body}\n"
        entries.append((title, body, cursor, cursor + len(text)))
        parts.append(text)
        cursor += len(text)
    source = "".join(parts)
    book = Book(
        title="合成测试书",
        author="作者",
        synopsis="",
        protagonists=["甲某"],
        front_matter="",
        filename="agent.txt",
        encoding="UTF-8",
        char_count=len(source),
        chapter_count=len(entries),
        main_chapter_count=len(entries),
    )
    db.add(book)
    db.flush()
    for idx, (title, body, start, end) in enumerate(entries):
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=title,
                kind="main",
                char_start=start,
                char_end=end,
                word_count=len(body),
            )
        )
    source_dir = UPLOADS_DIR / str(book.id)
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.txt").write_text(source, encoding="utf-8")
    db.add(
        Character(
            book_id=book.id,
            canonical_name="测试角色",
            first_seen_chapter=0,
            last_seen_chapter=0,
            mention_count=0,
        )
    )
    db.commit()
    return book.id


def _chapter_end(db, book_id, idx):
    row = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.idx == idx)
        .one()
    )
    return row.char_end


def _fake_model(monkeypatch, responses):
    calls = []

    def fake(db, provider_id, messages, timeout=45):
        calls.append({"provider_id": provider_id, "messages": messages})
        value = responses.pop(0)
        if isinstance(value, dict):
            return value
        return {"content": value, "model": "fake", "error": None}

    monkeypatch.setattr("app.services.agent_runtime.call_model", fake)
    return calls


def _tool_call(tool="read_around", args=None):
    return json.dumps(
        {"action": "tool", "calls": [{"tool": tool, "args": args or {}}]},
        ensure_ascii=False,
    )


def _answer(text, citations=None):
    return json.dumps(
        {
            "action": "answer",
            "answer": text,
            "citations": citations or [],
        },
        ensure_ascii=False,
    )


def test_chat_tool_then_answer_and_persists_session(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
        calls = _fake_model(
            monkeypatch,
            [
                _tool_call(),
                _answer(
                    "第1章：甲某走进院子。",
                    [{"chapter_idx": 0, "quote": "甲某走进院子"}],
                ),
            ],
        )
        data = agent_service.chat(db, book_id, "刚才谁走进了院子", 0, end)
        assert data["reply"].startswith("第1章")
        assert data["citation_verified"] is True
        assert data["tool_trace"][0]["tool"] == "read_around"
        assert len(calls) == 2
        assert "read_around" in calls[0]["messages"][0]["content"]
        session = agent_context_service.get_or_create_session(db, book_id)
        messages = (
            db.query(AgentMessage)
            .filter(AgentMessage.session_id == session.id)
            .order_by(AgentMessage.id.asc())
            .all()
        )
        assert [item.role for item in messages] == ["user", "assistant"]
        assert "第1章" in messages[1].content


def test_chapter_gate_uses_current_chapter(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        body = text_window_service.get_chapter_content(db, book_id, 1)
        offset = int(body["char_start"]) + 3
        assert agent_context_service.safe_chapter(db, book_id, 1, offset) == 1
        _fake_model(
            monkeypatch,
            [
                _tool_call(),
                _answer(
                    "第2章：门口这一段。",
                    [{"chapter_idx": 1, "quote": "第三段"}],
                ),
            ],
        )
        data = agent_service.chat(db, book_id, "门口这一段", 1, offset)
        assert data["citation_verified"] is True
        assert data["citations"][0]["chapter_idx"] == 1


def test_citation_without_quote_is_dropped(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        body = text_window_service.get_chapter_content(db, book_id, 1)
        offset = int(body["char_start"]) + 3
        _fake_model(
            monkeypatch,
            [
                _tool_call(),
                _answer(
                    "第2章：门口这一段。",
                    [{"chapter_idx": 1}],
                ),
            ],
        )
        data = agent_service.chat(db, book_id, "门口这一段", 1, offset)
        assert data["citations"] == []
        assert data["citation_verified"] is False


def test_out_of_range_citation_is_dropped(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        body = text_window_service.get_chapter_content(db, book_id, 1)
        offset = int(body["char_start"]) + 3
        _fake_model(
            monkeypatch,
            [
                _tool_call(),
                _answer(
                    "第3章：不存在。",
                    [{"chapter_idx": 2, "quote": "第三段"}],
                ),
            ],
        )
        data = agent_service.chat(db, book_id, "门口这一段", 1, offset)
        assert data["citations"] == []
        assert data["citation_verified"] is False


def test_tool_results_strip_character_ids():
    with SessionLocal() as db:
        book_id = _book(db)
        character = Character(
            book_id=book_id,
            canonical_name="甲某",
            first_seen_chapter=0,
            last_seen_chapter=0,
            mention_count=1,
        )
        db.add(character)
        db.flush()
        db.add(
            CharacterAlias(
                book_id=book_id,
                character_id=character.id,
                alias="甲某",
                first_seen_chapter=0,
                last_seen_chapter=0,
                occurrence_count=1,
                source="mention",
            )
        )
        db.commit()
        book = db.get(Book, book_id)
        chapter = (
            db.query(Chapter)
            .filter(Chapter.book_id == book_id, Chapter.idx == 0)
            .one()
        )
        body = text_window_service.get_chapter_content(db, book_id, 0)
        ctx = agent_tool_service.ToolContext(
            db=db,
            book=book,
            chapter=chapter,
            safe_chapter=0,
            char_offset=int(body["char_start"]) + 20,
        )
        result = agent_tool_service.execute(
            "read_around", {"anchor": "甲某", "radius": 1}, ctx
        )
        assert result["ok"] is True
        payload = json.dumps(result["data"], ensure_ascii=False)
        assert "character_ids" not in payload
        assert "ambiguous" not in payload


def test_invalid_json_is_repaired(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
        calls = _fake_model(
            monkeypatch,
            [
                "不是 JSON",
                _answer("第1章：窗口内容。", []),
            ],
        )
        data = agent_service.chat(db, book_id, "这一段说了什么", 0, end)
        assert data["reply"] == "第1章：窗口内容。"
        assert len(calls) == 2
        assert "只输出一个 JSON" in calls[1]["messages"][-1]["content"]


def test_provider_error_returns_readable_error(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
        _fake_model(
            monkeypatch,
            [{"content": None, "model": None, "error": "模型不可用"}],
        )
        data = agent_service.chat(db, book_id, "问题", 0, end)
        assert data["error"] == "模型不可用"
        assert data["reply"] == "模型不可用"


def test_history_filters_by_safe_chapter(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end1 = _chapter_end(db, book_id, 1)
        first = _fake_model(
            monkeypatch,
            [_answer("第一轮回答", [])],
        )
        agent_service.chat(db, book_id, "第一轮问题", 1, end1)
        assert first

        second = _fake_model(
            monkeypatch,
            [_answer("第二轮回答", [])],
        )
        agent_service.chat(db, book_id, "第二轮问题", 0, 0)
        prompt = second[0]["messages"][1]["content"]
        assert "第一轮回答" not in prompt


def test_two_tool_rounds_are_allowed(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
        calls = _fake_model(
            monkeypatch,
            [
                _tool_call(),
                _tool_call("search_summaries", {"query": "前情"}),
                _answer("第1章：两轮工具。", []),
            ],
        )
        monkeypatch.setattr(
            "app.services.agent_tool_service.execute",
            lambda tool, args, ctx: {"ok": True, "data": {}},
        )
        data = agent_service.chat(db, book_id, "前情是什么", 0, end)
        assert data["reply"] == "第1章：两轮工具。"
        assert len(calls) == 3
        assert [item["tool"] for item in data["tool_trace"]] == [
            "read_around",
            "search_summaries",
        ]


def test_tool_round_limit_wraps_up(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
        calls = _fake_model(
            monkeypatch,
            [
                *[_tool_call() for _ in range(6)],
                _answer("基于已查结果收尾。", []),
            ],
        )
        monkeypatch.setattr(
            "app.services.agent_tool_service.execute",
            lambda tool, args, ctx: {"ok": True, "data": {}},
        )
        data = agent_service.chat(db, book_id, "继续查", 0, end)
        assert len(calls) == 7
        assert len(data["tool_trace"]) == 5
        assert data["budget_exhausted"] == "rounds"
        assert data["reply"] == "基于已查结果收尾。"


def test_total_tool_budget_wraps_up(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
        monkeypatch.setattr(agent_service, "MAX_TOTAL_TOOL_CALLS", 1)
        calls = _fake_model(
            monkeypatch,
            [
                _tool_call(),
                _tool_call(),
                _answer("只有一条证据，收尾。", []),
            ],
        )
        monkeypatch.setattr(
            "app.services.agent_tool_service.execute",
            lambda tool, args, ctx: {"ok": True, "data": {}},
        )
        data = agent_service.chat(db, book_id, "继续查", 0, end)
        assert len(calls) == 3
        assert len(data["tool_trace"]) == 1
        assert data["budget_exhausted"] == "tools"
        assert data["reply"] == "只有一条证据，收尾。"


def test_book_lock_returns_4090(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
        _fake_model(monkeypatch, [_answer("回答", [])])
        lock = agent_service._book_lock(book_id)
        assert lock.acquire(blocking=False)
        try:
            with pytest.raises(BusinessException) as exc:
                agent_service.chat(db, book_id, "问题", 0, end)
            assert exc.value.code == 4090
        finally:
            lock.release()


def test_agent_api_session_chat_and_clear(client, monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        end = _chapter_end(db, book_id, 0)
    _fake_model(monkeypatch, [_answer("第1章：API 回答。", [])])
    response = client.post(
        f"/api/books/{book_id}/agent/chat",
        json={
            "message": "这一段说了什么",
            "chapter_idx": 0,
            "char_offset": end,
        },
    ).json()
    assert response["code"] == 200
    assert response["data"]["reply"] == "第1章：API 回答。"
    session = client.get(f"/api/books/{book_id}/agent/session").json()["data"]
    assert session["session_id"] is not None
    assert [item["role"] for item in session["messages"]] == [
        "user",
        "assistant",
    ]
    cleared = client.delete(f"/api/books/{book_id}/agent/session").json()
    assert cleared["data"]["cleared"] is True
    empty = client.get(f"/api/books/{book_id}/agent/session").json()["data"]
    assert empty["session_id"] is None
