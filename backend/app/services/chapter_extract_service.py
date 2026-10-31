"""单章结构化抽取与摘要读写。

本步只做单章试跑与既有摘要编辑：不写向量、不调用 embedding、不做全量调度。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import asc, desc
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.chapter_summary import ChapterSummary
from app.models.summary_issue import SummaryIssue
from app.schemas.summary import ExtractionParsedPatch, SummaryUpdate
from app.services import chapter_extract_prompt as prompt
from app.services import (
    alias_check_service,
    chunk_service,
    llm_provider_service,
    llm_service,
    text_window_service,
)

LLM_TIMEOUT_SECONDS = 300
# 首调已经等满 300 秒没回来，重试再给同样的时长几乎必然还是等满，所以只给 120 秒
LLM_TIMEOUT_RETRY_SECONDS = 120
KNOWN_LORE_LIMIT = 50
PARSED_CHARACTER_KEYS = ("mentions", "card_deltas", "new_aliases")
PARSED_LIMIT_BYTES = 200_000
KNOWN_LORE_SUMMARY_CHARS = 40


def _source_path(book_id: int) -> Path:
    return UPLOADS_DIR / str(book_id) / "source.txt"


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _main_chapter(db: Session, book_id: int, chapter_idx: int) -> Chapter:
    chapter = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.idx == chapter_idx)
        .first()
    )
    if not chapter:
        raise BusinessException(message="章节不存在", code=4040)
    if chapter.kind != "main":
        raise BusinessException(message="番外章节不支持摘要抽取", code=4000)
    return chapter


def _read_chapter_text(db: Session, book: Book, chapter: Chapter) -> tuple[str, bool]:
    source_path = _source_path(book.id)
    if not source_path.exists():
        raise BusinessException(message="源文件丢失，无法读取章节正文", code=4100)
    source = source_path.read_text(encoding="utf-8")
    if chapter.char_end > len(source):
        raise BusinessException(
            message="章节偏移超出源文件范围，数据可能已损坏", code=4100
        )
    raw = source[chapter.char_start : chapter.char_end]
    body, _ = chunk_service.strip_title_prefix(raw, chapter.title)
    body = body.strip()
    if not body:
        raise BusinessException(message="章节正文为空，无法抽取摘要", code=4000)
    truncated = len(body) > 10000
    return body[:10000], truncated


def _profile(name: str, profiles: dict[str, dict[str, Any]]) -> dict[str, Any]:
    key = name.strip()
    if not key:
        return {
            "name": "",
            "aliases": [],
            "identity": "",
            "traits": [],
            "relations": [],
            "known_facts": [],
            "voice_sample": "",
        }
    if key not in profiles:
        profiles[key] = {
            "name": key,
            "aliases": [],
            "identity": "",
            "traits": [],
            "relations": [],
            "known_facts": [],
            "voice_sample": "",
            "_mentions": 0,
            "_last_seen": -1,
        }
    return profiles[key]


def _append_unique(target: list, values: list) -> None:
    for value in values:
        text = str(value).strip() if value is not None else ""
        if text and text not in target:
            target.append(text)


def _known_characters(
    db: Session, book: Book, chapter_idx: int
) -> list[dict[str, Any]]:
    profiles: dict[str, dict[str, Any]] = {}
    alias_map: dict[str, str] = {}
    for name in book.protagonists or []:
        raw_name = str(name).strip()
        if not raw_name:
            continue
        # 主角配置里的“林晚（阿晚）”与模型输出“林晚”应归并为同一张卡。
        canonical = re.sub(r"[（(].*?[）)]", "", raw_name).strip() or raw_name
        if canonical != raw_name:
            alias_map[raw_name] = canonical
        profile = _profile(canonical, profiles)
        _append_unique(profile["aliases"], [raw_name] if raw_name != canonical else [])

    rows = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book.id,
            ChapterExtraction.chapter_idx < chapter_idx,
            ChapterExtraction.status == "success",
        )
        .order_by(asc(ChapterExtraction.chapter_idx))
        .all()
    )
    for row in rows:
        parsed = row.parsed or {}
        characters = parsed.get("characters") or {}
        for item in characters.get("new_aliases") or []:
            alias = str(item.get("alias") or "").strip()
            canonical = str(item.get("canonical") or "").strip()
            if alias and canonical:
                alias_map[alias] = canonical
                profile = _profile(canonical, profiles)
                _append_unique(profile["aliases"], [alias])
        for item in characters.get("mentions") or []:
            raw_name = str(item.get("name") or "").strip()
            name = alias_map.get(raw_name, raw_name)
            profile = _profile(name, profiles)
            if not profile["name"]:
                continue
            _append_unique(profile["aliases"], item.get("aliases") or [])
            profile["_mentions"] += 1
            profile["_last_seen"] = max(profile["_last_seen"], row.chapter_idx)
        for item in characters.get("card_deltas") or []:
            raw_name = str(item.get("character") or "").strip()
            name = alias_map.get(raw_name, raw_name)
            profile = _profile(name, profiles)
            if not profile["name"]:
                continue
            if item.get("identity"):
                profile["identity"] = str(item["identity"]).strip()
            _append_unique(profile["traits"], item.get("traits") or [])
            _append_unique(profile["relations"], item.get("relations") or [])
            _append_unique(profile["known_facts"], item.get("known_facts") or [])
            if item.get("voice_sample"):
                profile["voice_sample"] = str(item["voice_sample"]).strip()
            profile["_mentions"] += 1
            profile["_last_seen"] = max(profile["_last_seen"], row.chapter_idx)

    protagonist_names = {
        re.sub(r"[（(].*?[）)]", "", str(name)).strip() or str(name)
        for name in (book.protagonists or [])
    }
    ranked = sorted(
        profiles.values(),
        key=lambda item: (
            1 if item["name"] in protagonist_names else 0,
            item["_last_seen"],
            item["_mentions"],
        ),
        reverse=True,
    )
    result = []
    for item in ranked[:15]:
        aliases = [alias for alias in item["aliases"] if alias != item["name"]]
        result.append(
            {
                "name": item["name"],
                "aliases": aliases[:10],
                "identity": item["identity"],
                "traits": item["traits"][:3],
                "relations": item["relations"][:3],
                "known_facts": item["known_facts"][:4],
                "voice_sample": item["voice_sample"],
            }
        )
    return result


def _known_lore(
    db: Session, book_id: int, chapter_idx: int, text: str
) -> list[dict[str, Any]]:
    """本章正文提到的已知世界观条目，带上次记录的概述。

    只发命中的：没提到的条目对本章没有价值，发过去只挤占上下文。
    世界观名是专有名词（地点/组织/器物），正文提到就会写出来，扫名字可靠。
    """
    rows = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.chapter_idx < chapter_idx,
            ChapterExtraction.status == "success",
        )
        .order_by(asc(ChapterExtraction.chapter_idx))
        .all()
    )
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        parsed = row.parsed or {}
        for item in parsed.get("lore") or []:
            name = str(item.get("name") or "").strip()
            if not name or name not in text:
                continue
            latest[name] = {
                "name": name,
                "category": item.get("category") or "world_rule",
                "summary": str(item.get("summary") or "").strip()[
                    :KNOWN_LORE_SUMMARY_CHARS
                ],
                "_last_seen": row.chapter_idx,
            }
    ranked = sorted(
        latest.values(),
        key=lambda item: (item["_last_seen"], item["name"]),
        reverse=True,
    )
    return [
        {
            "name": item["name"],
            "category": item["category"],
            "summary": item["summary"],
        }
        for item in ranked[:KNOWN_LORE_LIMIT]
    ]


def _known_character_names(
    db: Session, book: Book, chapter_idx: int
) -> set[str]:
    """全书已出现的人物规范名 + 主角，用于把人物从 lore 里剔掉。"""
    names: set[str] = set()
    for name in book.protagonists or []:
        raw = str(name).strip()
        if not raw:
            continue
        canonical = re.sub(r"[（(].*?[）)]", "", raw).strip() or raw
        names.add(canonical)
    rows = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book.id,
            ChapterExtraction.chapter_idx < chapter_idx,
            ChapterExtraction.status == "success",
        )
        .all()
    )
    for row in rows:
        characters = (row.parsed or {}).get("characters") or {}
        for item in characters.get("mentions") or []:
            name = str(item.get("name") or "").strip()
            if name:
                names.add(name)
        for item in characters.get("new_aliases") or []:
            canonical = str(item.get("canonical") or "").strip()
            if canonical:
                names.add(canonical)
    return names


def _drop_character_lore(
    db: Session, book: Book, chapter_idx: int, parsed: dict[str, Any]
) -> list[dict[str, Any]]:
    """lore 里与人物规范名重名的条目直接丢弃。

    人物归 characters，不归世界观；同一名字两边都记会让检索互相打架。
    """
    names = _known_character_names(db, book, chapter_idx)
    characters = parsed.get("characters") or {}
    for item in characters.get("mentions") or []:
        name = str(item.get("name") or "").strip()
        if name:
            names.add(name)
    for item in characters.get("new_aliases") or []:
        canonical = str(item.get("canonical") or "").strip()
        if canonical:
            names.add(canonical)
    kept: list[dict[str, Any]] = []
    for item in parsed.get("lore") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name and name in names:
            continue
        kept.append(item)
    return kept


def _build_messages(
    db: Session,
    book: Book,
    chapter: Chapter,
    text: str,
    text_truncated: bool,
    known_characters: list[dict[str, Any]] | None = None,
    known_lore: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
    previous = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == book.id,
            ChapterSummary.chapter_idx < chapter.idx,
        )
        .order_by(desc(ChapterSummary.chapter_idx))
        .first()
    )
    payload = {
        "book_title": book.title,
        "synopsis": book.synopsis or "",
        "protagonists": book.protagonists or [],
        "chapter_index": chapter.idx,
        "chapter_title": chapter.title,
        "previous_summary": (previous.summary[-300:] if previous else ""),
        "text_truncated": text_truncated,
        "known_characters": (
            known_characters
            if known_characters is not None
            else _known_characters(db, book, chapter.idx)
        ),
        "known_lore": (
            known_lore
            if known_lore is not None
            else _known_lore(db, book.id, chapter.idx, text)
        ),
        "chapter_text": text,
    }
    return [
        {"role": "system", "content": prompt.SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def _record_issue(db: Session, book_id: int, chapter_idx: int, error: str) -> None:
    issue = (
        db.query(SummaryIssue)
        .filter(
            SummaryIssue.book_id == book_id,
            SummaryIssue.chapter_idx == chapter_idx,
        )
        .first()
    )
    error = (error or "未知错误")[:1000]
    if not issue:
        issue = SummaryIssue(book_id=book_id, chapter_idx=chapter_idx, error=error)
        db.add(issue)
    else:
        issue.error = error


def _remove_issue(db: Session, book_id: int, chapter_idx: int) -> None:
    issue = (
        db.query(SummaryIssue)
        .filter(
            SummaryIssue.book_id == book_id,
            SummaryIssue.chapter_idx == chapter_idx,
        )
        .first()
    )
    if issue:
        db.delete(issue)


def summary_to_dict(summary: ChapterSummary) -> dict[str, Any]:
    return {
        "id": summary.id,
        "chapter_idx": summary.chapter_idx,
        "title": summary.title,
        "summary": summary.summary,
        "key_events": summary.key_events or [],
        "characters": summary.characters or [],
        "cliffhanger": summary.cliffhanger or "",
        "summary_truncated": summary.summary_truncated,
        "edited": summary.edited,
        "prompt_version": summary.prompt_version,
        "provider_name": summary.provider_name,
        "model": summary.model,
        "elapsed_ms": summary.elapsed_ms,
    }


def extraction_to_dict(
    extraction: ChapterExtraction,
    include_parsed: bool = False,
    include_raw: bool = False,
    include_alias_check: bool = False,
) -> dict[str, Any]:
    result = {
        "id": extraction.id,
        "chapter_idx": extraction.chapter_idx,
        "title": extraction.title,
        "status": extraction.status,
        "prompt_version": extraction.prompt_version,
        "provider_name": extraction.provider_name,
        "model": extraction.model,
        "text_truncated": extraction.text_truncated,
        "output_trimmed": extraction.output_trimmed,
        "elapsed_ms": extraction.elapsed_ms,
    }
    if include_parsed:
        result["parsed"] = extraction.parsed
    if include_raw:
        result["raw_content"] = extraction.raw_content
    if include_alias_check:
        result["alias_check"] = extraction.alias_check
    return result


def _save_success(
    db: Session,
    book_id: int,
    chapter: Chapter,
    provider: dict[str, Any],
    parsed: dict[str, Any],
    raw_content: str,
    text_truncated: bool,
    output_trimmed: bool,
    summary_truncated: bool,
    elapsed_ms: int,
    alias_report: dict[str, Any] | None = None,
) -> tuple[ChapterExtraction, ChapterSummary]:
    extraction = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.chapter_idx == chapter.idx,
        )
        .first()
    )
    if not extraction:
        extraction = ChapterExtraction(book_id=book_id, chapter_idx=chapter.idx)
        db.add(extraction)
    extraction.title = chapter.title
    extraction.status = "success"
    extraction.prompt_version = prompt.PROMPT_VERSION
    extraction.provider_name = provider["provider_name"]
    extraction.model = provider["model"]
    extraction.text_truncated = text_truncated
    extraction.output_trimmed = output_trimmed
    extraction.elapsed_ms = elapsed_ms
    extraction.raw_content = raw_content
    extraction.parsed = parsed
    extraction.alias_check = alias_report

    summary = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == book_id,
            ChapterSummary.chapter_idx == chapter.idx,
        )
        .first()
    )
    if not summary:
        summary = ChapterSummary(book_id=book_id, chapter_idx=chapter.idx)
        db.add(summary)
    summary.title = chapter.title
    summary.summary = parsed["summary"]["summary"]
    summary.characters = parsed["summary"]["characters"]
    summary.cliffhanger = parsed["summary"]["cliffhanger"]
    summary.summary_truncated = summary_truncated
    summary.edited = False
    summary.prompt_version = prompt.PROMPT_VERSION
    summary.provider_name = provider["provider_name"]
    summary.model = provider["model"]
    summary.elapsed_ms = elapsed_ms
    try:
        summary.key_events = parsed["summary"]["key_events"]
        _remove_issue(db, book_id, chapter.idx)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(extraction)
    db.refresh(summary)
    return extraction, summary


def _save_parse_failed_extraction(
    db: Session,
    book_id: int,
    chapter: Chapter,
    provider: dict[str, Any],
    raw_content: str | None,
    text_truncated: bool,
    elapsed_ms: int,
) -> ChapterExtraction:
    extraction = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.chapter_idx == chapter.idx,
        )
        .first()
    )
    if not extraction:
        extraction = ChapterExtraction(book_id=book_id, chapter_idx=chapter.idx)
        db.add(extraction)
    extraction.title = chapter.title
    extraction.status = "parse_failed"
    extraction.prompt_version = prompt.PROMPT_VERSION
    extraction.provider_name = provider["provider_name"]
    extraction.model = provider["model"]
    extraction.text_truncated = text_truncated
    extraction.output_trimmed = False
    extraction.elapsed_ms = elapsed_ms
    extraction.raw_content = raw_content
    extraction.parsed = None
    return extraction


def _call_llm(
    provider: dict[str, Any],
    messages: list[dict[str, str]],
    client=None,
    timeout: float = LLM_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    return llm_service.chat_completion(
        provider,
        messages,
        max_tokens=8000,
        temperature=0.2,
        timeout=timeout,
        client=client,
    )


def extract_chapter(
    db: Session,
    book_id: int,
    chapter_idx: int,
    provider_id: int | None = None,
    client=None,
    check_provider_id: int | None = None,
    check_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """同步抽取一章；失败时保留已有成功摘要。"""
    book = _book_or_404(db, book_id)
    chapter = _main_chapter(db, book_id, chapter_idx)
    provider = llm_provider_service.get_active_provider_config(db, provider_id)
    text, text_truncated = _read_chapter_text(db, book, chapter)
    known_characters = _known_characters(db, book, chapter.idx)
    known_lore = _known_lore(db, book.id, chapter.idx, text)
    old_summary = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == book_id,
            ChapterSummary.chapter_idx == chapter_idx,
        )
        .first()
    )
    messages = _build_messages(
        db,
        book,
        chapter,
        text,
        text_truncated,
        known_characters=known_characters,
        known_lore=known_lore,
    )
    started = time.perf_counter()
    first = _call_llm(provider, messages, client=client)
    if first.get("error") and first.get("error_code") == "timeout":
        retry = _call_llm(
            provider, messages, client=client, timeout=LLM_TIMEOUT_RETRY_SECONDS
        )
        if not retry.get("error"):
            first = retry
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    if first.get("error"):
        _record_issue(db, book_id, chapter_idx, first["error"])
        db.commit()
        return {
            "status": "failed",
            "chapter_idx": chapter_idx,
            "extraction": None,
            "summary": summary_to_dict(old_summary) if old_summary else None,
            "raw_content": None,
            "error": first["error"],
        }

    raw_content = first.get("content") or ""
    parsed = None
    parse_error = ""
    try:
        parsed = prompt.parse_extraction(raw_content)
    except prompt.ExtractionParseError as exc:
        parse_error = exc.message if hasattr(exc, "message") else str(exc)
        retry_messages = messages + [
            {"role": "assistant", "content": raw_content},
            {"role": "user", "content": prompt.PARSE_RETRY_HINT},
        ]
        second = _call_llm(provider, retry_messages, client=client)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if second.get("error"):
            parse_error = f"{parse_error}；重试失败：{second['error']}"
        else:
            raw_content = second.get("content") or ""
            try:
                parsed = prompt.parse_extraction(raw_content)
                parse_error = ""
            except prompt.ExtractionParseError as second_exc:
                parse_error = str(second_exc)

    if parsed is None:
        _record_issue(db, book_id, chapter_idx, parse_error)
        extraction = None
        if not old_summary:
            extraction = _save_parse_failed_extraction(
                db,
                book_id,
                chapter,
                provider,
                raw_content,
                text_truncated,
                elapsed_ms,
            )
        db.commit()
        if extraction:
            db.refresh(extraction)
        return {
            "status": "parse_failed",
            "chapter_idx": chapter_idx,
            "extraction": (extraction_to_dict(extraction) if extraction else None),
            "summary": summary_to_dict(old_summary) if old_summary else None,
            "raw_content": raw_content,
            "error": parse_error or "模型输出不是合法 JSON",
        }

    if prompt.needs_compression(parsed):
        retry_messages = messages + [
            {"role": "assistant", "content": raw_content},
            {"role": "user", "content": prompt.COMPRESS_RETRY_HINT},
        ]
        compressed = _call_llm(provider, retry_messages, client=client)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if not compressed.get("error"):
            candidate_raw = compressed.get("content") or ""
            try:
                candidate = prompt.parse_extraction(candidate_raw)
            except prompt.ExtractionParseError:
                candidate = None
            if candidate is not None:
                parsed = candidate
                raw_content = candidate_raw

    parsed, summary_truncated, output_trimmed = prompt.enforce_limits(parsed)
    # 单章自检：错的称谓当场删掉，不留给后面章节继续沿用。
    # 用完整正文而不是可能被截断的抽取输入，避免尾部误删。
    full_text = text_window_service.get_chapter_content(
        db, book_id, chapter_idx
    )["text"]
    parsed, alias_report = alias_check_service.run(
        db,
        parsed,
        full_text,
        check_provider_id=check_provider_id,
        client=client,
        known_characters=known_characters,
        protagonists=book.protagonists or [],
        state=check_state,
        # 规则层清掉编造/自指称谓之后，再用清理过的名单过滤世界观，
        # 避免一条编出来的称谓把同名 lore 整条挤掉。
        after_prune=lambda value: {
            **value,
            "lore": _drop_character_lore(db, book, chapter_idx, value),
        },
    )
    try:
        extraction, summary = _save_success(
            db,
            book_id,
            chapter,
            provider,
            parsed,
            raw_content,
            text_truncated,
            output_trimmed,
            summary_truncated,
            elapsed_ms,
            alias_report,
        )
    except Exception as exc:
        _record_issue(db, book_id, chapter_idx, str(exc))
        db.commit()
        raise
    return {
        "status": "success",
        "chapter_idx": chapter_idx,
        "extraction": extraction_to_dict(extraction),
        "summary": summary_to_dict(summary),
        "raw_content": None,
        "error": None,
        "alias_check": alias_report,
    }


def extract_sequence(
    db: Session,
    book_id: int,
    limit: int = 3,
    provider_id: int | None = None,
    client=None,
    progress: Callable[[int, int, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """CLI 试跑：按正文顺序处理前 N 章，不做断点或停止。"""
    _book_or_404(db, book_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.kind == "main")
        .order_by(asc(Chapter.idx))
        .limit(max(1, limit))
        .all()
    )
    results = []
    total = len(chapters)
    for position, chapter in enumerate(chapters, start=1):
        result = extract_chapter(
            db, book_id, chapter.idx, provider_id=provider_id, client=client
        )
        results.append(result)
        if progress:
            progress(position, total, result)
    return results


def _summary_status(summary: ChapterSummary | None, issue: SummaryIssue | None) -> str:
    if summary:
        return "done"
    if issue:
        return "failed"
    return "pending"


def plan_batch(
    db: Session,
    book_id: int,
    limit: int | None = None,
    stop_at: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
) -> dict[str, Any]:
    """规划本次批量范围；不写库、不调用模型。"""
    if limit is not None and stop_at is not None:
        raise BusinessException(message="limit 和 stop_at 只能选一个", code=4000)
    if limit is not None and limit < 1:
        raise BusinessException(message="limit 必须大于等于 1", code=4000)
    if stop_at is not None and stop_at < 1:
        raise BusinessException(message="stop_at 必须大于等于 1", code=4000)

    _book_or_404(db, book_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.kind == "main")
        .order_by(asc(Chapter.idx))
        .all()
    )
    chapter_idxs = [chapter.idx for chapter in chapters]
    chapter_map = {chapter.idx: chapter for chapter in chapters}
    done = {
        row.chapter_idx
        for row in db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.status == "success",
        )
        .all()
    } & set(chapter_idxs)
    issues = {
        row.chapter_idx
        for row in db.query(SummaryIssue).filter(SummaryIssue.book_id == book_id).all()
    } & set(chapter_idxs)

    if force:
        candidates = list(chapter_idxs)
    elif retry_failed:
        candidates = [idx for idx in chapter_idxs if idx not in done or idx in issues]
    else:
        candidates = [idx for idx in chapter_idxs if idx not in done]

    if stop_at is not None:
        candidates = [idx for idx in candidates if idx <= stop_at - 1]
    if limit is not None:
        candidates = candidates[:limit]

    next_idx = next((idx for idx in chapter_idxs if idx not in done), None)
    failed = len(issues - done)
    overview_done = len(done)
    return {
        "candidates": candidates,
        "planned": len(candidates),
        "first_chapter_idx": candidates[0] if candidates else None,
        "last_chapter_idx": candidates[-1] if candidates else None,
        "total_main": len(chapters),
        "done": overview_done,
        "failed": failed,
        "pending": max(0, len(chapters) - overview_done - failed),
        "next_chapter_idx": next_idx,
        "next_chapter_number": next_idx + 1 if next_idx is not None else None,
        "next_chapter_title": (
            chapter_map[next_idx].title if next_idx is not None else None
        ),
    }


def get_next_pending(db: Session, book_id: int) -> dict[str, Any]:
    plan = plan_batch(db, book_id)
    return {
        "chapter_idx": plan["next_chapter_idx"],
        "chapter_number": plan["next_chapter_number"],
        "chapter_title": plan["next_chapter_title"],
    }


def extract_batch(
    db: Session,
    book_id: int,
    limit: int | None = None,
    stop_at: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
    provider_id: int | None = None,
    client=None,
    progress: Callable[[int, int, int, dict[str, Any]], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    check_provider_id: int | None = None,
) -> dict[str, Any]:
    """按计划逐章抽取；每章独立提交，失败记 issue 后继续。"""
    plan = plan_batch(
        db,
        book_id,
        limit=limit,
        stop_at=stop_at,
        force=force,
        retry_failed=retry_failed,
    )
    if plan["planned"] == 0:
        next_info = get_next_pending(db, book_id)
        return {
            "book_id": book_id,
            "planned": 0,
            "generated": 0,
            "failed": 0,
            "stopped": False,
            "next_chapter_idx": next_info["chapter_idx"],
            "next_chapter_number": next_info["chapter_number"],
            "next_chapter_title": next_info["chapter_title"],
            "elapsed_seconds": 0.0,
            "provider": None,
            "model": None,
            "alias_dropped": 0,
            "alias_flagged": 0,
            "check_disabled": False,
        }

    provider = llm_provider_service.get_active_provider_config(db, provider_id)
    candidates = plan["candidates"]
    total = len(candidates)
    generated = 0
    failed = 0
    alias_dropped = 0
    alias_flagged = 0
    check_state: dict[str, Any] = {}
    stopped = False
    started = time.perf_counter()

    for position, chapter_idx in enumerate(candidates, start=1):
        chapter_started = time.perf_counter()
        try:
            result = extract_chapter(
                db,
                book_id,
                chapter_idx,
                provider_id=provider_id,
                client=client,
                check_provider_id=check_provider_id,
                check_state=check_state,
            )
        except BusinessException as exc:
            db.rollback()
            if exc.code == 4100:
                raise
            _record_issue(db, book_id, chapter_idx, exc.message)
            db.commit()
            result = {
                "status": "failed",
                "chapter_idx": chapter_idx,
                "extraction": None,
                "summary": None,
                "raw_content": None,
                "error": exc.message,
            }
        except Exception:
            db.rollback()
            raise

        result["_batch_elapsed_seconds"] = round(
            time.perf_counter() - chapter_started, 3
        )
        if result.get("status") == "success":
            generated += 1
            report = result.get("alias_check") or {}
            alias_dropped += int(report.get("dropped") or 0)
            alias_flagged += int(report.get("flagged") or 0)
        else:
            failed += 1
        if progress:
            progress(position, total, chapter_idx, result)
        if should_stop and should_stop():
            stopped = True
            break

    next_info = get_next_pending(db, book_id)
    return {
        "book_id": book_id,
        "planned": total,
        "generated": generated,
        "failed": failed,
        "stopped": stopped,
        "next_chapter_idx": next_info["chapter_idx"],
        "next_chapter_number": next_info["chapter_number"],
        "next_chapter_title": next_info["chapter_title"],
        "elapsed_seconds": round(time.perf_counter() - started, 1),
        "provider": provider["provider_name"],
        "model": provider["model"],
        "alias_dropped": alias_dropped,
        "alias_flagged": alias_flagged,
        "check_disabled": bool(check_state.get("disabled")),
    }


def get_overview(db: Session, book_id: int) -> dict[str, Any]:
    _book_or_404(db, book_id)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.kind == "main")
        .order_by(asc(Chapter.idx))
        .all()
    )
    indexes = [chapter.idx for chapter in chapters]
    summaries = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == book_id,
            ChapterSummary.chapter_idx.in_(indexes),
        )
        .all()
        if indexes
        else []
    )
    issues = (
        db.query(SummaryIssue)
        .filter(
            SummaryIssue.book_id == book_id,
            SummaryIssue.chapter_idx.in_(indexes),
        )
        .all()
        if indexes
        else []
    )
    done = len({item.chapter_idx for item in summaries})
    failed = len(
        {
            item.chapter_idx
            for item in issues
            if item.chapter_idx not in {s.chapter_idx for s in summaries}
        }
    )
    next_info = get_next_pending(db, book_id)
    from app.services import follow_summary_view
    from app.models.companion_read import CompanionRead
    available = {item.chapter_idx for item in summaries}
    complete_follow, partial_follow = set(), set()
    candidates = db.query(CompanionRead.chapter_idx).filter(CompanionRead.book_id == book_id,
        CompanionRead.chapter_idx.in_(indexes)).distinct().all()
    for (idx,) in candidates:
        view = follow_summary_view.get(db, book_id, idx, respect_progress=False, include_events=False)
        if view:
            (complete_follow if view["complete"] else partial_follow).add(idx)
    available.update(complete_follow)
    next_available = next((c for c in chapters if c.idx not in available), None)
    return {
        "available_done": len(available), "follow_done": len(complete_follow), "follow_partial": len(partial_follow),
        "next_available_number": next_available.idx + 1 if next_available else None,
        "total": len(chapters),
        "done": done,
        "failed": failed,
        "pending": max(0, len(chapters) - done - failed),
        "next_chapter_idx": next_info["chapter_idx"],
        "next_chapter_number": next_info["chapter_number"],
        "next_chapter_title": next_info["chapter_title"],
    }


def list_summaries(
    db: Session,
    book_id: int,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
) -> dict[str, Any]:
    _book_or_404(db, book_id)
    if status not in {None, "all", "done", "pending", "failed"}:
        raise BusinessException(message="status 参数不合法", code=4000)
    chapters = (
        db.query(Chapter)
        .filter(Chapter.book_id == book_id, Chapter.kind == "main")
        .order_by(asc(Chapter.idx))
        .all()
    )
    summaries = {
        item.chapter_idx: item
        for item in db.query(ChapterSummary)
        .filter(ChapterSummary.book_id == book_id)
        .all()
    }
    issues = {
        item.chapter_idx: item
        for item in db.query(SummaryIssue).filter(SummaryIssue.book_id == book_id).all()
    }
    items = []
    for chapter in chapters:
        summary = summaries.get(chapter.idx)
        issue = issues.get(chapter.idx)
        chapter_status = _summary_status(summary, issue)
        if status not in {None, "all"} and chapter_status != status:
            continue
        items.append(
            {
                "chapter_idx": chapter.idx,
                "title": chapter.title,
                "status": chapter_status,
                "summary_preview": ((summary.summary or "")[:80] if summary else ""),
                "edited": bool(summary and summary.edited),
                "issue": issue.error if issue and not summary else None,
                "provider_name": summary.provider_name if summary else None,
                "model": summary.model if summary else None,
                "update_time": summary.update_time if summary else None,
            }
        )
    total = len(items)
    page = max(1, page)
    page_size = min(100, max(1, page_size))
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def get_summary_detail(db: Session, book_id: int, chapter_idx: int) -> dict[str, Any]:
    _book_or_404(db, book_id)
    chapter = _main_chapter(db, book_id, chapter_idx)
    summary = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == book_id,
            ChapterSummary.chapter_idx == chapter_idx,
        )
        .first()
    )
    extraction = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.chapter_idx == chapter_idx,
        )
        .first()
    )
    issue = (
        db.query(SummaryIssue)
        .filter(
            SummaryIssue.book_id == book_id,
            SummaryIssue.chapter_idx == chapter_idx,
        )
        .first()
    )
    status = _summary_status(summary, issue)
    extraction_data = None
    if extraction:
        extraction_data = extraction_to_dict(
            extraction,
            include_parsed=True,
            include_raw=extraction.status == "parse_failed",
            include_alias_check=True,
        )
    return {
        "chapter_idx": chapter.idx,
        "title": chapter.title,
        "status": status,
        "summary": summary_to_dict(summary) if summary else None,
        "extraction": extraction_data,
        "issue": (
            {"error": issue.error, "create_time": issue.create_time} if issue else None
        ),
    }


def _clean_character_block(value: Any) -> dict[str, Any]:
    """只接受抽取结构里的三个数组字段，整体替换。"""
    if not isinstance(value, dict):
        raise BusinessException(message="人物结构必须是 JSON 对象", code=4000)
    extra = set(value) - set(PARSED_CHARACTER_KEYS)
    if extra:
        raise BusinessException(
            message=f"人物结构不支持这些字段：{'、'.join(sorted(extra))}",
            code=4000,
        )
    cleaned: dict[str, Any] = {}
    for key in PARSED_CHARACTER_KEYS:
        if key not in value:
            continue
        items = value[key]
        if not isinstance(items, list):
            raise BusinessException(message=f"{key} 必须是数组", code=4000)
        for item in items:
            if not isinstance(item, dict):
                raise BusinessException(
                    message=f"{key} 里的每一项必须是对象", code=4000
                )
        cleaned[key] = items
    return cleaned


def _clean_lore_block(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise BusinessException(message="世界观条目必须是数组", code=4000)
    cleaned: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BusinessException(
                message="世界观条目的每一项必须是对象", code=4000
            )
        cleaned.append(item)
    return cleaned


def update_extraction_parsed(
    db: Session,
    book_id: int,
    chapter_idx: int,
    payload: ExtractionParsedPatch,
) -> dict[str, Any]:
    """改抽取结果里的原始人物结构与世界观条目。

    只动 parsed.characters / parsed.lore，摘要与事件保持原样。保存后把人物层和
    世界观层标记为待重建，不自动重建——全书重放很慢，交给用户决定时机。
    """
    from app.services import entity_override_service

    _book_or_404(db, book_id)
    _main_chapter(db, book_id, chapter_idx)
    extraction = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.chapter_idx == chapter_idx,
        )
        .first()
    )
    if not extraction:
        raise BusinessException(message="该章还没有抽取结果", code=4000)
    if extraction.status != "success":
        raise BusinessException(message="该章抽取失败，请先重新抽取", code=4000)

    parsed = dict(extraction.parsed or {})
    data = payload.model_dump(exclude_unset=True)
    if data.get("characters") is not None:
        parsed["characters"] = _clean_character_block(data["characters"])
    if data.get("lore") is not None:
        parsed["lore"] = _clean_lore_block(data["lore"])

    size = len(json.dumps(parsed, ensure_ascii=False).encode("utf-8"))
    if size > PARSED_LIMIT_BYTES:
        raise BusinessException(
            message=f"抽取结果超过 {PARSED_LIMIT_BYTES // 1024}KB 上限",
            code=4000,
        )
    extraction.parsed = parsed
    db.commit()
    db.refresh(extraction)

    entity_override_service.mark_dirty(db, book_id, "character")
    entity_override_service.mark_dirty(db, book_id, "lore")
    return extraction_to_dict(
        extraction, include_parsed=True, include_raw=False
    )


def update_summary(
    db: Session,
    book_id: int,
    chapter_idx: int,
    payload: SummaryUpdate,
) -> dict[str, Any]:
    _book_or_404(db, book_id)
    _main_chapter(db, book_id, chapter_idx)
    summary = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == book_id,
            ChapterSummary.chapter_idx == chapter_idx,
        )
        .first()
    )
    if not summary:
        raise BusinessException(message="该章还没有摘要", code=4000)

    data = payload.model_dump(exclude_unset=True)
    current = {
        "summary": summary.summary,
        "key_events": summary.key_events or [],
        "characters": summary.characters or [],
        "cliffhanger": summary.cliffhanger or "",
    }
    current.update({key: value for key, value in data.items() if key in current})
    try:
        cleaned = prompt.validate_edit_fields(current)
    except prompt.ExtractionValidationError as exc:
        raise BusinessException(message=str(exc), code=4000) from exc

    summary.summary = cleaned["summary"]
    summary.key_events = cleaned["key_events"]
    summary.characters = cleaned["characters"]
    summary.cliffhanger = cleaned["cliffhanger"]
    summary.edited = True
    db.commit()
    return get_summary_detail(db, book_id, chapter_idx)
