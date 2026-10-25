"""阅读笔记的写入与查询。

两条硬规则：
1. chapter_idx 不接受调用方手填，一律由引文偏移反查得到——它是剧透闸门的键；
2. 查询侧在 SQL 层强制 chapter_idx <= safe_chapter AND spoiler = 0，
   不依赖调用方自觉。
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from app.models.book import Book
from app.models.chapter import Chapter
from app.models.note import Note
from app.models.reader_progress import ReaderProgress
from app.services.chunk_service import strip_title_prefix
from app.services.text_window_service import source_text

NOTE_TYPES = ("event", "relation", "address", "fact")
QUOTE_MIN_CHARS = 4
QUOTE_MAX_CHARS = 120
SUBJECT_KEY_MAX_CHARS = 120
MAX_QUERY_LIMIT = 50
DEFAULT_QUERY_LIMIT = 20
DEAD_STATUSES = ("superseded", "rejected")
RELATION_LABELS = (
    "亲近",
    "利用",
    "敌对",
    "决裂",
    "主仆",
    "合作",
    "亲属",
    "暧昧",
    "单恋",
    "仇恨",
)
FACT_PREDICATES = (
    "身份",
    "外貌",
    "性格",
    "能力",
    "关系",
    "持有物",
    "位置",
    "状态",
    "目标",
    "过往",
    "秘密",
)
# payload 各类型的必填字段与取值约束（附录 B）
PAYLOAD_RULES: dict[str, dict[str, Any]] = {
    "event": {
        "required": ("action", "participants"),
        # detail 是可选补充：有价值，但不能当成引文撑得住的证据，只限长
        "str_max": {"action": 30, "detail": 200},
        "list_range": {"participants": (1, 6)},
    },
    "relation": {
        "required": ("a", "b", "label", "state_at_chapter"),
        "enum": {"label": RELATION_LABELS},
    },
    "address": {"required": ("speaker", "target", "address")},
    "fact": {
        "required": ("predicate", "value"),
        "enum": {"predicate": FACT_PREDICATES},
    },
}


class NoteError(Exception):
    """可结构化返回给调用方的笔记错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        data: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        # message 永远在场：data 只带 parameter/allowed 等补充信息
        self.data = {"message": message, **(data or {})}


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, int(book_id))
    if not book:
        raise NoteError("book_not_found", "书籍不存在或已被删除")
    return book


def safe_chapter(db: Session, book_id: int) -> int:
    """safe_chapter 来自 reader_progress；没读过这本书就没有闸门可言。"""
    row = (
        db.query(ReaderProgress)
        .filter(ReaderProgress.book_id == int(book_id))
        .first()
    )
    if not row:
        raise NoteError(
            "no_reading_context",
            "这本书还没有阅读进度，请先在阅读器打开它。",
        )
    return max(0, int(row.chapter_idx))


# ---------------------------------------------------------------------------
# 引文定位：直接 find → 归一化带偏移映射再 find → 拒收
# ---------------------------------------------------------------------------

def _fold_char(ch: str) -> str:
    """全角 ASCII 与全角空格转半角，其余原样。"""
    code = ord(ch)
    if 0xFF01 <= code <= 0xFF5E:
        return chr(code - 0xFEE0)
    if ch == "\u3000":
        return " "
    return ch


_ELLIPSIS_CHARS = "…"
_DOT_RUN_CHARS = ".．。·"


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """归一化全文并保留每个输出字符对应的原始偏移。

    只做三类变换：全角转半角、省略号归一（……/…/。。./.... 统一成 …）、
    连续空白压成一个空格。变换不跨类混合，保证映射单调且无跳空。
    """
    out: list[str] = []
    offsets: list[int] = []
    length = len(text)
    i = 0
    while i < length:
        ch = _fold_char(text[i])
        if ch in _ELLIPSIS_CHARS:
            j = i
            while j < length and _fold_char(text[j]) in _ELLIPSIS_CHARS:
                j += 1
            out.append("…")
            offsets.append(i)
            i = j
            continue
        if ch in _DOT_RUN_CHARS:
            j = i
            while j < length and _fold_char(text[j]) in _DOT_RUN_CHARS:
                j += 1
            if j - i >= 2:
                out.append("…")
            else:
                out.append(ch)
            offsets.append(i)
            i = j
            continue
        if ch.isspace():
            j = i
            while j < length and _fold_char(text[j]).isspace():
                j += 1
            out.append(" ")
            offsets.append(i)
            i = j
            continue
        out.append(ch)
        offsets.append(i)
        i += 1
    return "".join(out), offsets


def locate(text: str, quote: str) -> tuple[int, int] | None:
    """在原文里定位引文，返回原始码点区间；找不到返回 None。

    第一遍直接 find；失败后对两边做同一套归一化（全半角/省略号/连续空白）
    再找，命中区间映射回原始偏移。宁可拒收，不可错配。
    """
    if not quote:
        return None
    pos = text.find(quote)
    if pos >= 0:
        return pos, pos + len(quote)

    norm_text, offsets = _normalize_with_map(text)
    norm_quote, _ = _normalize_with_map(quote)
    if not norm_quote.strip():
        return None
    hit = norm_text.find(norm_quote)
    if hit < 0:
        return None
    start = offsets[hit]
    end_index = hit + len(norm_quote)
    end = offsets[end_index] if end_index < len(offsets) else len(text)
    return start, end


# ---------------------------------------------------------------------------
# 章号反查：单章、非标题行
# ---------------------------------------------------------------------------

def _chapter_at(
    db: Session, book_id: int, text: str, start: int, end: int
) -> Chapter:
    """把引文区间反查成章。跨章或落在标题行都按找不到处理。"""
    chapter = (
        db.query(Chapter)
        .filter(
            Chapter.book_id == int(book_id),
            Chapter.char_start <= start,
            Chapter.char_end > start,
        )
        .first()
    )
    if not chapter:
        raise NoteError(
            "quote_not_found",
            "引文定位失败：偏移不在任何章节范围内。",
        )
    if end > int(chapter.char_end):
        raise NoteError(
            "quote_not_found",
            "引文定位失败：引文跨章，必须落在单章内。",
        )
    raw = text[int(chapter.char_start) : int(chapter.char_end)]
    _, prefix = strip_title_prefix(raw, chapter.title)
    if start - int(chapter.char_start) < prefix:
        raise NoteError(
            "quote_not_found",
            "引文定位失败：引文落在章节标题行上。",
        )
    return chapter


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _validate_args(
    note_type: Any,
    subject_key: Any,
    quote: Any,
    payload: Any,
    confidence: Any,
) -> tuple[str, str, str, dict[str, Any], float]:
    if note_type not in NOTE_TYPES:
        raise NoteError(
            "invalid_arguments",
            f"type 必须是 {'/'.join(NOTE_TYPES)} 之一",
            {"parameter": "type", "allowed": list(NOTE_TYPES)},
        )
    subject = str(subject_key or "").strip()
    if not subject or len(subject) > SUBJECT_KEY_MAX_CHARS:
        raise NoteError(
            "invalid_arguments",
            f"subject_key 不能为空且不超过 {SUBJECT_KEY_MAX_CHARS} 字",
            {"parameter": "subject_key"},
        )
    # 写库前规范化，A|B 与 B|A 归一成同一条，避免同证据双笔记。
    subject = _normalize_subject(subject)
    if not all(subject.replace(">", "|").split("|")):
        raise NoteError(
            "invalid_arguments",
            "subject_key 含空成员",
            {"parameter": "subject_key"},
        )
    text = str(quote or "").strip()
    if not (QUOTE_MIN_CHARS <= len(text) <= QUOTE_MAX_CHARS):
        raise NoteError(
            "invalid_arguments",
            f"quote 长度须在 {QUOTE_MIN_CHARS}-{QUOTE_MAX_CHARS} 字之间",
            {"parameter": "quote"},
        )
    if not isinstance(payload, dict) or not payload:
        raise NoteError(
            "invalid_arguments",
            "payload 必须是非空对象",
            {"parameter": "payload"},
        )
    rules = PAYLOAD_RULES[note_type]
    for field in rules["required"]:
        if field not in payload or payload[field] in (None, ""):
            raise NoteError(
                "invalid_arguments",
                f"{note_type} 的 payload 缺少必填字段 {field}",
                {"parameter": f"payload.{field}"},
            )
    for field, allowed in (rules.get("enum") or {}).items():
        if payload[field] not in allowed:
            raise NoteError(
                "invalid_arguments",
                f"payload.{field} 的取值不在允许范围内",
                {"parameter": f"payload.{field}", "allowed": list(allowed)},
            )
    for field, limit in (rules.get("str_max") or {}).items():
        value = payload.get(field)
        if value in (None, ""):
            # 可选字段（如 detail）缺省不校验；必填字段已在上面查过
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise NoteError(
                "invalid_arguments",
                f"payload.{field} 必须是不超过 {limit} 字的非空字符串",
                {"parameter": f"payload.{field}"},
            )
    for field, (low, high) in (rules.get("list_range") or {}).items():
        value = payload[field]
        if not isinstance(value, list) or not (low <= len(value) <= high):
            raise NoteError(
                "invalid_arguments",
                f"payload.{field} 必须是 {low}-{high} 个元素的列表",
                {"parameter": f"payload.{field}"},
            )
    try:
        score = 0.6 if confidence is None else float(confidence)
    except (TypeError, ValueError):
        raise NoteError(
            "invalid_arguments",
            "confidence 必须是 0-1 的数",
            {"parameter": "confidence"},
        )
    if not 0.0 <= score <= 1.0:
        raise NoteError(
            "invalid_arguments",
            "confidence 必须在 0-1 之间",
            {"parameter": "confidence"},
        )
    return note_type, subject, text, payload, score


def _normalize_subject(subject: str) -> str:
    """去空格；含 | 两侧按 unicode 排序；含 > 保留方向。"""
    compact = "".join(subject.split())
    if ">" in compact:
        left, _, right = compact.partition(">")
        return f"{left}>{right}"
    members = sorted(compact.split("|"))
    return "|".join(members)


def _note_id(book_id: int, note_type: str, subject: str, quote: str) -> str:
    digest = hashlib.sha256(
        f"{int(book_id)}|{note_type}|{subject}|{quote}".encode("utf-8")
    ).hexdigest()[:12]
    return f"n_{note_type}_{digest}"


def _to_dict(row: Note) -> dict[str, Any]:
    return {
        "note_id": row.note_id,
        "book_id": row.book_id,
        "type": row.type,
        "subject_key": row.subject_key,
        "chapter_idx": row.chapter_idx,
        "char_start": row.char_start,
        "char_end": row.char_end,
        "quote": row.quote,
        "payload": row.payload_json or {},
        "confidence": row.confidence,
        "status": row.status,
        "spoiler": row.spoiler,
        "supersedes": row.supersedes,
        "session_id": row.session_id,
        "question": row.question,
        "created_by": row.created_by,
        "created_at": row.create_time.isoformat(sep=" ", timespec="seconds")
        if row.create_time
        else None,
        "updated_at": row.update_time.isoformat(sep=" ", timespec="seconds")
        if row.update_time
        else None,
    }


def upsert(
    db: Session,
    book_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """写一条笔记。chapter_idx 由引文偏移反查；超出安全章标 spoiler=1 存而不返。"""
    _book_or_404(db, book_id)
    note_type, subject, quote, payload, confidence = _validate_args(
        args.get("type"),
        args.get("subject_key"),
        args.get("quote"),
        args.get("payload"),
        args.get("confidence"),
    )

    safe = safe_chapter(db, book_id)
    text = source_text(int(book_id))
    located = locate(text, quote)
    if located is None:
        raise NoteError(
            "quote_not_found",
            "引文在原文中找不到，请核对后原样摘录。",
            {"hint": "引文必须是原文的逐字片段，且不能跨章。"},
        )
    start, end = located
    chapter = _chapter_at(db, book_id, text, start, end)
    chapter_idx = int(chapter.idx)
    spoiler = 1 if chapter_idx > safe else 0

    note_id = _note_id(book_id, note_type, subject, quote)
    row = (
        db.query(Note)
        .filter(Note.note_id == note_id)
        .first()
    )
    if row is None:
        row = Note(
            note_id=note_id,
            book_id=int(book_id),
            type=note_type,
            subject_key=subject,
            chapter_idx=chapter_idx,
            char_start=start,
            char_end=end,
            quote=quote,
            payload_json=payload,
            confidence=confidence,
            status="tentative",
            spoiler=spoiler,
            created_by="agent",
        )
        db.add(row)
    else:
        row.chapter_idx = chapter_idx
        row.char_start = start
        row.char_end = end
        row.payload_json = payload
        row.confidence = confidence
        row.spoiler = spoiler
    db.commit()
    db.refresh(row)
    return {
        "ok": True,
        "data": {
            **_to_dict(row),
            "safe_chapter": safe,
            "beyond_safe_chapter": bool(spoiler),
        },
    }


# ---------------------------------------------------------------------------
# 查询：SQL 层强制剧透闸门
# ---------------------------------------------------------------------------

def _split_members(subject_key: str) -> set[str]:
    members = set()
    for part in subject_key.replace(">", "|").split("|"):
        part = part.strip()
        if part:
            members.add(part)
    return members


def query(
    db: Session,
    book_id: int,
    args: dict[str, Any],
) -> dict[str, Any]:
    """查笔记并返回覆盖度。只返回 chapter_idx <= safe_chapter 且 spoiler=0 的行。"""
    _book_or_404(db, book_id)
    safe = safe_chapter(db, book_id)

    note_type = args.get("type")
    if note_type is not None and note_type not in NOTE_TYPES + ("correction",):
        raise NoteError(
            "invalid_arguments",
            f"type 必须是 {'/'.join(NOTE_TYPES)} 之一",
            {"parameter": "type"},
        )
    chapter_from = args.get("chapter_from")
    chapter_to = args.get("chapter_to")
    status = args.get("status")
    entity = str(args.get("entity") or "").strip()
    pair = args.get("pair")
    if pair is not None:
        if not isinstance(pair, list) or len(pair) != 2 or not all(pair):
            raise NoteError(
                "invalid_arguments",
                "pair 必须是两个实体组成的列表",
                {"parameter": "pair"},
            )
        pair = [str(item).strip() for item in pair]
    try:
        limit = int(args.get("limit") or DEFAULT_QUERY_LIMIT)
        limit = max(1, min(MAX_QUERY_LIMIT, limit))
        cursor = max(0, int(args.get("cursor") or 0))
    except (TypeError, ValueError):
        raise NoteError(
            "invalid_arguments",
            "limit / cursor 必须是整数",
            {"parameter": "limit"},
        )

    filters = [
        Note.book_id == int(book_id),
        Note.chapter_idx <= safe,
        Note.spoiler == 0,
    ]
    if note_type:
        filters.append(Note.type == note_type)
    if chapter_from is not None:
        filters.append(Note.chapter_idx >= int(chapter_from))
    if chapter_to is not None:
        filters.append(Note.chapter_idx <= int(chapter_to))
    if status:
        filters.append(Note.status == str(status))
    else:
        filters.append(Note.status.notin_(DEAD_STATUSES))

    rows = (
        db.query(Note)
        .filter(*filters)
        .order_by(Note.chapter_idx.asc(), Note.id.asc())
        .all()
    )
    if entity:
        rows = [r for r in rows if entity in _split_members(r.subject_key)]
    if pair:
        rows = [
            r
            for r in rows
            if set(pair) <= _split_members(r.subject_key)
        ]

    total = len(rows)
    page = rows[cursor : cursor + limit]
    matched = sorted({int(r.chapter_idx) for r in rows})

    # 覆盖度单独查一次：只带 book_id / 章区间 / 存活状态，
    # 不受 type / entity / pair 影响——它回答"这本书记过哪些章"，
    # 不是"本次过滤命中哪些章"（那是 matched_chapters）。
    scope_from = max(0, int(chapter_from)) if chapter_from is not None else 0
    scope_to = min(int(chapter_to), safe) if chapter_to is not None else safe
    coverage_rows = (
        db.query(Note.chapter_idx, Note.create_time, Note.update_time)
        .filter(
            Note.book_id == int(book_id),
            Note.chapter_idx <= safe,
            Note.spoiler == 0,
            Note.chapter_idx >= scope_from,
            Note.chapter_idx <= scope_to,
            Note.status.notin_(DEAD_STATUSES),
        )
        .all()
    )
    covered = sorted({int(row.chapter_idx) for row in coverage_rows})
    gaps = [
        c for c in range(scope_from, scope_to + 1) if c not in covered
    ]
    last_updated = None
    for idx, created, updated in coverage_rows:
        stamp = updated or created
        if stamp is not None:
            iso = stamp.isoformat(sep=" ", timespec="seconds")
            if last_updated is None or iso > last_updated:
                last_updated = iso

    return {
        "ok": True,
        "data": {
            "book_id": int(book_id),
            "safe_chapter": safe,
            "items": [_to_dict(r) for r in page],
            "next_cursor": (
                cursor + limit if cursor + limit < total else None
            ),
            "total": total,
            "coverage": {
                "scope": {"chapter_from": scope_from, "chapter_to": scope_to},
                "chapters_with_notes": covered,
                "gaps": gaps,
                "matched_chapters": matched,
                "note_count": len(coverage_rows),
                "last_updated": last_updated,
            },
        },
    }
