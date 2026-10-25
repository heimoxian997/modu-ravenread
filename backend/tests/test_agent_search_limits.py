"""四个搜索工具的 limit 与 6000 字符体积护栏测试。"""

from __future__ import annotations

import json

import pytest

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.services import agent_tool_service

CHAPTER_COUNT = 6
COMMON_WORD = "茶盏"


def _book(db):
    book = Book(
        title="合成检索测试书",
        author="作者",
        synopsis="",
        protagonists=[],
        front_matter="",
        filename="search-limits.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=CHAPTER_COUNT,
        main_chapter_count=CHAPTER_COUNT,
    )
    db.add(book)
    db.flush()
    parts = []
    cursor = 0
    for idx in range(CHAPTER_COUNT):
        body = (
            f"{COMMON_WORD}里的水凉了，甲某又换了{COMMON_WORD}，"
            f"第三只{COMMON_WORD}摆在案上。\n乙某只当没看见。"
        )
        title = f"第{idx + 1}章 试茶"
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
    db.commit()
    source_dir = UPLOADS_DIR / str(book.id)
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.txt").write_text("".join(parts), encoding="utf-8")
    return book.id


def _ctx(db, book_id):
    book = db.get(Book, book_id)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.idx == CHAPTER_COUNT - 1)
        .first()
    )
    return agent_tool_service.ToolContext(
        db=db,
        book=book,
        chapter=chapter,
        safe_chapter=CHAPTER_COUNT - 1,
        char_offset=0,
    )


def _run(db, book_id, tool, args):
    return agent_tool_service.execute(
        tool, args, _ctx(db, book_id)
    )["data"]


def item_chars(items):
    return sum(len(json.dumps(item, ensure_ascii=False)) for item in items)


@pytest.fixture()
def book_id():
    with SessionLocal() as db:
        return _book(db)


def test_exact_total_limit_and_clamp(book_id):
    # 6 章 × 每章上限 2 条 = 12 条可用；默认（不传）8 条。
    with SessionLocal() as db:
        default = _run(
            db, book_id, "search_text", {"query": COMMON_WORD, "mode": "exact"}
        )
    assert default["count"] == 8
    assert default["limit"] == 8
    assert default["truncated"] is True

    with SessionLocal() as db:
        cases = [
            ({"limit": 8}, 8, 8, True),
            ({"limit": 4}, 4, 4, True),
            ({"limit": 3}, 3, 3, True),
            ({"limit": 0}, 1, 1, True),  # clamp 到下界，仍有更多命中
            ({"limit": 999}, 8, 8, True),  # clamp 到上界
        ]
        for extra, count, effective, truncated in cases:
            result = _run(
                db,
                book_id,
                "search_text",
                {"query": COMMON_WORD, "mode": "exact", **extra},
            )
            assert result["count"] == count, extra
            assert result["limit"] == effective, extra
            assert result["truncated"] is truncated, extra
            assert all(
                i["chapter_idx"] <= CHAPTER_COUNT - 1 for i in result["items"]
            )


def test_hybrid_default_two_and_top_k(book_id, monkeypatch):
    calls = []

    def fake(db, book_id_, query, top_k=5, recall=20, chapter_idx=None):
        calls.append((top_k, recall))

        def hit(chunk_id, parent_id):
            return {
                "chunk_id": chunk_id,
                "parent_id": parent_id,
                "chapter_idx": 1,
                "chapter_title": "第2章",
                "text": f"子块{chunk_id}",
                "parent_text": f"父块{parent_id}的正文" * 60,
                "char_start": 10,
                "char_end": 20,
                "parent_char_start": 0,
                "parent_char_end": 400,
                "rrf_score": 0.5,
            }

        return [hit(i, i) for i in range(1, 11)]

    monkeypatch.setattr(
        "app.services.text_index_service.search_text_index", fake
    )
    with SessionLocal() as db:
        default = _run(db, book_id, "search_text", {"query": "某词"})
        assert default["count"] == 2  # 与改动前的默认条数一致
        assert default["limit"] == 2
        assert default["truncated"] is True
        assert calls[-1] == (20, 20)  # 默认 limit 2 → top_k 20

        more = _run(
            db, book_id, "search_text", {"query": "某词", "limit": 8}
        )
        assert more["count"] == 8
        assert more["limit"] == 8
        assert calls[-1] == (80, 80)  # limit 8 → top_k 80


def _summary_hits(count):
    return [
        {
            "chapter_idx": i,
            "chapter_title": f"第{i + 1}章",
            "summary": f"第{i}章摘要内容",
            "key_events": [],
            "characters": [],
            "cliffhanger": "",
            "rrf_score": 0.5,
        }
        for i in range(count)
    ]


def test_summaries_limit_and_clamp(book_id, monkeypatch):
    top_k_calls = []

    def fake(db, book_id_, query, top_k=5, recall=20, chapter_idx=None):
        top_k_calls.append(top_k)
        return _summary_hits(12)

    monkeypatch.setattr(
        "app.services.summary_index_service.search_summary_index", fake
    )
    with SessionLocal() as db:
        default = _run(db, book_id, "search_summaries", {"query": "某词"})
        assert default["count"] == 3  # 与改动前一致
        assert default["limit"] == 3
        assert default["truncated"] is True
        assert top_k_calls[-1] == 3  # top_k 直取 limit，不过采样

        eight = _run(
            db, book_id, "search_summaries", {"query": "某词", "limit": 8}
        )
        assert eight["count"] == 8
        assert eight["limit"] == 8
        assert eight["truncated"] is True
        assert top_k_calls[-1] == 8

        zero = _run(
            db, book_id, "search_summaries", {"query": "某词", "limit": 0}
        )
        assert zero["count"] == 1
        assert zero["limit"] == 1

        huge = _run(
            db, book_id, "search_summaries", {"query": "某词", "limit": 999}
        )
        assert huge["count"] == 10  # clamp 到 10
        assert huge["limit"] == 10
        assert huge["truncated"] is True


def test_lore_limit(book_id, monkeypatch):
    top_k_calls = []

    def fake(db, book_id_, query, top_k=5, recall=20, chapter_idx=None):
        top_k_calls.append(top_k)
        return [
            {
                "lore_id": i,
                "canonical_name": f"条目{i}",
                "category": "item",
                "chapter_idx": i,
                "summary": f"条目{i}的说明",
                "aliases": [],
                "rrf_score": 0.5,
            }
            for i in range(12)
        ]

    monkeypatch.setattr(
        "app.services.entity_index_service.search_lore_index", fake
    )
    with SessionLocal() as db:
        default = _run(db, book_id, "search_lore", {"query": "某物"})
        assert default["count"] == 3
        assert default["limit"] == 3
        assert top_k_calls[-1] == 3

        eight = _run(db, book_id, "search_lore", {"query": "某物", "limit": 8})
        assert eight["count"] == 8
        assert eight["limit"] == 8
        assert eight["truncated"] is True
        assert top_k_calls[-1] == 8


def test_character_changes_limit(book_id, monkeypatch):
    top_k_calls = []

    def fake(
        db,
        book_id_,
        query,
        chapter_idx,
        top_k=5,
        recall=20,
        unique_characters=True,
    ):
        top_k_calls.append(top_k)
        return [
            {
                "character_id": 1,
                "character": "甲某",
                "chapter_idx": i,
                "delta_id": i,
                "identity": "",
                "traits_add": [],
                "relations_add": [f"关系{i}"],
                "known_facts_add": [],
                "voice_sample": "",
                "arc": "",
                "rrf_score": 0.5,
            }
            for i in range(12)
        ]

    monkeypatch.setattr(
        "app.services.entity_index_service.search_character_changes", fake
    )
    with SessionLocal() as db:
        default = _run(
            db, book_id, "search_character_changes", {"query": "变化"}
        )
        assert default["count"] == 3
        assert default["limit"] == 3
        assert top_k_calls[-1] == 3

        eight = _run(
            db,
            book_id,
            "search_character_changes",
            {"query": "变化", "limit": 8},
        )
        assert eight["count"] == 8
        assert eight["limit"] == 8
        assert eight["truncated"] is True
        assert top_k_calls[-1] == 8
        assert "character_id" not in eight["items"][0]


def test_volume_cap_6000(book_id, monkeypatch):
    """体积护栏：条数放开也吃不掉上下文，单次返回 ≤6000 字。"""
    monkeypatch.setattr(
        "app.services.text_index_service.search_text_index",
        lambda db, book_id_, query, top_k=5, recall=20, chapter_idx=None: [
            {
                "chunk_id": i,
                "parent_id": i,
                "chapter_idx": 1,
                "chapter_title": "第2章",
                "text": "子块",
                "parent_text": "块" * 1500,
                "char_start": 10,
                "char_end": 20,
                "parent_char_start": 0,
                "parent_char_end": 1500,
                "rrf_score": 0.5,
            }
            for i in range(1, 9)
        ],
    )

    def big_summaries(db, book_id_, query, top_k=5, recall=20, chapter_idx=None):
        return _summary_hits_with("摘" * 800, 12)

    monkeypatch.setattr(
        "app.services.summary_index_service.search_summary_index",
        big_summaries,
    )
    with SessionLocal() as db:
        text = _run(
            db, book_id, "search_text", {"query": "某词", "limit": 8}
        )
        assert text["count"] < 8
        assert text["truncated"] is True
        assert item_chars(text["items"]) <= 6000

        summaries = _run(
            db, book_id, "search_summaries", {"query": "某词", "limit": 10}
        )
        assert summaries["count"] < 10
        assert summaries["truncated"] is True
        assert item_chars(summaries["items"]) <= 6000


def _summary_hits_with(summary_text, count):
    return [
        {
            "chapter_idx": i,
            "chapter_title": f"第{i + 1}章",
            "summary": summary_text,
            "key_events": [],
            "characters": [],
            "cliffhanger": "",
            "rrf_score": 0.5,
        }
        for i in range(count)
    ]


def test_mcp_limit_passthrough(monkeypatch, mcp_server):
    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "data": {"ok": True, "data": {"items": []}}}

    monkeypatch.setattr(mcp_server, "_request", fake_request)
    monkeypatch.setattr(
        mcp_server,
        "_context",
        lambda book_id: {
            "ok": True,
            "data": {"available": True, "book_id": 3},
        },
    )
    mcp_server.search_summaries(query="某词", limit=8)
    assert calls[0][2]["json"] == {
        "tool": "search_summaries",
        "args": {"query": "某词", "limit": 8},
    }
    mcp_server.search_summaries(query="某词")
    assert calls[1][2]["json"]["args"]["limit"] == 3

    mcp_server.search_text(query="某词", limit=5)
    assert calls[2][2]["json"]["args"]["limit"] == 5
    mcp_server.search_lore(query="某物", limit=10)
    assert calls[3][2]["json"]["args"] == {"query": "某物", "limit": 10}
    mcp_server.search_character_changes(query="变化")
    assert calls[4][2]["json"]["args"] == {"query": "变化", "limit": 3}
