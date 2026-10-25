"""notes 两张表、note_upsert / note_query 与 MCP 注册的测试。"""

from __future__ import annotations

import asyncio

import pytest

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.note import Note
from app.services.agent_capability_service import AgentCapabilities, detect

CHAPTERS = [
    ("院门", "甲某收起纸伞走进院门。他把（半旧）的信交给乙某，只说“不成……”。\n乙某捏着信站在廊下，没有立刻拆。"),
    ("拆信", "乙某当夜拆了信，看完便烧。丙某来问，她只答“再等些时日”。\n甲某在门外听得一清二楚。"),
    ("送别", "第二日天未亮，甲某便动身回乡。\n乙某送到渡口，替他理了理衣领，终究什么也没说。"),
]


def _book(db):
    book = Book(
        title="合成笔记测试书",
        author="作者",
        synopsis="",
        protagonists=[],
        front_matter="",
        filename="notes.txt",
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
    (source_dir / "source.txt").write_text("".join(parts), encoding="utf-8")
    return book.id


def _api(response):
    body = response.json()
    assert body["code"] == 200, body
    return body["data"]


@pytest.fixture()
def book_with_progress(client):
    with SessionLocal() as db:
        book_id = _book(db)
    _api(
        client.put(
            f"/api/books/{book_id}/progress",
            json={"chapter_idx": 2, "char_offset": 0},
        )
    )
    return book_id


def _upsert(client, book_id, **kwargs):
    return _api(
        client.post(f"/api/books/{book_id}/notes/upsert", json=kwargs)
    )


def _query(client, book_id, **kwargs):
    return _api(
        client.post(f"/api/books/{book_id}/notes/query", json=kwargs)
    )


def test_query_empty_lists_all_gaps(book_with_progress, client):
    result = _query(client, book_with_progress)
    assert result["ok"] is True
    data = result["data"]
    assert data["items"] == []
    assert data["safe_chapter"] == 2
    assert data["coverage"]["gaps"] == [0, 1, 2]
    assert data["coverage"]["note_count"] == 0


def test_upsert_and_query_roundtrip(book_with_progress, client):
    first = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="乙某捏着信站在廊下",
        payload={"predicate": "状态", "value": "收到信但未拆"},
        confidence=0.8,
    )
    assert first["ok"] is True
    item = first["data"]
    assert item["chapter_idx"] == 0
    assert item["spoiler"] == 0
    assert item["status"] == "tentative"
    assert item["quote"] == "乙某捏着信站在廊下"
    assert 0 <= item["char_start"] < item["char_end"]

    second = _upsert(
        client,
        book_with_progress,
        type="event",
        subject_key="ent_2|ent_3",
        quote="乙某当夜拆了信，看完便烧",
        payload={
            "action": "拆信后焚毁",
            "participants": ["ent_2", "ent_3"],
        },
    )
    assert second["data"]["chapter_idx"] == 1

    result = _query(client, book_with_progress)
    data = result["data"]
    assert [i["chapter_idx"] for i in data["items"]] == [0, 1]
    assert data["coverage"]["scope"] == {"chapter_from": 0, "chapter_to": 2}
    assert data["coverage"]["chapters_with_notes"] == [0, 1]
    assert data["coverage"]["matched_chapters"] == [0, 1]
    assert data["coverage"]["gaps"] == [2]
    assert data["coverage"]["note_count"] == 2


def test_upsert_locates_via_normalization(book_with_progress, client):
    # 原文是全角括号，引文用半角。
    result = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="他把(半旧)的信交给乙某",
        payload={"predicate": "持有物", "value": "一封半旧的信"},
    )
    assert result["ok"] is True
    assert result["data"]["chapter_idx"] == 0

    # 原文是“……”，引文用单个省略号。
    ellipsis = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="只说“不成…”",
        payload={"predicate": "状态", "value": "回绝了托付"},
    )
    assert ellipsis["ok"] is True
    assert ellipsis["data"]["chapter_idx"] == 0


def test_upsert_rejects_quote_not_found(book_with_progress, client):
    result = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="这句原文里根本没有",
        payload={"predicate": "状态", "value": "无"},
    )
    assert result["ok"] is False
    assert result["error"] == "quote_not_found"


def test_upsert_rejects_cross_chapter_quote(book_with_progress, client):
    result = _upsert(
        client,
        book_with_progress,
        type="event",
        subject_key="ent_1|ent_2",
        quote="没有立刻拆。\n第2章 拆信",
        payload={
            "action": "跨章引文",
            "participants": ["ent_1", "ent_2"],
        },
    )
    assert result["ok"] is False
    assert result["error"] == "quote_not_found"


def test_upsert_rejects_title_line_quote(book_with_progress, client):
    result = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="第2章 拆信",
        payload={"predicate": "状态", "value": "标题行"},
    )
    assert result["ok"] is False
    assert result["error"] == "quote_not_found"


def test_future_note_is_stored_but_never_returned(client):
    with SessionLocal() as db:
        book_id = _book(db)
    _api(
        client.put(
            f"/api/books/{book_id}/progress",
            json={"chapter_idx": 1, "char_offset": 0},
        )
    )
    result = _upsert(
        client,
        book_id,
        type="event",
        subject_key="ent_1|ent_2",
        quote="乙某送到渡口，替他理了理衣领",
        payload={
            "action": "渡口相送",
            "participants": ["ent_1", "ent_2"],
        },
    )
    assert result["ok"] is True
    assert result["data"]["spoiler"] == 1
    assert result["data"]["beyond_safe_chapter"] is True
    assert result["data"]["chapter_idx"] == 2

    query = _query(client, book_id)
    assert query["data"]["items"] == []
    assert query["data"]["coverage"]["note_count"] == 0

    # 读者追上进度后，剧透笔记依然不返回：spoiler 是写入时定死的。
    _api(
        client.put(
            f"/api/books/{book_id}/progress",
            json={"chapter_idx": 2, "char_offset": 0},
        )
    )
    after = _query(client, book_id)
    assert after["data"]["items"] == []


def test_same_fingerprint_updates_in_place(book_with_progress, client):
    args = dict(
        type="fact",
        subject_key="ent_1",
        quote="乙某捏着信站在廊下",
    )
    _upsert(
        client,
        book_with_progress,
        payload={"predicate": "状态", "value": "未拆信"},
        confidence=0.5,
        **args,
    )
    second = _upsert(
        client,
        book_with_progress,
        payload={"predicate": "状态", "value": "犹豫未拆"},
        confidence=0.9,
        **args,
    )
    assert second["data"]["payload"]["value"] == "犹豫未拆"
    assert second["data"]["confidence"] == 0.9
    result = _query(client, book_with_progress)
    assert result["data"]["total"] == 1


def test_subject_key_normalized(book_with_progress, client):
    relation_args = dict(
        type="relation",
        quote="乙某当夜拆了信，看完便烧",
        payload={
            "a": "乙某",
            "b": "甲某",
            "label": "主仆",
            "state_at_chapter": "仍有托付之情",
        },
    )
    first = _upsert(
        client, book_with_progress, subject_key="甲某|乙某", **relation_args
    )
    # 乙(U+4E59) < 甲(U+7532)，按 unicode 排序后乙在前。
    assert first["data"]["subject_key"] == "乙某|甲某"
    assert first["data"]["note_id"].startswith("n_relation_")
    assert len(first["data"]["note_id"]) == len("n_relation_") + 12

    second = _upsert(
        client, book_with_progress, subject_key="乙 某 | 甲某", **relation_args
    )
    assert second["data"]["note_id"] == first["data"]["note_id"]
    result = _query(client, book_with_progress)
    assert result["data"]["total"] == 1

    # 称呼线带 >，方向必须保留。
    address = _upsert(
        client,
        book_with_progress,
        type="address",
        subject_key="甲某>乙某",
        quote="她只答“再等些时日”",
        payload={"speaker": "甲某", "target": "乙某", "address": "姑娘"},
    )
    assert address["data"]["subject_key"] == "甲某>乙某"


def test_blank_book_capabilities(client):
    blank = AgentCapabilities()
    assert blank.enabled is False
    assert blank.available_tools() == set()

    with_chapters = AgentCapabilities(chapters=True)
    assert with_chapters.enabled is True
    assert {"read_around", "read_chapter"} <= with_chapters.available_tools()

    # 只有章节、没有任何切块/摘要/人物的书，也必须能读章。
    with SessionLocal() as db:
        book_id = _book(db)
        caps = detect(db, book_id)
    assert caps.chapters is True
    assert caps.chunks is False
    assert caps.enabled is True
    assert "read_chapter" in caps.available_tools()


def test_query_entity_and_pair_filters(book_with_progress, client):
    _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="乙某捏着信站在廊下",
        payload={"predicate": "状态", "value": "未拆"},
    )
    _upsert(
        client,
        book_with_progress,
        type="relation",
        subject_key="ent_1|ent_2",
        quote="乙某当夜拆了信，看完便烧",
        payload={
            "a": "ent_1",
            "b": "ent_2",
            "label": "主仆",
            "state_at_chapter": "仍有托付之情",
        },
    )
    by_entity = _query(client, book_with_progress, entity="ent_1")
    assert by_entity["data"]["total"] == 2
    only_fact = _query(client, book_with_progress, entity="ent_2")
    assert only_fact["data"]["total"] == 1
    pair = _query(client, book_with_progress, pair=["ent_2", "ent_1"])
    assert pair["data"]["total"] == 1
    assert pair["data"]["items"][0]["type"] == "relation"
    miss = _query(client, book_with_progress, pair=["ent_1", "ent_9"])
    assert miss["data"]["total"] == 0

    # 覆盖度不受过滤影响：pair 查询与无过滤查询的 gaps 必须一致。
    unfiltered = _query(client, book_with_progress)["data"]
    coverage = pair["data"]["coverage"]
    assert coverage["gaps"] == unfiltered["coverage"]["gaps"]
    assert coverage["chapters_with_notes"] == [0, 1]
    assert coverage["matched_chapters"] == [1]
    assert coverage["note_count"] == 2
    assert pair["data"]["total"] == 1


def test_query_excludes_dead_status_by_default(book_with_progress, client):
    created = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="乙某捏着信站在廊下",
        payload={"predicate": "状态", "value": "未拆"},
    )
    note_id = created["data"]["note_id"]
    with SessionLocal() as db:
        row = db.query(Note).filter(Note.note_id == note_id).first()
        row.status = "rejected"
        db.commit()

    default = _query(client, book_with_progress)
    assert default["data"]["total"] == 0
    explicit = _query(client, book_with_progress, status="rejected")
    assert explicit["data"]["total"] == 1


def test_requires_reading_progress(client):
    with SessionLocal() as db:
        book_id = _book(db)
    upsert = _upsert(
        client,
        book_id,
        type="fact",
        subject_key="ent_1",
        quote="乙某捏着信站在廊下",
        payload={"predicate": "状态", "value": "未拆"},
    )
    assert upsert["ok"] is False
    assert upsert["error"] == "no_reading_context"
    query = _query(client, book_id)
    assert query["ok"] is False
    assert query["error"] == "no_reading_context"


def test_invalid_arguments_rejected(book_with_progress, client):
    bad_type = _upsert(
        client,
        book_with_progress,
        type="diary",
        subject_key="ent_1",
        quote="乙某捏着信站在廊下",
        payload={"predicate": "状态", "value": "无"},
    )
    assert bad_type["error"] == "invalid_arguments"

    missing_field = _upsert(
        client,
        book_with_progress,
        type="relation",
        subject_key="ent_1|ent_2",
        quote="乙某当夜拆了信，看完便烧",
        payload={"a": "ent_1", "b": "ent_2", "label": "主仆"},
    )
    assert missing_field["error"] == "invalid_arguments"
    assert (
        missing_field["data"]["parameter"] == "payload.state_at_chapter"
    )

    bad_label = _upsert(
        client,
        book_with_progress,
        type="relation",
        subject_key="ent_1|ent_2",
        quote="乙某当夜拆了信，看完便烧",
        payload={
            "a": "ent_1",
            "b": "ent_2",
            "label": "破镜重圆",
            "state_at_chapter": "章内状态",
        },
    )
    assert bad_label["error"] == "invalid_arguments"

    bad_predicate = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="乙某捏着信站在廊下",
        payload={"predicate": "口癖", "value": "无"},
    )
    assert bad_predicate["error"] == "invalid_arguments"

    short_quote = _upsert(
        client,
        book_with_progress,
        type="fact",
        subject_key="ent_1",
        quote="太短",
        payload={"predicate": "状态", "value": "无"},
    )
    assert short_quote["error"] == "invalid_arguments"


def test_mcp_tools_registered_and_forward(monkeypatch, mcp_server):
    tools = asyncio.run(mcp_server.mcp.list_tools())
    names = {tool.name for tool in tools}
    assert {"note_upsert", "note_query"} <= names

    calls = []

    def fake_request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return {"ok": True, "data": {"ok": True, "data": {"stored": True}}}

    monkeypatch.setattr(mcp_server, "_request", fake_request)
    from app.schemas.reading_note import EntryWrite, NoteQuery
    monkeypatch.setattr(mcp_server, "_context", lambda book_id: {"ok":True,"data":{"book_id":book_id}})
    note = EntryWrite(kind="entry",title="合成记录",claim="甲某留下纸伞",basis="explicit",
        payload={"type":"event","action":"留下纸伞","participants":["甲某"]},
        evidence=[{"chapter_number":1,"quote":"甲某留下纸伞"}])
    result = mcp_server.note_upsert(book_id=6, note=note)
    assert result["ok"] is True
    assert calls[0][1] == "/api/books/6/agent/model-tool"
    assert calls[0][2]["json"]["args"]["kind"] == "entry"
    mcp_server.note_query(book_id=6, filters=NoteQuery(entities=["甲某"],limit=5))
    assert calls[1][2]["json"]["args"]["entities"] == ["甲某"]
    assert calls[1][2]["json"]["args"]["limit"] == 5
