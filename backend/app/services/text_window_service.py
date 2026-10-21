"""按章读取原文，并按位置截取窗口。

正文不存数据库，只有章节偏移；每次读都从 uploads/<book_id>/source.txt 切片。
整文件读进内存并按 mtime 缓存：五十万字约 1.5MB，比按字节 seek 简单，
也不会因为 UTF-8 变长编码把字符切断。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.chunk import Chunk
from app.services.chunk_service import CHUNK_VERSION, strip_title_prefix

_CACHE: dict[int, tuple[int, str]] = {}

SCAN_MIN_LEN = 2
NEARBY_CHARS = 120
MAX_ANCHOR_HITS = 5
FALLBACK_WINDOW = 500
MAX_RADIUS = 3


def _source_path(book_id: int) -> Path:
    return UPLOADS_DIR / str(book_id) / "source.txt"


def clear_cache(book_id: int | None = None) -> None:
    """书籍重新导入或删除后调用，避免读到旧正文。"""
    if book_id is None:
        _CACHE.clear()
    else:
        _CACHE.pop(int(book_id), None)


def _source_text(book_id: int) -> str:
    path = _source_path(book_id)
    if not path.exists():
        raise BusinessException(message="正文文件不存在，请重新导入", code=4040)
    stat = path.stat()
    cached = _CACHE.get(book_id)
    if cached and cached[0] == stat.st_mtime_ns:
        return cached[1]
    text = path.read_text(encoding="utf-8")
    _CACHE[book_id] = (stat.st_mtime_ns, text)
    return text


def source_text(book_id: int) -> str:
    """整本正文，带 mtime 缓存。只读用途（对齐、全局检索）。"""
    return _source_text(book_id)


def _paragraphs(text: str, base: int) -> list[dict[str, Any]]:
    """按空行之外的每个换行切段，偏移是源文件全局码点。"""
    result: list[dict[str, Any]] = []
    cursor = 0
    for line in text.split("\n"):
        if line.strip():
            result.append({"offset": base + cursor, "text": line})
        cursor += len(line) + 1
    return result


def get_chapter(db: Session, book_id: int, chapter_idx: int) -> Chapter:
    if not db.get(Book, book_id):
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.idx == int(chapter_idx))
        .first()
    )
    if not chapter:
        raise BusinessException(message="章节不存在", code=4040)
    return chapter


def get_chapter_content(
    db: Session, book_id: int, chapter_idx: int
) -> dict[str, Any]:
    """一章的正文，去掉首行标题，返回段落与全局偏移。"""
    chapter = get_chapter(db, book_id, chapter_idx)
    source = _source_text(book_id)
    raw = source[chapter.char_start : chapter.char_end]
    body, prefix = strip_title_prefix(raw, chapter.title)
    body = body.rstrip("\n")
    body_start = chapter.char_start + prefix
    return {
        "chapter_idx": chapter.idx,
        "title": chapter.title,
        "kind": chapter.kind,
        "char_start": body_start,
        "char_end": chapter.char_end,
        "text": body,
        "paragraphs": _paragraphs(body, body_start),
    }


def _merge_paragraphs(
    paragraphs: list[dict[str, Any]], size: int
) -> list[dict[str, Any]]:
    """没有切块时，把段落拼成约 size 字的窗口。"""
    segments: list[dict[str, Any]] = []
    buffer: list[str] = []
    start = 0
    length = 0
    for paragraph in paragraphs:
        if not buffer:
            start = paragraph["offset"]
        buffer.append(paragraph["text"])
        length += len(paragraph["text"])
        if length < size:
            continue
        text = "\n".join(buffer)
        segments.append(
            {
                "seq": len(segments),
                "char_start": start,
                "char_end": start + len(text),
                "text": text,
            }
        )
        buffer = []
        length = 0
    if buffer:
        text = "\n".join(buffer)
        segments.append(
            {
                "seq": len(segments),
                "char_start": start,
                "char_end": start + len(text),
                "text": text,
            }
        )
    return segments


def _chapter_segments(
    db: Session, book_id: int, chapter: Chapter
) -> list[dict[str, Any]]:
    """优先用检索切块；没切块时退回段落窗口。"""
    rows = (
        db.query(Chunk)
        .filter(
            Chunk.book_id == book_id,
            Chunk.chapter_idx == chapter.idx,
            Chunk.level == "child",
            Chunk.version == CHUNK_VERSION,
        )
        .order_by(Chunk.seq.asc())
        .all()
    )
    if rows:
        return [
            {
                "seq": row.seq,
                "char_start": row.char_start,
                "char_end": row.char_end,
                "text": row.text,
            }
            for row in rows
        ]
    source = _source_text(book_id)
    raw = source[chapter.char_start : chapter.char_end]
    body, prefix = strip_title_prefix(raw, chapter.title)
    return _merge_paragraphs(
        _paragraphs(body.rstrip("\n"), chapter.char_start + prefix),
        FALLBACK_WINDOW,
    )


def _name_entries(db: Session, book_id: int) -> list[dict[str, Any]]:
    """扫名用的候选池：别名 + 本名，单字不参与。

    同一个称呼可能被多个人共用（姐姐、姑娘），所以条目带的是候选 id 列表，
    不是唯一身份。扫名只负责"这段文字里出现了哪个称呼"，身份判定交给
    别名候选接口和原文一起看。
    """
    entries: dict[str, set[int]] = {}
    aliases = (
        db.query(CharacterAlias.alias, CharacterAlias.character_id)
        .filter(CharacterAlias.book_id == book_id)
        .all()
    )
    for alias, character_id in aliases:
        name = str(alias or "").strip()
        if len(name) >= SCAN_MIN_LEN:
            entries.setdefault(name, set()).add(int(character_id))
    names = (
        db.query(Character.id, Character.canonical_name)
        .filter(Character.book_id == book_id)
        .all()
    )
    for character_id, canonical in names:
        name = str(canonical or "").strip()
        if len(name) >= SCAN_MIN_LEN:
            entries.setdefault(name, set()).add(int(character_id))
    return [
        {
            "name": name,
            "character_ids": sorted(ids),
            "ambiguous": len(ids) > 1,
        }
        for name, ids in entries.items()
    ]


def _scan_with_entries(
    entries: list[dict[str, Any]], text: str
) -> list[dict[str, Any]]:
    if not text:
        return []
    ordered = sorted(entries, key=lambda item: -len(item["name"]))
    hits: list[dict[str, Any]] = []
    for entry in ordered:
        name = entry["name"]
        cursor = 0
        while True:
            pos = text.find(name, cursor)
            if pos < 0:
                break
            hits.append(
                {
                    "start": pos,
                    "end": pos + len(name),
                    "name": name,
                    "character_ids": entry["character_ids"],
                    "ambiguous": entry["ambiguous"],
                }
            )
            cursor = pos + 1
    hits.sort(key=lambda item: (item["start"], -(item["end"] - item["start"])))
    kept: list[dict[str, Any]] = []
    last_end = -1
    for hit in hits:
        if hit["start"] < last_end:
            continue
        kept.append(hit)
        last_end = hit["end"]
    return kept


def scan_names(db: Session, book_id: int, text: str) -> list[dict[str, Any]]:
    """在文本里扫出人物称呼，最长匹配优先，重叠的短匹配丢弃。"""
    return _scan_with_entries(_name_entries(db, book_id), text)


def _normalize_offset(chapter: Chapter, value: int | None) -> int | None:
    """兼容传章内偏移的情况：小于章节起点时按章内偏移处理。"""
    if value is None:
        return None
    offset = int(value)
    if offset < chapter.char_start:
        return chapter.char_start + max(0, offset)
    return offset


def _segment_index_at(segments: list[dict[str, Any]], offset: int) -> int:
    for index, segment in enumerate(segments):
        if segment["char_start"] <= offset < segment["char_end"]:
            return index
    return max(0, len(segments) - 1)


def _window(
    segments: list[dict[str, Any]], index: int, radius: int
) -> list[dict[str, Any]]:
    start = max(0, index - radius)
    end = min(len(segments), index + radius + 1)
    return segments[start:end]


def _nearest_anchor_index(
    segments: list[dict[str, Any]], anchor: str, offset: int
) -> int | None:
    best: tuple[int, int] | None = None
    for index, segment in enumerate(segments):
        cursor = segment["text"].find(anchor)
        while cursor >= 0:
            distance = abs(segment["char_start"] + cursor - offset)
            if best is None or distance < best[0]:
                best = (distance, index)
            cursor = segment["text"].find(anchor, cursor + 1)
    return None if best is None else best[1]


def _summarize_names(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for hit in hits:
        item = grouped.setdefault(
            hit["name"],
            {
                "name": hit["name"],
                "character_ids": hit["character_ids"],
                "ambiguous": hit["ambiguous"],
                "count": 0,
            },
        )
        item["count"] += 1
    return list(grouped.values())


def _anchor_hits(
    segment: dict[str, Any], anchor: str, hits: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    if not anchor:
        return []
    result: list[dict[str, Any]] = []
    cursor = segment["text"].find(anchor)
    while cursor >= 0:
        nearby = []
        for hit in hits:
            distance = min(abs(hit["start"] - cursor), abs(hit["end"] - cursor))
            if distance <= NEARBY_CHARS:
                nearby.append(
                    {
                        "name": hit["name"],
                        "character_ids": hit["character_ids"],
                        "ambiguous": hit["ambiguous"],
                        "distance": distance,
                    }
                )
        nearby.sort(key=lambda item: item["distance"])
        result.append(
            {
                "pos": cursor,
                "absolute": segment["char_start"] + cursor,
                "names_nearby": nearby[:3],
            }
        )
        cursor = segment["text"].find(anchor, cursor + 1)
    return result


def read_around(
    db: Session,
    book_id: int,
    chapter_idx: int,
    anchor: str | None = None,
    char_offset: int | None = None,
    radius: int = 1,
) -> dict[str, Any]:
    """取读者当前位置附近的原文，位置优先、锚点只在块内定位。

    有 char_offset 时以它为准：先取 ±radius 个块；块内没有 anchor 就有界扩到
    ±2；仍没有才在本章 anchor 的出现点里取离当前位置最近的一处。
    没有位置时才退化成整章按 anchor 扫描。章节不会被跨过去，避免剧透。
    """
    chapter = get_chapter(db, book_id, chapter_idx)
    anchor = str(anchor or "").strip()
    radius = max(0, min(MAX_RADIUS, int(radius)))
    offset = _normalize_offset(chapter, char_offset)
    segments = _chapter_segments(db, book_id, chapter)
    entries = _name_entries(db, book_id)

    if not segments:
        return {
            "chapter_idx": chapter.idx,
            "chapter_title": chapter.title,
            "mode": "empty",
            "anchor": anchor or None,
            "char_offset": offset,
            "segments": [],
            "truncated": False,
        }

    if offset is not None:
        index = _segment_index_at(segments, offset)
        picked = _window(segments, index, radius)
        mode = "position"
        if anchor and not any(anchor in item["text"] for item in picked):
            widened_ok = False
            for extra in (1, 2):
                widened = _window(segments, index, radius + extra)
                if any(anchor in item["text"] for item in widened):
                    picked = widened
                    widened_ok = True
                    break
            if not widened_ok:
                nearest = _nearest_anchor_index(segments, anchor, offset)
                if nearest is not None:
                    picked = _window(segments, nearest, radius)
    elif anchor:
        matched = [item for item in segments if anchor in item["text"]]
        scored = []
        for item in matched:
            hits = _scan_with_entries(entries, item["text"])
            scored.append((0 if hits else 1, item["seq"], item))
        scored.sort(key=lambda row: (row[0], row[1]))
        picked = [row[2] for row in scored[:MAX_ANCHOR_HITS]]
        mode = "anchor"
    else:
        picked = list(segments)
        mode = "whole_chapter"

    result = []
    for segment in picked:
        hits = _scan_with_entries(entries, segment["text"])
        result.append(
            {
                "seq": segment["seq"],
                "char_start": segment["char_start"],
                "char_end": segment["char_end"],
                "text": segment["text"],
                "anchors": _anchor_hits(segment, anchor, hits),
                "names": _summarize_names(hits),
            }
        )
    return {
        "chapter_idx": chapter.idx,
        "chapter_title": chapter.title,
        "mode": mode,
        "anchor": anchor or None,
        "char_offset": offset,
        "segments": result,
        "truncated": len(result) < len(segments),
    }
