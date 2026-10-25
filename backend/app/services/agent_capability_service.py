"""伴读 agent 的能力探测：只暴露当前书真正可用的工具。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.chapter import Chapter
from app.models.chapter_summary import ChapterSummary
from app.models.character import Character
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.chunk import Chunk
from app.models.lore_fact import LoreFact
from app.services import entity_index_service, summary_index_service, text_index_service
from app.services.chunk_service import CHUNK_VERSION

ALL_TOOLS = {
    "read_around",
    "read_chapter",
    "search_text",
    "get_toc",
    "read_span",
    "search_summaries",
    "get_chapter_summary",
    "get_character",
    "get_character_timeline",
    "search_character_changes",
    "resolve_alias",
    "list_alias_candidates",
    "search_lore",
}

ALWAYS_TOOLS = {"read_around", "read_chapter"}

TOOL_DEPENDENCIES = {
    "search_text": "chapters",
    "get_toc": "chapters",
    "read_span": "chapters",
    "search_summaries": "summary_index",
    "get_chapter_summary": "summaries",
    "get_character": "character_cards",
    "get_character_timeline": "character_cards",
    "search_character_changes": "character_index",
    "resolve_alias": "alias_layer",
    "list_alias_candidates": "alias_layer",
    "search_lore": "lore_index",
}

REASONS = {
    "chapters": "本书还没有可读的章节",
    "text_index": "正文未切块，或正文索引尚未建立",
    "summary_index": "摘要尚未生成，或摘要索引尚未建立",
    "summaries": "本书还没有生成任何章节摘要",
    "character_cards": "人物卡尚未生成",
    "alias_layer": "人物实体层尚未生成",
    "character_index": "人物变化索引尚未建立",
    "lore_index": "世界观索引尚未建立",
}


@dataclass(frozen=True)
class AgentCapabilities:
    chapters: bool = False
    chunks: bool = False
    text_index: bool = False
    summary_index: bool = False
    summaries: bool = False
    character_cards: bool = False
    alias_layer: bool = False
    character_index: bool = False
    lore_index: bool = False

    @property
    def enabled(self) -> bool:
        # 有章可读就开基础读工具；没有任何切块/摘要/人物/世界观能力时，
        # 也至少保留 read_around / read_chapter。
        return self.chapters or any(
            (
                self.chunks,
                self.text_index,
                self.summary_index,
                self.summaries,
                self.character_cards,
                self.alias_layer,
                self.character_index,
                self.lore_index,
            )
        )

    def available_tools(self) -> set[str]:
        if not self.enabled:
            return set()
        available = set(ALWAYS_TOOLS)
        for tool, capability in TOOL_DEPENDENCIES.items():
            if getattr(self, capability):
                available.add(tool)
        return available

    def disabled_tools(self) -> set[str]:
        return ALL_TOOLS - self.available_tools()

    def reason(self, tool: str) -> str:
        capability = TOOL_DEPENDENCIES.get(tool)
        return REASONS.get(capability or "", "该工具当前不可用")


def _collection_count(
    client: Any, collection: str, count_filter: Any
) -> int:
    try:
        if not client.collection_exists(collection):
            return 0
        return int(
            client.count(
                collection_name=collection,
                count_filter=count_filter,
                exact=True,
            ).count
        )
    except Exception:  # noqa: BLE001 - 索引不可用时按 0 处理
        return 0


def _index_counts(book_id: int) -> dict[str, int]:
    try:
        client = text_index_service.get_client()
    except Exception:  # noqa: BLE001
        return {}
    counts: dict[str, int] = {}
    try:
        counts["text"] = _collection_count(
            client,
            text_index_service.get_collection_name(),
            text_index_service._book_filter(book_id),
        )
    except Exception:  # noqa: BLE001
        counts["text"] = 0
    try:
        counts["summary"] = _collection_count(
            client,
            summary_index_service.get_collection_name(),
            summary_index_service._book_filter(book_id),
        )
    except Exception:  # noqa: BLE001
        counts["summary"] = 0
    names = entity_index_service.get_collection_names()
    for layer in ("character", "lore"):
        try:
            counts[layer] = _collection_count(
                client,
                names[layer],
                entity_index_service._book_filter(book_id, layer),
            )
        except Exception:  # noqa: BLE001
            counts[layer] = 0
    return counts


def detect(db: Session, book_id: int) -> AgentCapabilities:
    """一次探测，只查 SQLite 计数和 Qdrant 的 per-book count。"""
    chapter_rows = (
        db.query(func.count(Chapter.id))
        .filter(Chapter.book_id == book_id)
        .scalar()
        or 0
    )
    chunks = (
        db.query(func.count(Chunk.id))
        .filter(
            Chunk.book_id == book_id,
            Chunk.level == "child",
            Chunk.version == CHUNK_VERSION,
        )
        .scalar()
        or 0
    )
    summaries = (
        db.query(func.count(ChapterSummary.id))
        .filter(ChapterSummary.book_id == book_id)
        .scalar()
        or 0
    )
    characters = (
        db.query(func.count(Character.id))
        .filter(Character.book_id == book_id)
        .scalar()
        or 0
    )
    deltas = (
        db.query(func.count(CharacterChapterDelta.id))
        .filter(CharacterChapterDelta.book_id == book_id)
        .scalar()
        or 0
    )
    lore_rows = (
        db.query(func.count(LoreFact.id))
        .filter(LoreFact.book_id == book_id)
        .scalar()
        or 0
    )

    counts = (
        _index_counts(book_id)
        if (chunks or summaries or deltas or lore_rows)
        else {}
    )
    return AgentCapabilities(
        chapters=bool(chapter_rows),
        chunks=bool(chunks),
        text_index=bool(chunks and counts.get("text", 0)),
        summary_index=bool(summaries and counts.get("summary", 0)),
        summaries=bool(summaries),
        character_cards=bool(deltas),
        alias_layer=bool(characters),
        character_index=bool(deltas and counts.get("character", 0)),
        lore_index=bool(counts.get("lore", 0)),
    )
