"""L0 导航三件套：get_toc / search_text(exact) / read_span 的闸门测试。"""

from __future__ import annotations

import asyncio

import pytest

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter

CHAPTERS = [
    (
        "院门",
        "甲某把庚帖收进袖中，转身出了院门。\n"
        "甲某放下旧伞，又提起旧伞，最后还是把旧伞靠在门边。",
    ),
    ("拆信", "乙某当夜拆了庚帖，看罢便烧了个干净。"),
    ("送别", "丙某交出朱雀衔环，说是信物，只在最后一章出现。"),
]
FUTURE_ONLY_WORD = "朱雀衔环"


def _book(db):
    book = Book(
        title="合成导航测试书",
        author="作者",
        synopsis="",
        protagonists=[],
        front_matter="",
        filename="nav.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=3,
        main_chapter_count=3,
    )
    db.add(book)
    db.flush()
    parts = []
    cursor = 0
    for idx, (title, body) in enumerate(CHAPTERS):
        text = f"第{idx + 1}章 {title}\n{body}\n"
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
    db.commit()
    source_dir = UPLOADS_DIR / str(book.id)
    source_dir.mkdir(parents=True, exist_ok=True)
    source = "".join(parts)
    (source_dir / "source.txt").write_text(source, encoding="utf-8")
    return book.id, source


@pytest.fixture()
def nav_book(client):
    with SessionLocal() as db:
        book_id, source = _book(db)
    _api(
        client.put(
            f"/api/books/{book_id}/progress",
            json={"chapter_idx": 1, "char_offset": 0},
        )
    )
    return book_id, source


def _api(response):
    body = response.json()
    assert body["code"] == 200, body
    return body["data"]


def _tool(client, book_id, tool, args=None):
    result = _api(
        client.post(
            f"/api/books/{book_id}/agent/tool",
            json={"tool": tool, "args": args or {}},
        )
    )
    assert result["ok"] is True, result
    return result["data"]


def test_get_toc_clamped_to_safe_chapter(nav_book, client):
    book_id, _ = nav_book
    toc = _tool(client, book_id, "get_toc")
    assert [item["idx"] for item in toc["items"]] == [0, 1]
    assert all(
        set(item) == {
            "idx",
            "title",
            "kind",
            "chars",
            "char_start",
            "char_end",
        }
        for item in toc["items"]
    )
    assert toc["items"][0]["chars"] == (
        toc["items"][0]["char_end"] - toc["items"][0]["char_start"]
    )
    assert toc["chapter_to"] == 1

    # 传再大的 chapter_to 也不能翻出安全章以后。
    overshoot = _tool(
        client, book_id, "get_toc", {"chapter_to": 99}
    )
    assert [item["idx"] for item in overshoot["items"]] == [0, 1]

    windowed = _tool(
        client, book_id, "get_toc", {"chapter_from": 1}
    )
    assert [item["idx"] for item in windowed["items"]] == [1]

    paged = _tool(client, book_id, "get_toc", {"limit": 1})
    assert [item["idx"] for item in paged["items"]] == [0]
    assert paged["next_from"] == 1
    assert paged["truncated"] is True


def test_search_text_exact_finds_within_safe(nav_book, client):
    book_id, _ = nav_book
    result = _tool(
        client, book_id, "search_text", {"query": "庚帖", "mode": "exact"}
    )
    assert result["mode"] == "exact"
    assert result["count"] == 2
    assert [item["chapter_idx"] for item in result["items"]] == [0, 1]
    for item in result["items"]:
        assert item["chapter_idx"] <= 1
        assert "庚帖" in item["snippet"]
        assert item["char_end"] - item["char_start"] == len("庚帖")


def test_search_text_exact_caps_two_per_chapter(nav_book, client):
    book_id, _ = nav_book
    result = _tool(
        client, book_id, "search_text", {"query": "旧伞", "mode": "exact"}
    )
    assert result["count"] == 2
    assert {item["chapter_idx"] for item in result["items"]} == {0}


def test_search_text_hybrid_falls_back_when_no_index(nav_book, client):
    book_id, _ = nav_book
    result = _tool(client, book_id, "search_text", {"query": "庚帖"})
    assert result["mode"] == "exact"
    assert result["count"] == 2


def test_search_text_never_returns_future_chapter(nav_book, client):
    """护栏：只在最后一章出现的词 + 安全章在中间，任何 mode 都必须 0 条。"""
    book_id, source = nav_book
    # 前置：该词确实只在第 2 章（> safe_chapter=1）出现。
    assert source.count(FUTURE_ONLY_WORD) == 1
    assert FUTURE_ONLY_WORD in source[
        _chapter_span(client, book_id, 2)[0] :
    ]
    for mode in ("exact", "hybrid"):
        result = _tool(
            client, book_id, "search_text", {"query": FUTURE_ONLY_WORD, "mode": mode}
        )
        assert result["count"] == 0, mode
        assert result["items"] == []


def _chapter_span(client, book_id, chapter_idx):
    with SessionLocal() as db:
        chapter = (
            db.query(Chapter)
            .filter(
                Chapter.book_id == book_id, Chapter.idx == chapter_idx
            )
            .first()
        )
        return int(chapter.char_start), int(chapter.char_end)


def test_read_span_slice_matches_source(nav_book, client):
    book_id, source = nav_book
    start, end = _chapter_span(client, book_id, 0)
    span = _tool(
        client,
        book_id,
        "read_span",
        {"char_start": start + 3, "char_end": end - 2},
    )
    assert span["text"] == source[start + 3 : end - 2]
    assert span["next_start"] is None


def test_read_span_clamps_and_blocks_future(nav_book, client):
    book_id, source = nav_book
    safe_end = _chapter_span(client, book_id, 1)[1]
    future_start = _chapter_span(client, book_id, 2)[0]

    # 区间伸进未来章：end 夹到安全章的 char_end。
    span = _tool(
        client,
        book_id,
        "read_span",
        {"char_start": 0, "char_end": len(source)},
    )
    assert span["char_end"] == safe_end
    assert span["text"] == source[0:safe_end]
    assert span["next_start"] is None

    # 起点落在未来章：直接拒收。
    blocked = _api(
        client.post(
            f"/api/books/{book_id}/agent/tool",
            json={
                "tool": "read_span",
                "args": {
                    "char_start": future_start,
                    "char_end": future_start + 100,
                },
            },
        )
    )
    assert blocked["ok"] is False
    assert blocked["error"] == "spoiler_blocked"


def test_read_span_truncates_with_next_start(nav_book, client):
    book_id, source = nav_book
    span = _tool(
        client,
        book_id,
        "read_span",
        {"char_start": 0, "char_end": len(source), "max_chars": 10},
    )
    assert span["text"] == source[0:10]
    assert span["next_start"] == 10
    follow = _tool(
        client,
        book_id,
        "read_span",
        {"char_start": span["next_start"], "char_end": len(source)},
    )
    assert follow["text"].startswith(source[10:20])


def test_mcp_registers_nav_tools(monkeypatch, mcp_server):
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"get_toc", "read_chapters", "read_passage", "search_text"} <= names

    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {
            "ok": True,
            "data": {"ok": True, "data": {"items": []}},
        }

    monkeypatch.setattr(mcp_server, "_request", fake_request)
    monkeypatch.setattr(
        mcp_server,
        "_context",
        lambda book_id: {
            "ok": True,
            "data": {"available": True, "book_id": 9},
        },
    )
    mcp_server.get_toc(chapter_from=1, chapter_to=5, limit=50)
    assert calls[0][1] == "/api/books/9/agent/model-tool"
    assert calls[0][2]["json"] == {
        "tool": "get_toc",
        "args": {"chapter_from": 1, "chapter_to": 5, "limit": 50},
    }
    mcp_server.read_chapters(chapter_from=1, chapter_to=2)
    assert calls[1][2]["json"]["tool"] == "read_chapters"
    mcp_server.search_text(query="某词", mode="exact", chapter_to=3)
    assert calls[2][2]["json"]["args"] == {
        "query": "某词",
        "mode": "exact",
        "chapter_to": 3,
    }
