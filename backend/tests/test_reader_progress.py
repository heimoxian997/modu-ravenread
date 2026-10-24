"""reader_progress、单工具端点和 MCP 代理测试。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.character import Character


def _book(db, *, with_source=True):
    book = Book(
        title="合成测试书",
        author="作者",
        synopsis="",
        protagonists=[],
        front_matter="",
        filename="progress.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=2,
        main_chapter_count=2,
    )
    db.add(book)
    db.flush()
    bodies = ["第一段甲某走进院子。", "第二段乙某在檐下缝衣。"]
    parts = []
    cursor = 0
    for idx, body in enumerate(bodies):
        title = f"第{idx + 1}章"
        text = f"{title}\n{body}\n"
        parts.append(text)
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=title,
                kind="main",
                char_start=cursor,
                char_end=cursor + len(text),
                word_count=len(body),
            )
        )
        cursor += len(text)
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
    if with_source:
        source_dir = UPLOADS_DIR / str(book.id)
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "source.txt").write_text("".join(parts), encoding="utf-8")
    return book.id


def _api(response):
    body = response.json()
    assert body["code"] == 200, body
    return body["data"]


def test_progress_round_trip_and_latest(client):
    with SessionLocal() as db:
        first = _book(db)
        second = _book(db)
    empty = _api(client.get(f"/api/books/{first}/progress"))
    assert empty["available"] is False

    saved = _api(
        client.put(
            f"/api/books/{first}/progress",
            json={"chapter_idx": 1, "char_offset": 7},
        )
    )
    assert saved["available"] is True
    assert saved["chapter_idx"] == 1
    assert saved["char_offset"] == 7
    assert saved["safe_chapter"] == 1

    loaded = _api(client.get(f"/api/books/{first}/progress"))
    assert loaded["chapter_idx"] == 1

    latest = _api(client.get("/api/reading-context"))
    assert latest["book_id"] == first

    _api(
        client.put(
            f"/api/books/{second}/progress",
            json={"chapter_idx": 0, "char_offset": 0},
        )
    )
    latest = _api(client.get("/api/reading-context"))
    assert latest["book_id"] == second


def test_progress_rejects_unknown_chapter(client):
    with SessionLocal() as db:
        book_id = _book(db)
    body = client.put(
        f"/api/books/{book_id}/progress",
        json={"chapter_idx": 99, "char_offset": 0},
    ).json()
    assert body["code"] == 4040


def test_tool_endpoint_requires_progress_and_respects_cap(client):
    with SessionLocal() as db:
        book_id = _book(db)
    no_progress = client.post(
        f"/api/books/{book_id}/agent/tool",
        json={"tool": "read_chapter", "args": {"chapter_idx": 0}},
    ).json()
    assert no_progress["code"] == 4000

    _api(
        client.put(
            f"/api/books/{book_id}/progress",
            json={"chapter_idx": 1, "char_offset": 0},
        )
    )
    ok = client.post(
        f"/api/books/{book_id}/agent/tool",
        json={"tool": "read_chapter", "args": {"chapter_idx": 0}},
    ).json()
    assert ok["code"] == 200
    assert ok["data"]["ok"] is True
    assert ok["data"]["data"]["chapter_idx"] == 0
    assert ok["data"]["chapter_idx"] == 1

    out = client.post(
        f"/api/books/{book_id}/agent/tool",
        json={"tool": "read_chapter", "args": {"chapter_idx": 2}},
    ).json()
    assert out["code"] == 200
    assert out["data"]["ok"] is False
    assert out["data"]["error"] == "chapter_out_of_range"
    assert out["data"]["data"]["allowed_max"] == 1


def test_tool_endpoint_returns_tool_unavailable_for_hidden_tool(client):
    with SessionLocal() as db:
        book_id = _book(db)
    _api(
        client.put(
            f"/api/books/{book_id}/progress",
            json={"chapter_idx": 1, "char_offset": 0},
        )
    )
    body = client.post(
        f"/api/books/{book_id}/agent/tool",
        json={"tool": "search_text", "args": {"query": "某词"}},
    ).json()
    assert body["code"] == 200
    assert body["data"]["ok"] is False
    assert body["data"]["error"] == "tool_unavailable"


def test_mcp_server_tools_are_registered_and_proxy(monkeypatch):
    mcp_server = pytest.importorskip("app.mcp_server")
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {
        "list_books",
        "get_reading_context",
        "read_around",
        "read_chapter",
        "search_text",
        "search_summaries",
        "get_chapter_summary",
        "get_character",
        "get_character_timeline",
        "search_character_changes",
        "resolve_alias",
        "list_alias_candidates",
        "search_lore",
    } <= names

    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        if path == "/api/reading-context":
            return {
                "ok": True,
                "data": {
                    "available": True,
                    "book_id": 7,
                    "chapter_idx": 4,
                    "char_offset": 120,
                    "safe_chapter": 4,
                },
            }
        if path == "/api/books/7/agent/tool":
            return {
                "ok": True,
                "data": {"ok": True, "data": {"text": "结果"}},
            }
        return {"ok": False, "error": "unexpected"}

    monkeypatch.setattr(mcp_server, "_request", fake_request)
    result = mcp_server._tool(None, "read_chapter", {"chapter_idx": 3})
    assert result["ok"] is True
    assert result["data"]["text"] == "结果"
    assert calls[0][0] == "GET"
    assert calls[0][1] == "/api/reading-context"
    assert calls[1] == (
        "POST",
        "/api/books/7/agent/tool",
        {"json": {"tool": "read_chapter", "args": {"chapter_idx": 3}}},
    )


def test_mcp_server_without_context():
    mcp_server = pytest.importorskip("app.mcp_server")
    assert mcp_server._context is not None
    # 不触发网络；只验证无 progress 的错误对象结构。
    original = mcp_server._request
    try:
        mcp_server._request = lambda method, path, **kwargs: {
            "ok": True,
            "data": {"available": False},
        }
        result = mcp_server._context(None)
        assert result["ok"] is False
        assert result["error"] == "no_reading_context"
    finally:
        mcp_server._request = original
