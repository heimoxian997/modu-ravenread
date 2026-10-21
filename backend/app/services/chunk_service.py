"""正文切块业务。

纯函数部分只依赖 Python 字符串，不依赖数据库，偏移全部使用源文件全局
Unicode 码点；数据库部分负责事务重建、统计与父块/子块嵌套返回。
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import CHILD_CHARS, CHILD_MAX_CHARS, CHILD_OVERLAP, PARENT_CHARS, UPLOADS_DIR
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chunk import Chunk
from app.schemas.chunk import (
    ChapterChunks,
    ChunkBuildStats,
    ChunkChapterStat,
    ChunkNode,
    ChunkOverview,
    ChunkWarning,
    ClearChunksResponse,
    ParentChunk,
)

CHUNK_VERSION = 1
_SENTENCE_END_CHARS = set("。！？!?；;\n")


def _source_path(book_id: int) -> Path:
    return UPLOADS_DIR / str(book_id) / "source.txt"


def strip_title_prefix(chapter_text: str, title: str) -> tuple[str, int]:
    """去掉章节正文第一行的标题，返回 (正文, 去掉的字符数)。

    优先判断首行去空白后是否等于标题；否则若标题是首行子串且首行不超过
    120 字，也视为标题行。命中去掉首行及其换行符，偏移从标题之后开始。
    """
    if not chapter_text:
        return "", 0

    title = (title or "").strip()
    if not title:
        return chapter_text, 0

    newline_index = chapter_text.find("\n")
    first_line = chapter_text if newline_index < 0 else chapter_text[:newline_index]
    first_stripped = first_line.strip()

    matched = first_stripped == title
    if not matched:
        matched = title in first_line and len(first_stripped) <= 120
    if not matched:
        return chapter_text, 0

    prefix_length = len(chapter_text) if newline_index < 0 else newline_index + 1
    return chapter_text[prefix_length:], prefix_length


def _last_sentence_end(text: str, start: int, end: int) -> int | None:
    """在 [start, end) 中从后往前找句界，返回句界之后的位置。"""
    if end <= start:
        return None
    for index in range(min(end, len(text)) - 1, start - 1, -1):
        if text[index] in _SENTENCE_END_CHARS:
            return index + 1
    return None


def _next_sentence_start(text: str, low: int, high: int) -> int:
    """在 [low, high) 中找下一个句首，找不到则从 low 硬切。"""
    if high <= low:
        return low
    for index in range(low, min(high - 1, len(text))):
        if text[index] in _SENTENCE_END_CHARS:
            return index + 1
    return low


def _split_long_span(text: str, start: int, end: int, limit: int) -> list[tuple[int, int]]:
    """把单个超长段落按句界切成不超过 limit 的区间。"""
    ranges: list[tuple[int, int]] = []
    cursor = start
    while cursor < end:
        hard_end = min(cursor + limit, end)
        if hard_end >= end:
            cut = end
        else:
            minimum = cursor + max(1, limit // 2)
            boundary = _last_sentence_end(text, minimum, hard_end)
            cut = boundary if boundary and boundary > cursor else hard_end
        ranges.append((cursor, cut))
        cursor = cut
    return ranges


def split_parents(text: str, limit: int) -> list[tuple[int, int]]:
    """按段落累积父块，保留原换行；单个超长段落再按句界切开。"""
    if limit <= 0:
        raise ValueError("parent limit 必须大于 0")
    if not text or not text.strip():
        return []

    ranges: list[tuple[int, int]] = []
    current_start = 0
    current_end = 0
    line_start = 0

    for line in text.splitlines(keepends=True):
        line_end = line_start + len(line)
        if len(line) > limit:
            if current_end > current_start:
                ranges.append((current_start, current_end))
            ranges.extend(_split_long_span(text, line_start, line_end, limit))
            current_start = line_end
            current_end = line_end
        else:
            if current_end > current_start and line_end - current_start > limit:
                ranges.append((current_start, current_end))
                current_start = current_end
            current_end = line_end
        line_start = line_end

    if current_end > current_start:
        ranges.append((current_start, current_end))
    return ranges


def split_children(
    text: str,
    parent_start: int,
    parent_end: int,
    size: int,
    overlap: int,
    max_size: int,
) -> list[tuple[int, int]]:
    """在父块内切子块。

    size 表示每块“新增正文”的目标字数：首个块从父块起点开始，之后
    从前一块结束位置向前回退最多 overlap 字，再向后续新增约 size 字。
    因此子块实际跨度最多为 size + overlap，且不超过 max_size。
    """
    if size <= 0 or max_size <= 0:
        raise ValueError("child size/max_size 必须大于 0")
    if parent_end <= parent_start:
        return []

    parent_end = min(parent_end, len(text))
    ranges: list[tuple[int, int]] = []
    start = parent_start
    previous_end: int | None = None
    previous_start = parent_start

    while start < parent_end:
        if previous_end is None:
            desired_end = min(start + size, parent_end)
        else:
            desired_end = min(previous_end + size, parent_end)

        max_end = min(start + max_size, parent_end)
        target_end = min(desired_end, max_end, parent_end)
        if target_end <= start:
            break

        # 先保证块内有足够内容，再尽量取靠近 max_end 的最后一个句界。
        search_min = min(start + max(overlap, size // 2), target_end)
        boundary = _last_sentence_end(text, search_min, max_end)
        end = boundary if boundary and boundary > start else target_end
        end = min(end, parent_end)
        if end <= start:
            end = min(start + 1, parent_end)

        ranges.append((start, end))
        if end >= parent_end:
            break

        previous_end = end
        previous_start = start
        desired_start = max(parent_start, end - overlap, previous_start + 1)
        start = _next_sentence_start(text, desired_start, end)
        if start >= end:
            start = desired_start
        if start <= previous_start:
            start = previous_start + 1

    return ranges


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _main_chapters(db: Session, book_id: int) -> list[Chapter]:
    return (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.kind == "main")
        .order_by(Chapter.idx.asc())
        .all()
    )


def _child_stats(lengths: list[int]) -> tuple[int, int, int]:
    if not lengths:
        return 0, 0, 0
    return round(sum(lengths) / len(lengths)), max(lengths), min(lengths)


def build_chunks(db: Session, book_id: int) -> ChunkBuildStats:
    """读取书籍章节与源文件，在同一事务内重建全部正文块。"""
    _book_or_404(db, book_id)
    source_path = _source_path(book_id)
    if not source_path.exists():
        raise BusinessException(message="源文件丢失，无法生成切块", code=4100)

    source = source_path.read_text(encoding="utf-8")
    chapters = _main_chapters(db, book_id)
    warnings: list[ChunkWarning] = []

    if not chapters:
        warnings.append(
            ChunkWarning(
                chapter_idx=None,
                code="no_main_chapters",
                message="本书没有正文章节，未生成切块",
            )
        )

    chapter_plans: list[dict] = []
    covered_chapters = 0
    parent_count = 0
    child_count = 0
    child_lengths: list[int] = []

    for chapter in chapters:
        if (
            chapter.char_start < 0
            or chapter.char_end > len(source)
            or chapter.char_start >= chapter.char_end
        ):
            raise BusinessException(
                message=f"第 {chapter.idx + 1} 章偏移超出源文件范围，无法生成切块",
                code=4100,
            )

        chapter_text = source[chapter.char_start : chapter.char_end]
        body, removed = strip_title_prefix(chapter_text, chapter.title)
        if not body.strip():
            warnings.append(
                ChunkWarning(
                    chapter_idx=chapter.idx,
                    code="empty_body",
                    message="该章正文为空，已跳过",
                )
            )
            continue

        body_start = chapter.char_start + removed
        parents: list[dict] = []
        parent_seq = 0
        child_seq = 0

        for parent_start, parent_end in split_parents(body, PARENT_CHARS):
            global_parent_start = body_start + parent_start
            global_parent_end = body_start + parent_end
            parent_text = source[global_parent_start:global_parent_end]
            children: list[dict] = []

            for child_start, child_end in split_children(
                body,
                parent_start,
                parent_end,
                CHILD_CHARS,
                CHILD_OVERLAP,
                CHILD_MAX_CHARS,
            ):
                global_child_start = body_start + child_start
                global_child_end = body_start + child_end
                child_text = source[global_child_start:global_child_end]
                child_lengths.append(len(child_text))
                children.append(
                    {
                        "seq": child_seq,
                        "char_start": global_child_start,
                        "char_end": global_child_end,
                        "text": child_text,
                    }
                )
                child_seq += 1

            parents.append(
                {
                    "seq": parent_seq,
                    "char_start": global_parent_start,
                    "char_end": global_parent_end,
                    "text": parent_text,
                    "children": children,
                }
            )
            parent_seq += 1

        if parents:
            covered_chapters += 1
            parent_count += len(parents)
            child_count += sum(len(parent["children"]) for parent in parents)
            chapter_plans.append({"chapter_idx": chapter.idx, "parents": parents})

    avg_child_chars, max_child_chars, min_child_chars = _child_stats(child_lengths)

    # 数据库写入阶段：先删除旧块，再插入新块；任何异常都回滚，旧块不会丢。
    try:
        db.query(Chunk).filter(Chunk.book_id == book_id).delete(
            synchronize_session=False
        )

        parent_records: list[tuple[Chunk, list[dict]]] = []
        for plan in chapter_plans:
            for parent_plan in plan["parents"]:
                parent = Chunk(
                    book_id=book_id,
                    version=CHUNK_VERSION,
                    chapter_idx=plan["chapter_idx"],
                    level="parent",
                    parent_id=None,
                    seq=parent_plan["seq"],
                    text=parent_plan["text"],
                    char_start=parent_plan["char_start"],
                    char_end=parent_plan["char_end"],
                )
                db.add(parent)
                parent_records.append((parent, parent_plan["children"]))

        db.flush()  # 取得父块 id，供子块 parent_id 使用

        for parent, children in parent_records:
            for child_plan in children:
                db.add(
                    Chunk(
                        book_id=book_id,
                        version=CHUNK_VERSION,
                        chapter_idx=parent.chapter_idx,
                        level="child",
                        parent_id=parent.id,
                        seq=child_plan["seq"],
                        text=child_plan["text"],
                        char_start=child_plan["char_start"],
                        char_end=child_plan["char_end"],
                    )
                )
        db.commit()
    except Exception:
        db.rollback()
        raise

    return ChunkBuildStats(
        chapter_count=covered_chapters,
        parent_count=parent_count,
        child_count=child_count,
        avg_child_chars=avg_child_chars,
        max_child_chars=max_child_chars,
        min_child_chars=min_child_chars,
        warnings=warnings,
    )


def get_overview(db: Session, book_id: int) -> ChunkOverview:
    """返回切块状态、全局统计、每章块数与空章警告。"""
    _book_or_404(db, book_id)
    chapters = _main_chapters(db, book_id)

    count_rows = (
        db.query(Chunk.chapter_idx, Chunk.level, func.count(Chunk.id))
        .filter(Chunk.book_id == book_id, Chunk.version == CHUNK_VERSION)
        .group_by(Chunk.chapter_idx, Chunk.level)
        .all()
    )
    counts: dict[int, dict[str, int]] = {}
    for chapter_idx, level, count in count_rows:
        counts.setdefault(chapter_idx, {})[level] = int(count)

    parent_count = sum(item.get("parent", 0) for item in counts.values())
    child_count = sum(item.get("child", 0) for item in counts.values())
    covered = {index for index, item in counts.items() if item.get("parent", 0) > 0}

    avg_child_chars, max_child_chars, min_child_chars = _child_stats_for_book(
        db, book_id
    )
    built = parent_count > 0 or child_count > 0

    chapter_stats = [
        ChunkChapterStat(
            idx=chapter.idx,
            title=chapter.title,
            parent_count=counts.get(chapter.idx, {}).get("parent", 0),
            child_count=counts.get(chapter.idx, {}).get("child", 0),
        )
        for chapter in chapters
    ]

    warnings: list[ChunkWarning] = []
    if built:
        for chapter in chapters:
            if counts.get(chapter.idx, {}).get("parent", 0) == 0:
                warnings.append(
                    ChunkWarning(
                        chapter_idx=chapter.idx,
                        code="empty_body",
                        message="该章正文为空，已跳过",
                    )
                )

    return ChunkOverview(
        built=built,
        chapter_count=len(covered),
        parent_count=parent_count,
        child_count=child_count,
        avg_child_chars=avg_child_chars,
        max_child_chars=max_child_chars,
        min_child_chars=min_child_chars,
        chapters=chapter_stats,
        warnings=warnings,
    )


def _child_stats_for_book(db: Session, book_id: int) -> tuple[int, int, int]:
    row = (
        db.query(
            func.avg(func.length(Chunk.text)),
            func.max(func.length(Chunk.text)),
            func.min(func.length(Chunk.text)),
        )
        .filter(
            Chunk.book_id == book_id,
            Chunk.version == CHUNK_VERSION,
            Chunk.level == "child",
        )
        .one()
    )
    average, maximum, minimum = row
    return (
        round(float(average)) if average is not None else 0,
        int(maximum) if maximum is not None else 0,
        int(minimum) if minimum is not None else 0,
    )


def list_chapter_chunks(db: Session, book_id: int, chapter_idx: int) -> ChapterChunks:
    """返回某章父块及其嵌套子块。"""
    _book_or_404(db, book_id)
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.idx == chapter_idx)
        .first()
    )
    if not chapter:
        raise BusinessException(message="章节不存在", code=4040)

    parents = (
        db.query(Chunk)
        .filter(
            Chunk.book_id == book_id,
            Chunk.version == CHUNK_VERSION,
            Chunk.chapter_idx == chapter_idx,
            Chunk.level == "parent",
        )
        .order_by(Chunk.seq.asc())
        .all()
    )
    children = (
        db.query(Chunk)
        .filter(
            Chunk.book_id == book_id,
            Chunk.version == CHUNK_VERSION,
            Chunk.chapter_idx == chapter_idx,
            Chunk.level == "child",
        )
        .order_by(Chunk.seq.asc())
        .all()
    )

    child_map: dict[int, list[ChunkNode]] = {}
    for child in children:
        if child.parent_id is None:
            continue
        child_map.setdefault(child.parent_id, []).append(
            ChunkNode(
                id=child.id,
                seq=child.seq,
                char_start=child.char_start,
                char_end=child.char_end,
                text=child.text,
            )
        )

    parent_items = [
        ParentChunk(
            id=parent.id,
            seq=parent.seq,
            char_start=parent.char_start,
            char_end=parent.char_end,
            text=parent.text,
            children=child_map.get(parent.id, []),
        )
        for parent in parents
    ]

    return ChapterChunks(
        chapter_idx=chapter.idx,
        title=chapter.title,
        parents=parent_items,
    )


def clear_chunks(db: Session, book_id: int) -> ClearChunksResponse:
    """删除本书全部切块，返回删除行数。"""
    _book_or_404(db, book_id)
    try:
        deleted = (
            db.query(Chunk)
            .filter(Chunk.book_id == book_id)
            .delete(synchronize_session=False)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ClearChunksResponse(deleted=int(deleted or 0))
