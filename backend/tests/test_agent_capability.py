"""能力探测：没切块 / 没摘要 / 没索引时只暴露真正可用的工具。"""

from __future__ import annotations

from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_summary import ChapterSummary
from app.models.character import Character
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.chunk import Chunk
from app.services import agent_capability_service, agent_tool_service
from app.services.chunk_service import CHUNK_VERSION


def _book(db, *, chunks=0, summaries=0, characters=0, deltas=0):
    book = Book(
        title="合成测试书",
        author="",
        synopsis="",
        protagonists=[],
        front_matter="",
        filename="caps.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=2,
        main_chapter_count=2,
    )
    db.add(book)
    db.flush()
    for idx in range(2):
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=f"第{idx + 1}章",
                kind="main",
                char_start=idx * 100,
                char_end=idx * 100 + 90,
                word_count=90,
            )
        )
    for index in range(chunks):
        db.add(
            Chunk(
                book_id=book.id,
                version=CHUNK_VERSION,
                chapter_idx=0,
                level="child",
                seq=index,
                text="正文块",
                char_start=index * 10,
                char_end=index * 10 + 10,
            )
        )
    for index in range(summaries):
        db.add(
            ChapterSummary(
                book_id=book.id,
                chapter_idx=index,
                title=f"第{index + 1}章",
                summary="摘要",
                key_events=[],
                characters=[],
                cliffhanger="",
            )
        )
    character = None
    if characters:
        character = Character(
            book_id=book.id,
            canonical_name="甲某",
            first_seen_chapter=0,
            last_seen_chapter=1,
            mention_count=2,
        )
        db.add(character)
        db.flush()
    for index in range(deltas):
        db.add(
            CharacterChapterDelta(
                book_id=book.id,
                character_id=character.id,
                chapter_idx=index,
                identity="身份",
                traits_add=[],
                relations_add=[],
                known_facts_add=[],
                voice_sample="",
                arc="",
            )
        )
    db.commit()
    return book.id


def _mock_counts(monkeypatch, **counts):
    monkeypatch.setattr(
        "app.services.agent_capability_service._index_counts",
        lambda book_id: {
            "text": counts.get("text", 0),
            "summary": counts.get("summary", 0),
            "character": counts.get("character", 0),
            "lore": counts.get("lore", 0),
        },
    )


def test_no_chunks_no_summaries_hides_dependent_tools(monkeypatch):
    _mock_counts(monkeypatch, text=9, summary=9, character=9, lore=9)
    with SessionLocal() as db:
        book_id = _book(db)
        caps = agent_capability_service.detect(db, book_id)
    assert caps.chapters is True
    assert caps.text_index is False
    assert caps.summary_index is False
    assert caps.summaries is False
    assert caps.character_cards is False
    assert caps.alias_layer is False
    assert caps.character_index is False
    assert caps.lore_index is False
    # 有章可读就开伴读，但只开基础读工具与 L0 导航三件套。
    assert caps.enabled is True
    available = caps.available_tools()
    assert available == {
        "read_around",
        "read_chapter",
        "search_text",
        "get_toc",
        "read_span",
    }
    assert "search_summaries" not in available
    assert "get_chapter_summary" not in available
    assert "get_character" not in available
    assert "resolve_alias" not in available


def test_chunks_but_no_summaries_hides_both_summary_tools(monkeypatch):
    _mock_counts(monkeypatch, text=2, summary=9)
    with SessionLocal() as db:
        book_id = _book(db, chunks=2)
        caps = agent_capability_service.detect(db, book_id)
    assert caps.text_index is True
    assert caps.summaries is False
    assert caps.summary_index is False
    available = caps.available_tools()
    assert "search_text" in available
    assert "search_summaries" not in available
    assert "get_chapter_summary" not in available


def test_search_text_available_without_text_index(monkeypatch):
    _mock_counts(monkeypatch, text=0)
    with SessionLocal() as db:
        book_id = _book(db, chunks=2)
        caps = agent_capability_service.detect(db, book_id)
    assert caps.text_index is False
    # search_text 依赖 chapters：索引缺失也开放，运行时退回精确扫描。
    assert "search_text" in caps.available_tools()


def test_summaries_without_index_keep_exact_summary(monkeypatch):
    _mock_counts(monkeypatch, summary=0)
    with SessionLocal() as db:
        book_id = _book(db, summaries=2)
        caps = agent_capability_service.detect(db, book_id)
    available = caps.available_tools()
    assert caps.summaries is True
    assert caps.summary_index is False
    assert "get_chapter_summary" in available
    assert "search_summaries" not in available


def test_character_sqlite_tools_do_not_need_character_index(monkeypatch):
    _mock_counts(monkeypatch, character=0, lore=0)
    with SessionLocal() as db:
        book_id = _book(db, characters=1, deltas=1)
        caps = agent_capability_service.detect(db, book_id)
    available = caps.available_tools()
    assert caps.character_cards is True
    assert caps.alias_layer is True
    assert caps.character_index is False
    assert "get_character" in available
    assert "get_character_timeline" in available
    assert "resolve_alias" in available
    assert "list_alias_candidates" in available
    assert "search_character_changes" not in available


def test_character_index_requires_deltas(monkeypatch):
    _mock_counts(monkeypatch, character=9)
    with SessionLocal() as db:
        book_id = _book(db, characters=1, deltas=0)
        caps = agent_capability_service.detect(db, book_id)
    assert caps.character_cards is False
    assert caps.character_index is False


def test_specs_filter_and_disabled_execute(monkeypatch):
    with SessionLocal() as db:
        book_id = _book(db)
        book = db.get(Book, book_id)
        chapter = (
            db.query(Chapter)
            .filter(Chapter.book_id == book_id, Chapter.idx == 0)
            .one()
        )
        specs = agent_tool_service.specs(
            1, available={"read_around", "read_chapter", "get_character"}
        )
        assert [item["name"] for item in specs] == [
            "read_around",
            "read_chapter",
            "get_character",
        ]
        ctx = agent_tool_service.ToolContext(
            db=db,
            book=book,
            chapter=chapter,
            safe_chapter=0,
            char_offset=0,
            disabled_tools={"search_text"},
        )
        result = agent_tool_service.execute(
            "search_text", {"query": "某词"}, ctx
        )
    assert result["ok"] is False
    assert result["error"] == "tool_unavailable"


def test_capabilities_endpoint_keeps_read_tools_for_blank_book(client):
    with SessionLocal() as db:
        book_id = _book(db)
    caps = client.get(f"/api/books/{book_id}/agent/capabilities").json()
    assert caps["code"] == 200
    # 只有章节的书不再整体关闭：enabled=True，基础读工具可用。
    assert caps["data"]["enabled"] is True
    assert "read_chapter" in caps["data"]["tools"]
    chat = client.post(
        f"/api/books/{book_id}/agent/chat",
        json={
            "message": "这一段讲了什么",
            "chapter_idx": 0,
            "char_offset": 0,
        },
    ).json()
    # 不再以「伴读已关闭」拒绝（无模型时报别的错）。
    assert "伴读已关闭" not in chat.get("message", "")
