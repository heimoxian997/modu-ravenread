"""Step 3：章级闸门、11 工具面、章号校验与投影测试。"""

from __future__ import annotations

import json

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.chapter_summary import ChapterSummary
from app.models.character import Character
from app.models.character_alias_observation import CharacterAliasObservation
from app.services import (
    agent_prompts,
    agent_tool_service,
    alias_check_service,
    entity_layer_service,
)


def _payload(mentions, deltas, new_aliases=None):
    return {
        "characters": {
            "mentions": mentions,
            "card_deltas": deltas,
            "new_aliases": new_aliases or [],
        },
        "lore": [],
    }


def _book(db, bodies=None):
    bodies = bodies or (
        "第一段甲某走进院子。\n第二段乙某在檐下缝衣。",
        "第三段甲某发现乙某是叛徒。",
        "第四段丙某在门口站着。",
    )
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
        filename="agent-step3.txt",
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
    payloads = [
        _payload(
            mentions=[
                {"name": "甲某", "aliases": ["甲某"], "role": "主角"},
                {"name": "乙某", "aliases": ["小乙"], "role": ""},
            ],
            deltas=[
                {
                    "character": "甲某",
                    "identity": "身份0",
                    "traits": ["trait0"],
                    "relations": ["rel0"],
                    "known_facts": ["fact0"],
                    "voice_sample": "voice0",
                    "arc": "arc0",
                    "reason": "",
                },
                {
                    "character": "乙某",
                    "identity": "身份乙",
                    "traits": [],
                    "relations": [],
                    "known_facts": [],
                    "voice_sample": "",
                    "arc": "",
                    "reason": "",
                },
            ],
            new_aliases=[
                {"alias": "甲某", "canonical": "甲某"},
                {"alias": "小乙", "canonical": "乙某"},
            ],
        ),
        _payload(
            mentions=[
                {"name": "甲某", "aliases": ["甲某"], "role": ""},
                {"name": "乙某", "aliases": ["小乙"], "role": ""},
            ],
            deltas=[
                {
                    "character": "甲某",
                    "identity": "",
                    "traits": [],
                    "relations": [],
                    "known_facts": ["发现乙某是叛徒"],
                    "voice_sample": "",
                    "arc": "",
                    "reason": "",
                },
                {
                    "character": "乙某",
                    "identity": "",
                    "traits": [],
                    "relations": ["与甲某对立"],
                    "known_facts": [],
                    "voice_sample": "",
                    "arc": "",
                    "reason": "",
                },
            ],
        ),
        _payload(
            mentions=[
                {"name": "甲某", "aliases": ["老甲"], "role": ""},
                {"name": "丙某", "aliases": ["丙某"], "role": ""},
            ],
            deltas=[
                {
                    "character": "甲某",
                    "identity": "",
                    "traits": [],
                    "relations": [],
                    "known_facts": ["fact2"],
                    "voice_sample": "",
                    "arc": "",
                    "reason": "",
                }
            ],
            new_aliases=[
                {"alias": "老甲", "canonical": "甲某"},
                {"alias": "丙某", "canonical": "丙某"},
            ],
        ),
    ]
    for idx, parsed in enumerate(payloads[: len(entries)]):
        db.add(
            ChapterExtraction(
                book_id=book.id,
                chapter_idx=idx,
                title=titles[idx],
                status="success",
                parsed=parsed,
            )
        )
        db.add(
            ChapterSummary(
                book_id=book.id,
                chapter_idx=idx,
                title=titles[idx],
                summary=f"第{idx + 1}章摘要",
                key_events=[f"事件{idx}"],
                characters=["甲某"],
                cliffhanger="",
            )
        )
    db.commit()
    source_dir = UPLOADS_DIR / str(book.id)
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.txt").write_text(source, encoding="utf-8")
    entity_layer_service.build_entity_layer(db, book.id)
    return book.id


def _ctx(db, book_id, chapter_idx=1, safe=None):
    book = db.get(Book, book_id)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.idx == chapter_idx)
        .one()
    )
    return agent_tool_service.ToolContext(
        db=db,
        book=book,
        chapter=chapter,
        safe_chapter=chapter_idx if safe is None else safe,
        char_offset=100,
    )


def _character_id(db, book_id, name):
    return (
        db.query(Character)
        .filter(
            Character.book_id == book_id,
            Character.canonical_name == name,
        )
        .one()
        .id
    )


def test_alias_rules_keep_english_but_drop_digits():
    assert alias_check_service._bad_format("Alice") is None
    assert alias_check_service._bad_format("Mr. Smith") is None
    assert alias_check_service._bad_format("Elizabeth") is None
    assert "数字" in alias_check_service._bad_format("Alice2")
    assert "单字" in alias_check_service._bad_format("某")
    assert "标点" in alias_check_service._bad_format("某，人")


def test_search_text_returns_two_full_parents(monkeypatch):
    calls = []
    parent = "父块" * 800

    def fake(db, book_id, query, top_k=5, recall=20, chapter_idx=None):
        calls.append((top_k, chapter_idx))

        def hit(chunk_id, parent_id, child):
            return {
                "chunk_id": chunk_id,
                "parent_id": parent_id,
                "chapter_idx": 1,
                "chapter_title": "第2章",
                "text": child,
                "parent_text": parent,
                "char_start": 100,
                "char_end": 150,
                "parent_char_start": 100,
                "parent_char_end": 1700,
                "rrf_score": 0.5,
            }

        return [
            hit(1, 10, "子块一"),
            hit(2, 10, "子块一重复"),
            hit(3, 11, "子块二"),
            hit(4, 12, "子块三"),
        ]

    monkeypatch.setattr(
        "app.services.text_index_service.search_text_index", fake
    )
    with SessionLocal() as db:
        book_id = _book(db)
        data = agent_tool_service.execute(
            "search_text", {"query": "某词"}, _ctx(db, book_id)
        )
    assert data["ok"] is True
    items = data["data"]["items"]
    assert len(items) == 2
    assert {item["parent_id"] for item in items} == {10, 11}
    assert all(item["text"] == parent for item in items)
    assert items[0]["hit_text"] == "子块一"
    assert calls[0] == (20, 1)


def test_chapter_summary_and_read_chapter():
    long_body = "长" * 2600
    with SessionLocal() as db:
        book_id = _book(db, bodies=("第一章正文。", long_body, "第三章。"))
        ctx = _ctx(db, book_id, chapter_idx=2, safe=2)
        db.query(ChapterSummary).filter(
            ChapterSummary.book_id == book_id,
            ChapterSummary.chapter_idx == 2,
        ).delete()
        db.commit()
        found = agent_tool_service.execute(
            "get_chapter_summary", {"chapter_idx": 0}, ctx
        )
        missing = agent_tool_service.execute(
            "get_chapter_summary", {"chapter_idx": 2}, ctx
        )
        first = agent_tool_service.execute(
            "read_chapter", {"chapter_idx": 1, "offset": 0}, ctx
        )
        assert found["data"]["found"] is True
        assert missing["data"]["found"] is False
        assert first["data"]["text"]
        assert len(first["data"]["text"]) <= 1200
        assert first["data"]["has_more"] is True
        second = agent_tool_service.execute(
            "read_chapter",
            {"chapter_idx": 1, "offset": first["data"]["next_offset"]},
            ctx,
        )
        assert second["data"]["text"]
        offset = second["data"]["next_offset"]
        guard = 0
        while offset is not None and guard < 10:
            guard += 1
            more = agent_tool_service.execute(
                "read_chapter", {"chapter_idx": 1, "offset": offset}, ctx
            )
            assert more["ok"] is True
            assert len(more["data"]["text"]) <= 1200
            offset = more["data"]["next_offset"]
        assert more["data"]["has_more"] is False


def test_alias_canonical_alias_negative_and_gate():
    with SessionLocal() as db:
        book_id = _book(db)
        second = _character_id(db, book_id, "乙某")
        third = _character_id(db, book_id, "丙某")
        for character_id, chapter in ((second, 0), (third, 2)):
            db.add(
                CharacterAliasObservation(
                    book_id=book_id,
                    character_id=character_id,
                    alias="旧称",
                    chapter_idx=chapter,
                    count=1,
                    source="mention",
                )
            )
        db.commit()
        ctx = _ctx(db, book_id, chapter_idx=2, safe=2)
        canonical = agent_tool_service.execute(
            "resolve_alias", {"alias": "甲某"}, ctx
        )
        assert canonical["data"]["matched_by"] == "canonical_name"
        assert set(canonical["data"]["candidates"][0]) == set(
            agent_tool_service.ALIAS_FIELDS
        )
        payload = json.dumps(canonical["data"], ensure_ascii=False)
        assert "character_id" not in payload
        assert "score" not in payload

        missing = agent_tool_service.execute(
            "resolve_alias", {"alias": "不存在"}, ctx
        )
        assert missing["data"]["found"] is False
        assert missing["data"]["checked"]
        assert "suggestion" not in missing["data"]

        early = agent_tool_service.execute(
            "resolve_alias", {"alias": "旧称", "as_of_chapter": 1}, ctx
        )
        late = agent_tool_service.execute(
            "resolve_alias", {"alias": "旧称", "as_of_chapter": 2}, ctx
        )
        early_list = agent_tool_service.execute(
            "list_alias_candidates",
            {"alias": "旧称", "as_of_chapter": 1},
            ctx,
        )
        late_list = agent_tool_service.execute(
            "list_alias_candidates",
            {"alias": "旧称", "as_of_chapter": 2},
            ctx,
        )
    assert early["data"]["gate_chapter"] == 1
    assert early["data"]["total"] == 1
    assert late["data"]["gate_chapter"] == 2
    assert late["data"]["total"] == 2
    assert early_list["data"]["total"] == 1
    assert late_list["data"]["total"] == 2


def test_as_of_defaults_to_current_chapter():
    with SessionLocal() as db:
        book_id = _book(db)
        ctx = _ctx(db, book_id, chapter_idx=2, safe=2)
        card = agent_tool_service.execute(
            "get_character", {"name": "甲某"}, ctx
        )
        alias = agent_tool_service.execute(
            "resolve_alias", {"alias": "甲某"}, ctx
        )
        listing = agent_tool_service.execute(
            "list_alias_candidates", {"alias": "甲某"}, ctx
        )
    assert card["data"]["as_of_chapter"] == 2
    assert alias["data"]["gate_chapter"] == 2
    assert listing["data"]["gate_chapter"] == 2


def test_get_character_single_card_with_chapters():
    with SessionLocal() as db:
        book_id = _book(db)
        ctx = _ctx(db, book_id, chapter_idx=2, safe=2)
        early = agent_tool_service.execute(
            "get_character", {"name": "甲某", "as_of_chapter": 1}, ctx
        )
        assert early["ok"] is True
        data = early["data"]
        assert data["as_of_chapter"] == 1
        assert "provenance" not in data
        assert set(data["latest_snapshot"]) == {
            "edited",
            "override_from_chapter",
        }
        assert "id" not in data["character"]
        assert "canonical_name" not in data["character"]
        for item in data["traits"] + data["relations"] + data["known_facts"]:
            assert "first_seen_chapter" in item
            assert "last_seen_chapter" in item
        assert data["identity"]["chapter_idx"] == 0
        assert all(item["text"] != "fact2" for item in data["known_facts"])

        late = agent_tool_service.execute(
            "get_character", {"name": "甲某", "as_of_chapter": 2}, ctx
        )
        assert any(
            item["text"] == "fact2" for item in late["data"]["known_facts"]
        )

        future = agent_tool_service.execute(
            "get_character", {"name": "丙某", "as_of_chapter": 1}, ctx
        )
        assert future["ok"] is False


def test_out_of_range_all_six_entries():
    with SessionLocal() as db:
        book_id = _book(db)
        ctx = _ctx(db, book_id, chapter_idx=2, safe=2)
        cases = [
            ("read_chapter", {"chapter_idx": 3}),
            ("get_chapter_summary", {"chapter_idx": 3}),
            ("get_character", {"name": "甲某", "as_of_chapter": 3}),
            (
                "get_character_timeline",
                {"name": "甲某", "to_chapter": 3},
            ),
            ("resolve_alias", {"alias": "甲某", "as_of_chapter": 3}),
            (
                "list_alias_candidates",
                {"alias": "甲某", "as_of_chapter": 3},
            ),
            ("read_chapter", {"chapter_idx": -1}),
        ]
        for tool, args in cases:
            result = agent_tool_service.execute(tool, args, ctx)
            assert result["ok"] is False, (tool, result)
            assert result["error"] == "chapter_out_of_range", (tool, result)
            assert result["data"]["allowed_max"] == 2


def test_unprocessed_chapter_is_clamped(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        ctx = _ctx(db, book_id, chapter_idx=2, safe=2)
        monkeypatch.setattr(
            "app.services.entity_layer_service._max_processed_chapter",
            lambda db, book_id: 1,
        )
        card = agent_tool_service.execute(
            "get_character",
            {"name": "甲某", "as_of_chapter": 2},
            ctx,
        )
        assert card["data"]["requested_chapter"] == 2
        assert card["data"]["as_of_chapter"] == 1
        assert card["data"]["capped"] is True
        alias = agent_tool_service.execute(
            "resolve_alias",
            {"alias": "甲某", "as_of_chapter": 2},
            ctx,
        )
        assert alias["data"]["gate_chapter"] == 1
        assert alias["data"]["capped"] is True


def test_list_alias_candidates_and_ambiguity():
    with SessionLocal() as db:
        book_id = _book(db)
        for name in ("甲某", "乙某", "丙某"):
            character_id = _character_id(db, book_id, name)
            db.add(
                CharacterAliasObservation(
                    book_id=book_id,
                    character_id=character_id,
                    alias="同伴",
                    chapter_idx=0,
                    count=1,
                    source="mention",
                )
            )
        db.commit()
        ctx = _ctx(db, book_id)
        first = agent_tool_service.execute(
            "list_alias_candidates",
            {"alias": "同伴", "limit": 2, "offset": 0},
            ctx,
        )
        second = agent_tool_service.execute(
            "list_alias_candidates",
            {"alias": "同伴", "limit": 2, "offset": 2},
            ctx,
        )
        ambiguous = agent_tool_service.execute(
            "get_character", {"name": "同伴"}, ctx
        )
    assert first["data"]["total"] == 3
    assert first["data"]["truncated"] is True
    assert len(first["data"]["items"]) == 2
    assert len(second["data"]["items"]) == 1
    assert second["data"]["truncated"] is False
    assert all(
        "character_id" not in item for item in first["data"]["items"]
    )
    assert ambiguous["ok"] is False
    assert ambiguous["error"] == "ambiguous"
    assert all(
        "character_id" not in item
        for item in ambiguous["data"]["candidates"]
    )


def test_timeline_chapter_range_and_next_from():
    with SessionLocal() as db:
        book_id = _book(db)
        ctx = _ctx(db, book_id, chapter_idx=2, safe=2)
        page = agent_tool_service.execute(
            "get_character_timeline",
            {"name": "甲某", "from_chapter": 0, "to_chapter": 2, "limit": 1},
            ctx,
        )
        assert page["ok"] is True
        assert page["data"]["total"] == 3
        assert page["data"]["truncated"] is True
        assert page["data"]["next_from_chapter"] == 1
        assert "id" not in page["data"]["character"]
        assert "canonical_name" not in page["data"]["character"]
        filtered = agent_tool_service.execute(
            "get_character_timeline",
            {"name": "甲某", "from_chapter": 1, "to_chapter": 1},
            ctx,
        )
        assert filtered["data"]["total"] == 1
        assert filtered["data"]["items"][0]["chapter_idx"] == 1
        out = agent_tool_service.execute(
            "get_character_timeline",
            {"name": "甲某", "to_chapter": 3},
            ctx,
        )
    assert out["error"] == "chapter_out_of_range"


def test_character_change_search_has_no_id(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        second = _character_id(db, book_id, "乙某")

    def fake(
        db,
        book_id,
        query,
        chapter_idx,
        top_k=5,
        recall=20,
        unique_characters=True,
    ):
        return [
            {
                "character_id": second,
                "character": "乙某",
                "chapter_idx": 1,
                "delta_id": 1,
                "identity": "",
                "traits_add": [],
                "relations_add": ["与甲某对立"],
                "known_facts_add": [],
                "voice_sample": "",
                "arc": "",
                "rrf_score": 0.5,
            }
        ]

    monkeypatch.setattr(
        "app.services.entity_index_service.search_character_changes", fake
    )
    with SessionLocal() as db:
        data = agent_tool_service.execute(
            "search_character_changes",
            {"query": "对立"},
            _ctx(db, book_id),
        )
    assert data["ok"] is True
    assert "character_id" not in data["data"]["items"][0]
    assert data["data"]["items"][0]["character"]


def test_prompt_cover_block_and_no_placeholders():
    specs = agent_tool_service.specs(2)
    empty = agent_prompts.build_system_prompt(
        specs, {"synopsis": "", "protagonists": []}, 2
    )
    assert "【封面信息 · 读者已知】" in empty
    assert "简介：" not in empty
    assert "主角：" not in empty
    assert "简介：无" not in empty
    assert "主角：未标注" not in empty
    assert "问「" not in empty

    full = agent_prompts.build_system_prompt(
        specs,
        {"synopsis": "一段简介", "protagonists": ["甲某"]},
        2,
    )
    assert "简介：一段简介" in full
    assert "主角：甲某" in full

    user = agent_prompts.build_user_prompt(
        book={"title": "合成测试书"},
        chapter={"idx": 1, "title": "第2章"},
        safe_chapter=2,
        char_offset=0,
        question="问题",
        history=[],
    )
    assert "简介：" not in user
    assert "主角：" not in user
    assert "书名：合成测试书" in user
