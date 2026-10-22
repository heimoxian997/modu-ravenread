"""伴读 agent 的工具注册与执行（章级闸门，工具面 11 个）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_summary import ChapterSummary
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_alias_observation import CharacterAliasObservation
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.character_mention import CharacterMention
from app.services import (
    agent_context_service,
    entity_index_service,
    entity_layer_service,
    entity_override_service,
    summary_index_service,
    text_index_service,
    text_window_service,
)

MAX_RADIUS = 2
MAX_READ_SEGMENTS = 3
MAX_SEGMENT_CHARS = 600
MAX_READ_CHAPTER_CHARS = 1200
MAX_ALIAS_SHOWN = 3
MAX_ALIAS_LIST_LIMIT = 50
MAX_TIMELINE_ITEMS = 20
ALIAS_FIELDS = (
    "display_name",
    "identity",
    "hits_this_chapter",
    "last_seen_chapter",
    "occurrence_count",
)

SPECS: list[dict[str, Any]] = [
    {
        "name": "read_around",
        "description": (
            "读当前章光标附近的原文，用于消歧「这段里的她/他/那人是谁」。"
            "anchor 可选，radius 0-2。"
        ),
        "args": {
            "anchor": "string，可选，读者看到的词",
            "radius": "integer 0-2，默认 1",
        },
    },
    {
        "name": "read_chapter",
        "description": (
            "读某一章的正文。按章号精确访问，不检索；单次最多 1200 字，"
            "用 next_offset 继续往后读。只允许 0–{max} 章。"
        ),
        "args": {
            "chapter_idx": "integer，必填，0–{max}",
            "offset": "integer，可选，章内字符偏移，默认 0",
        },
    },
    {
        "name": "search_text",
        "description": (
            "在正文里找原句/找场景，返回命中的父块全文，固定 2 条、每条约 1500 字。"
            "当前章精读用 read_around，其他章用 read_chapter。"
        ),
        "args": {"query": "string，检索词或短句"},
    },
    {
        "name": "search_summaries",
        "description": "在章节摘要里做语义检索，固定返回 3 条。",
        "args": {"query": "string，人物/事件/关键词"},
    },
    {
        "name": "get_chapter_summary",
        "description": (
            "精确取某一章的摘要、关键事件、章末钩子；不走向量。"
            "只允许 0–{max} 章。"
        ),
        "args": {"chapter_idx": "integer，必填，0–{max}"},
    },
    {
        "name": "get_character",
        "description": (
            "取一个人物在某一章时点的卡：身份、性格、关系、已知事实，"
            "每条带 first_seen_chapter / last_seen_chapter。知道本名直接传 name。"
        ),
        "args": {
            "name": "string，人物称呼或规范名",
            "as_of_chapter": "integer，可选，省略则为读者当前章；只允许 0–{max}",
        },
    },
    {
        "name": "get_character_timeline",
        "description": (
            "按章看一个人物的变化；用 from_chapter / to_chapter 翻页，"
            "不要按条数翻。"
        ),
        "args": {
            "name": "string，人物称呼或规范名",
            "from_chapter": "integer，可选，默认 0；0–{max}",
            "to_chapter": "integer，可选，默认读者当前章；0–{max}",
            "keyword": "string，可选，逗号分隔",
            "limit": "integer，可选，1-20，默认 10",
        },
    },
    {
        "name": "search_character_changes",
        "description": "按事件语义检索人物变化，返回具体章和 delta。",
        "args": {"query": "string，事件/关系/性格变化"},
    },
    {
        "name": "resolve_alias",
        "description": (
            "把一个称呼解析成候选人物：别称、代称、昵称、外号都走这里；"
            "传规范名也能命中。只覆盖人物层，不含世界观和正文；"
            "默认只给最可能的 3 个，total > shown 时可调 list_alias_candidates。"
        ),
        "args": {
            "alias": "string，读者看到的称呼",
            "as_of_chapter": "integer，可选，省略则为读者当前章；0–{max}",
        },
    },
    {
        "name": "list_alias_candidates",
        "description": (
            "列出用这个称呼的全部候选人物（resolve_alias 只给前 3 个），"
            "用于候选不够或需要翻页时。"
        ),
        "args": {
            "alias": "string，读者看到的称呼",
            "as_of_chapter": "integer，可选，省略则为读者当前章；0–{max}",
            "offset": "integer，可选，默认 0",
            "limit": "integer，可选，1-50，默认 10",
        },
    },
    {
        "name": "search_lore",
        "description": "语义检索世界观：地点、门派、物品、设定，固定返回 3 条。",
        "args": {"query": "string，地名/物品/设定"},
    },
]


class ToolError(Exception):
    """可结构化返回给模型的工具错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        data: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data or {"message": message}


class _Ambiguous(Exception):
    def __init__(self, candidates: list[dict[str, Any]]):
        super().__init__("称呼有歧义")
        self.candidates = candidates


@dataclass
class ToolContext:
    db: Session
    book: Book
    chapter: Chapter
    safe_chapter: int
    char_offset: int
    max_processed: int | None = None
    disabled_tools: set[str] | None = None


def specs(
    max_chapter: int | None = None,
    available: set[str] | None = None,
) -> list[dict[str, Any]]:
    """工具清单；max_chapter 用于把描述里的 0–N 写成具体数字。"""
    limit = str(int(max_chapter)) if max_chapter is not None else "N"
    result = []
    for item in SPECS:
        if available is not None and item["name"] not in available:
            continue
        result.append(
            {
                "name": item["name"],
                "description": item["description"].replace("{max}", limit),
                "args": {
                    key: str(value).replace("{max}", limit)
                    for key, value in item["args"].items()
                },
            }
        )
    return result


def _max_processed(ctx: ToolContext) -> int:
    if ctx.max_processed is None:
        ctx.max_processed = entity_layer_service._max_processed_chapter(
            ctx.db, ctx.book.id
        )
    return int(ctx.max_processed)


def _int_arg(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ToolError("invalid_arguments", f"{name} 必须是整数")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ToolError(
            "invalid_arguments", f"{name} 必须是整数", {"parameter": name}
        ) from exc


def _check_chapter(value: int, allowed: int, name: str) -> int:
    if value < 0 or value > allowed:
        raise ToolError(
            "chapter_out_of_range",
            f"章节超出读者当前进度：可查询 0–{allowed}。",
            {
                "parameter": name,
                "requested_chapter": value,
                "allowed_max": allowed,
                "message": f"章节超出读者当前进度：可查询 0–{allowed}。",
            },
        )
    return value


def _prepare_chapters(
    ctx: ToolContext, tool: str, args: dict[str, Any]
) -> dict[str, Any]:
    """集中校验所有章号参数：越界拒绝，未抽取降级。"""
    prepared = dict(args)
    current = int(ctx.chapter.idx)
    max_processed = max(0, _max_processed(ctx))

    if tool in {"read_chapter", "get_chapter_summary"}:
        if (
            "chapter_idx" not in prepared
            or prepared.get("chapter_idx") in (None, "")
        ):
            raise ToolError(
                "invalid_arguments",
                "缺少 chapter_idx",
                {"parameter": "chapter_idx"},
            )
        requested = _int_arg(prepared.get("chapter_idx"), "chapter_idx")
        _check_chapter(requested, current, "chapter_idx")
        prepared["_chapter_idx"] = requested
        prepared["_requested_chapter"] = requested
        return prepared

    if tool in {"get_character", "resolve_alias", "list_alias_candidates"}:
        raw = prepared.get("as_of_chapter")
        requested = (
            current if raw in (None, "") else _int_arg(raw, "as_of_chapter")
        )
        _check_chapter(requested, current, "as_of_chapter")
        actual = min(requested, max_processed)
        prepared["_as_of_chapter"] = actual
        prepared["_requested_as_of"] = requested
        prepared["_capped"] = actual < requested
        return prepared

    if tool == "get_character_timeline":
        raw_from = prepared.get("from_chapter")
        raw_to = prepared.get("to_chapter")
        requested_from = (
            0 if raw_from in (None, "") else _int_arg(raw_from, "from_chapter")
        )
        requested_to = (
            current
            if raw_to in (None, "")
            else _int_arg(raw_to, "to_chapter")
        )
        _check_chapter(requested_from, current, "from_chapter")
        _check_chapter(requested_to, current, "to_chapter")
        actual_to = min(requested_to, max_processed)
        prepared["_from_chapter"] = requested_from
        prepared["_to_chapter"] = actual_to
        prepared["_requested_from"] = requested_from
        prepared["_requested_to"] = requested_to
        prepared["_capped"] = actual_to < requested_to
        prepared["_as_of_chapter"] = current
        prepared["_requested_as_of"] = current
        return prepared

    return prepared


def _clip(text: Any, limit: int) -> str:
    value = str(text or "").strip()
    return value[:limit]


def _display_names(
    db: Session, book_id: int, character_ids: list[int], safe: int
) -> dict[int, str]:
    if not character_ids:
        return {}
    rows = (
        db.query(CharacterAlias)
        .filter(
            CharacterAlias.book_id == book_id,
            CharacterAlias.character_id.in_(character_ids),
            or_(
                CharacterAlias.first_seen_chapter.is_(None),
                CharacterAlias.first_seen_chapter <= int(safe),
            ),
        )
        .order_by(
            CharacterAlias.character_id.asc(),
            CharacterAlias.occurrence_count.desc(),
            CharacterAlias.alias.asc(),
        )
        .all()
    )
    names: dict[int, str] = {}
    for row in rows:
        names.setdefault(int(row.character_id), row.alias)
    return names


def _display_name(
    db: Session, book_id: int, character: Character, safe: int
) -> str:
    names = _display_names(db, book_id, [character.id], safe)
    return names.get(character.id) or character.canonical_name


def _identity_map(
    ctx: ToolContext, character_ids: list[int], as_of: int
) -> dict[int, str]:
    if not character_ids:
        return {}
    rows = (
        ctx.db.query(CharacterChapterDelta)
        .filter(
            CharacterChapterDelta.book_id == ctx.book.id,
            CharacterChapterDelta.character_id.in_(character_ids),
            CharacterChapterDelta.chapter_idx <= int(as_of),
            CharacterChapterDelta.identity.isnot(None),
            CharacterChapterDelta.identity != "",
        )
        .order_by(
            CharacterChapterDelta.character_id.asc(),
            CharacterChapterDelta.chapter_idx.desc(),
        )
        .all()
    )
    identities: dict[int, str] = {}
    for row in rows:
        identities.setdefault(int(row.character_id), row.identity)
    return identities


def _alias_candidates(
    ctx: ToolContext, alias: str, as_of: int
) -> tuple[list[dict[str, Any]], str | None]:
    """把观测候选和规范名候选合并；内部可带 character_id，出口会剥掉。"""
    data = entity_layer_service.get_alias_candidates(
        ctx.db,
        ctx.book.id,
        alias,
        chapter_idx=as_of,
        limit=0,
    )
    items: dict[int, dict[str, Any]] = {}
    for item in data.get("candidates") or []:
        character_id = int(item["character_id"])
        items[character_id] = {
            "character_id": character_id,
            "display_name": item.get("display_name") or alias,
            "first_seen_chapter": item.get("first_seen_chapter"),
            "last_seen_chapter": item.get("last_seen_chapter"),
            "hits_this_chapter": int(item.get("chapter_hits") or 0),
            "occurrence_count": int(item.get("occurrence_count") or 0),
            "chapters": list(item.get("chapters") or []),
        }

    matched_by = "alias" if items else None
    character = (
        ctx.db.query(Character)
        .filter(
            Character.book_id == ctx.book.id,
            Character.canonical_name == alias,
        )
        .first()
    )
    if character is not None and (
        character.first_seen_chapter is not None
        and int(character.first_seen_chapter) <= int(as_of)
    ):
        matched_by = "canonical_name"
        character_id = int(character.id)
        if character_id not in items:
            observations = (
                ctx.db.query(CharacterAliasObservation)
                .filter(
                    CharacterAliasObservation.book_id == ctx.book.id,
                    CharacterAliasObservation.character_id == character_id,
                    CharacterAliasObservation.alias == alias,
                    CharacterAliasObservation.chapter_idx <= int(as_of),
                )
                .all()
            )
            chapters = sorted(
                {int(row.chapter_idx) for row in observations}
            )
            hits = sum(
                int(row.count or 0)
                for row in observations
                if int(row.chapter_idx) == int(as_of)
            )
            occurrence = sum(int(row.count or 0) for row in observations)
            mention_count = (
                ctx.db.query(func.count(CharacterMention.id))
                .filter(
                    CharacterMention.book_id == ctx.book.id,
                    CharacterMention.character_id == character_id,
                    CharacterMention.chapter_idx <= int(as_of),
                )
                .scalar()
                or 0
            )
            if not chapters and character.first_seen_chapter is not None:
                chapters = [
                    int(character.first_seen_chapter)
                ] if int(character.first_seen_chapter) <= int(as_of) else []
            last_seen = max(chapters) if chapters else None
            if last_seen is None and character.last_seen_chapter is not None:
                last_seen = min(
                    int(character.last_seen_chapter), int(as_of)
                )
            items[character_id] = {
                "character_id": character_id,
                "display_name": alias,
                "first_seen_chapter": min(chapters) if chapters else None,
                "last_seen_chapter": last_seen,
                "hits_this_chapter": hits,
                "occurrence_count": max(occurrence, int(mention_count)),
                "chapters": chapters,
            }
        else:
            items[character_id]["display_name"] = alias

    identities = _identity_map(ctx, list(items), as_of)
    for character_id, item in items.items():
        item["identity"] = _clip(identities.get(character_id, ""), 20)
        item["chapters"] = sorted(set(item.get("chapters") or []))
        if item.get("first_seen_chapter") is None and item["chapters"]:
            item["first_seen_chapter"] = item["chapters"][0]
        if item.get("last_seen_chapter") is None and item["chapters"]:
            item["last_seen_chapter"] = item["chapters"][-1]

    return list(items.values()), matched_by


def _sort_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (
            -int(item.get("hits_this_chapter") or 0),
            -int(item.get("last_seen_chapter") or -1),
            -int(item.get("occurrence_count") or 0),
            str(item.get("display_name") or ""),
        ),
    )


def _ambiguity(candidates: list[dict[str, Any]]) -> bool:
    evidenced = sum(
        1
        for item in candidates
        if int(item.get("hits_this_chapter") or 0) > 0
        or int(item.get("occurrence_count") or 0) > 0
    )
    return evidenced >= 2


def _public_candidate(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "display_name": item.get("display_name") or "",
        "identity": item.get("identity") or "",
        "hits_this_chapter": int(item.get("hits_this_chapter") or 0),
        "last_seen_chapter": item.get("last_seen_chapter"),
        "occurrence_count": int(item.get("occurrence_count") or 0),
    }


def _shape_alias(
    ctx: ToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    alias = str(args.get("alias") or "").strip()
    if not alias:
        raise ToolError("invalid_arguments", "alias 不能为空")
    as_of = int(args["_as_of_chapter"])
    candidates, matched_by = _alias_candidates(ctx, alias, as_of)
    ordered = _sort_candidates(candidates)
    total = len(ordered)
    shown = min(MAX_ALIAS_SHOWN, total)
    result: dict[str, Any] = {
        "alias": alias,
        "gate_chapter": as_of,
        "requested_chapter": int(args.get("_requested_as_of") or as_of),
        "capped": bool(args.get("_capped")),
        "matched_by": matched_by,
        "found": total > 0,
        "shown": shown,
        "total": total,
        "ambiguous": _ambiguity(ordered),
        "truncated": total > shown,
        "candidates": [_public_candidate(item) for item in ordered[:shown]],
    }
    if total == 0:
        result.update(
            {
                "checked": [
                    "character.canonical_name",
                    "character_alias_observation.alias",
                ],
                "scope": "人物层，不含世界观与正文",
            }
        )
    return result


def _list_alias(
    ctx: ToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    alias = str(args.get("alias") or "").strip()
    if not alias:
        raise ToolError("invalid_arguments", "alias 不能为空")
    as_of = int(args["_as_of_chapter"])
    try:
        offset = max(0, int(args.get("offset", 0)))
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = max(1, min(MAX_ALIAS_LIST_LIMIT, int(args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    candidates, _ = _alias_candidates(ctx, alias, as_of)
    total = len(candidates)
    page = candidates[offset : offset + limit]
    items = []
    for item in page:
        chapters = list(item.get("chapters") or [])
        items.append(
            {
                "display_name": item.get("display_name") or "",
                "identity": item.get("identity") or "",
                "occurrence_count": int(item.get("occurrence_count") or 0),
                "first_seen_chapter": item.get("first_seen_chapter"),
                "last_seen_chapter": item.get("last_seen_chapter"),
                "chapters": chapters[:20],
                "chapters_truncated": len(chapters) > 20,
            }
        )
    return {
        "alias": alias,
        "gate_chapter": as_of,
        "requested_chapter": int(args.get("_requested_as_of") or as_of),
        "capped": bool(args.get("_capped")),
        "total": total,
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(items) < total,
        "items": items,
    }


def _resolve_character(
    ctx: ToolContext, args: dict[str, Any]
) -> tuple[Character, str]:
    name = str(args.get("name") or "").strip()
    if not name:
        raise ToolError("invalid_arguments", "缺少 name")
    as_of = int(args["_as_of_chapter"])
    character = (
        ctx.db.query(Character)
        .filter(
            Character.book_id == ctx.book.id,
            Character.canonical_name == name,
        )
        .first()
    )
    if character is not None:
        if (
            character.first_seen_chapter is None
            or int(character.first_seen_chapter) > as_of
        ):
            raise BusinessException(
                message="该人物在当前章之前尚未出现", code=4040
            )
        return character, _display_name(
            ctx.db, ctx.book.id, character, as_of
        )

    data = entity_layer_service.get_alias_candidates(
        ctx.db,
        ctx.book.id,
        name,
        chapter_idx=as_of,
        limit=0,
    )
    if data.get("ambiguous"):
        shaped = _shape_alias(ctx, {**args, "alias": name})
        raise _Ambiguous(shaped.get("candidates") or [])
    best = data.get("best")
    if not best:
        raise BusinessException(
            message="人物不存在，或在当前章之前尚未出现", code=4040
        )
    character = ctx.db.get(Character, int(best["character_id"]))
    if not character:
        raise BusinessException(message="人物不存在", code=4040)
    if (
        character.first_seen_chapter is None
        or int(character.first_seen_chapter) > as_of
    ):
        raise BusinessException(
            message="该人物在当前章之前尚未出现", code=4040
        )
    return character, best.get("display_name") or _display_name(
        ctx.db, ctx.book.id, character, as_of
    )


def _flat_card(effective: dict[str, Any], chapter_idx: int | None) -> dict[str, Any]:
    chapter = int(chapter_idx or 0)

    def items(values: Any) -> list[dict[str, Any]]:
        return [
            {
                "text": str(value),
                "first_seen_chapter": chapter,
                "last_seen_chapter": chapter,
            }
            for value in (values or [])
            if str(value).strip()
        ]

    def one(value: Any) -> dict[str, Any] | None:
        text = str(value or "").strip()
        return (
            {"text": text, "chapter_idx": chapter}
            if text
            else None
        )

    return {
        "identity": one(effective.get("identity")),
        "traits": items(effective.get("traits")),
        "relations": items(effective.get("relations")),
        "known_facts": items(effective.get("known_facts")),
        "voice_sample": one(effective.get("voice_sample")),
        "arc": one(effective.get("arc")),
    }


def _shape_character_card(
    detail: dict[str, Any],
    display_name: str,
    *,
    as_of: int,
    requested: int,
    capped: bool,
) -> dict[str, Any]:
    character = dict(detail.get("character") or {})
    character.pop("id", None)
    character.pop("canonical_name", None)
    character["display_name"] = display_name
    character["aliases"] = list(character.get("aliases") or [])[:10]

    card = detail.get("provenance") or {}
    if detail.get("edited"):
        card = _flat_card(
            detail.get("effective") or {},
            detail.get("override_from_chapter"),
        )
    return {
        "character": character,
        "as_of_chapter": as_of,
        "requested_chapter": requested,
        "capped": capped,
        "identity": card.get("identity"),
        "traits": list(card.get("traits") or []),
        "relations": list(card.get("relations") or []),
        "known_facts": list(card.get("known_facts") or []),
        "voice_sample": card.get("voice_sample"),
        "arc": card.get("arc"),
        "totals": detail.get("totals") or {},
        "capped_by_limit": detail.get("capped_by_limit") or {},
        "latest_snapshot": {
            "edited": bool(detail.get("edited")),
            "override_from_chapter": detail.get("override_from_chapter"),
        },
        "edited": bool(detail.get("edited")),
        "override_from_chapter": detail.get("override_from_chapter"),
    }


def _get_character(
    ctx: ToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    character, display = _resolve_character(ctx, args)
    as_of = int(args["_as_of_chapter"])
    detail = entity_override_service.get_character_detail(
        ctx.db, ctx.book.id, character.id, as_of
    )
    return _shape_character_card(
        detail,
        display,
        as_of=as_of,
        requested=int(args.get("_requested_as_of") or as_of),
        capped=bool(args.get("_capped")),
    )


def _get_character_timeline(
    ctx: ToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    character, display = _resolve_character(ctx, args)
    try:
        limit = max(1, min(MAX_TIMELINE_ITEMS, int(args.get("limit", 10))))
    except (TypeError, ValueError):
        limit = 10
    keyword = str(args.get("keyword") or "").strip()[:100] or None
    from_chapter = int(args["_from_chapter"])
    to_chapter = int(args["_to_chapter"])
    data = entity_layer_service.get_character_timeline(
        ctx.db,
        ctx.book.id,
        character.id,
        int(ctx.chapter.idx),
        from_chapter=from_chapter,
        to_chapter=to_chapter,
        keyword=keyword,
        limit=limit,
        offset=0,
    )
    data["character"] = {"display_name": display}
    data["requested_from_chapter"] = int(
        args.get("_requested_from") or from_chapter
    )
    data["requested_to_chapter"] = int(
        args.get("_requested_to") or to_chapter
    )
    data["capped"] = bool(args.get("_capped"))
    items = data.get("items") or []
    next_from = None
    if data.get("truncated") and items:
        candidate = int(items[-1]["chapter_idx"]) + 1
        if candidate <= int(data.get("to_chapter") or to_chapter):
            next_from = candidate
    data["next_from_chapter"] = next_from
    return data


def _read_around(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    anchor = str(args.get("anchor") or "").strip()[:64]
    try:
        radius = int(args.get("radius", 1))
    except (TypeError, ValueError):
        radius = 1
    radius = max(0, min(MAX_RADIUS, radius))
    data = text_window_service.read_around(
        ctx.db,
        ctx.book.id,
        ctx.chapter.idx,
        anchor=anchor or None,
        char_offset=ctx.char_offset,
        radius=radius,
    )
    segments = []
    total = 0
    for segment in data.get("segments") or []:
        if len(segments) >= MAX_READ_SEGMENTS or total >= 4000:
            break
        text = str(segment.get("text") or "")
        if len(text) > MAX_SEGMENT_CHARS:
            text = text[:MAX_SEGMENT_CHARS]
        if total + len(text) > 4000:
            text = text[: max(0, 4000 - total)]
        total += len(text)
        segments.append(
            {
                "seq": segment.get("seq"),
                "char_start": segment.get("char_start"),
                "char_end": segment.get("char_end"),
                "text": text,
                "anchors": [
                    {
                        "pos": item.get("pos"),
                        "absolute": item.get("absolute"),
                        "names_nearby": [
                            {
                                "name": near.get("name"),
                                "distance": near.get("distance"),
                            }
                            for near in item.get("names_nearby") or []
                            if near.get("name")
                        ],
                    }
                    for item in segment.get("anchors") or []
                ],
                "names": [
                    {
                        "name": item.get("name"),
                        "count": int(item.get("count") or 0),
                    }
                    for item in segment.get("names") or []
                    if item.get("name")
                ],
            }
        )
    return {
        "chapter_idx": data.get("chapter_idx"),
        "chapter_title": data.get("chapter_title"),
        "mode": data.get("mode"),
        "anchor": data.get("anchor"),
        "char_offset": data.get("char_offset"),
        "segments": segments,
        "truncated": bool(data.get("truncated")) or len(segments) < len(
            data.get("segments") or []
        ),
    }


def _read_chapter(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    chapter_idx = int(args["_chapter_idx"])
    chapter = agent_context_service.get_chapter_or_404(
        ctx.db, ctx.book.id, chapter_idx
    )
    try:
        offset = max(0, int(args.get("offset", 0)))
    except (TypeError, ValueError):
        raise ToolError("invalid_arguments", "offset 必须是整数")
    content = text_window_service.get_chapter_content(
        ctx.db, ctx.book.id, chapter_idx
    )
    paragraphs = content.get("paragraphs") or []
    start_global = int(chapter.char_start) + offset
    picked: list[dict[str, Any]] = []
    total = 0
    next_offset = None
    for item in paragraphs:
        item_start = int(item.get("offset") or 0)
        text = str(item.get("text") or "")
        item_end = item_start + len(text)
        if item_end <= start_global:
            continue
        if item_start < start_global:
            text = text[start_global - item_start :]
            item_start = start_global
        if not text:
            continue
        remain = MAX_READ_CHAPTER_CHARS - total
        if remain <= 0:
            next_offset = item_start - int(chapter.char_start)
            break
        if len(text) > remain:
            picked.append({"offset": item_start, "text": text[:remain]})
            total += remain
            next_offset = (
                item_start - int(chapter.char_start) + remain
            )
            break
        picked.append({"offset": item_start, "text": text})
        total += len(text)
    text = "\n".join(item["text"] for item in picked)
    has_more = bool(next_offset is not None and next_offset < (
        int(chapter.char_end) - int(chapter.char_start)
    ))
    return {
        "chapter_idx": chapter.idx,
        "chapter_title": chapter.title,
        "char_start": picked[0]["offset"] if picked else start_global,
        "char_end": (
            picked[-1]["offset"] + len(picked[-1]["text"])
            if picked
            else start_global
        ),
        "text": text,
        "next_offset": next_offset,
        "has_more": has_more,
        "chapter_offset": offset,
    }


def _get_chapter_summary(
    ctx: ToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    chapter_idx = int(args["_chapter_idx"])
    row = (
        ctx.db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == ctx.book.id,
            ChapterSummary.chapter_idx == chapter_idx,
        )
        .first()
    )
    if not row:
        return {
            "found": False,
            "chapter_idx": chapter_idx,
            "checked": ["chapter_summaries"],
        }
    return {
        "found": True,
        "chapter_idx": row.chapter_idx,
        "title": row.title,
        "summary": row.summary,
        "key_events": list(row.key_events or []),
        "characters": list(row.characters or []),
        "cliffhanger": row.cliffhanger or "",
    }


def _search_text(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    hits = text_index_service.search_text_index(
        ctx.db,
        ctx.book.id,
        query,
        top_k=20,
        recall=20,
        chapter_idx=int(ctx.chapter.idx),
    )
    seen: set[Any] = set()
    items = []
    for hit in hits:
        parent_id = hit.get("parent_id")
        key = ("parent", parent_id) if parent_id is not None else (
            "chunk",
            hit.get("chunk_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        child_text = str(hit.get("text") or "")
        parent_text = str(hit.get("parent_text") or child_text)
        items.append(
            {
                "chapter_idx": hit.get("chapter_idx"),
                "chapter_title": hit.get("chapter_title") or "",
                "parent_id": parent_id
                if parent_id is not None
                else hit.get("chunk_id"),
                "char_start": int(
                    hit.get("parent_char_start") or hit.get("char_start") or 0
                ),
                "char_end": int(
                    hit.get("parent_char_end") or hit.get("char_end") or 0
                ),
                "text": parent_text,
                "hit_char_start": int(hit.get("char_start") or 0),
                "hit_char_end": int(hit.get("char_end") or 0),
                "hit_text": child_text[:MAX_SEGMENT_CHARS],
                "score": hit.get("rrf_score"),
            }
        )
        if len(items) >= 2:
            break
    return {"query": query, "items": items, "count": len(items)}


def _search_summaries(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    hits = summary_index_service.search_summary_index(
        ctx.db,
        ctx.book.id,
        query,
        top_k=3,
        chapter_idx=int(ctx.chapter.idx),
    )
    items = []
    for hit in hits[:3]:
        items.append(
            {
                "chapter_idx": hit.get("chapter_idx"),
                "chapter_title": hit.get("chapter_title") or "",
                "summary": str(hit.get("summary") or "")[:800],
                "key_events": list(hit.get("key_events") or [])[:5],
                "characters": list(hit.get("characters") or [])[:8],
                "cliffhanger": str(hit.get("cliffhanger") or "")[:300],
                "score": hit.get("rrf_score"),
            }
        )
    return {"query": query, "items": items, "count": len(items)}


def _search_lore(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    hits = entity_index_service.search_lore_index(
        ctx.db,
        ctx.book.id,
        query,
        top_k=3,
        chapter_idx=int(ctx.chapter.idx),
    )
    items = [
        {
            "lore_id": hit.get("lore_id"),
            "name": hit.get("canonical_name"),
            "category": hit.get("category"),
            "chapter_idx": hit.get("chapter_idx"),
            "summary": str(hit.get("summary") or "")[:600],
            "aliases": list(hit.get("aliases") or [])[:8],
            "score": hit.get("rrf_score"),
        }
        for hit in hits[:3]
    ]
    return {"query": query, "items": items, "count": len(items)}


def _search_character_changes(
    ctx: ToolContext, args: dict[str, Any]
) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    items = entity_index_service.search_character_changes(
        ctx.db,
        ctx.book.id,
        query,
        chapter_idx=int(ctx.chapter.idx),
        top_k=3,
        unique_characters=False,
    )
    ids = [int(item["character_id"]) for item in items]
    names = _display_names(ctx.db, ctx.book.id, ids, int(ctx.chapter.idx))
    for item, character_id in zip(items, ids):
        item.pop("character_id", None)
        item["character"] = names.get(
            character_id, item.get("character") or ""
        )
    return {"query": query, "items": items, "count": len(items)}


def execute(
    tool: str, args: dict[str, Any] | None, ctx: ToolContext
) -> dict[str, Any]:
    """执行一个工具；业务错误转成 ok=false，不抛到 API。"""
    name = str(tool or "").strip()
    known = {item["name"] for item in SPECS}
    if name not in known:
        return {"ok": False, "error": "unknown_tool", "data": {"message": f"未知工具：{name}"}}
    if ctx.disabled_tools and name in ctx.disabled_tools:
        return {
            "ok": False,
            "error": "tool_unavailable",
            "data": {"message": "该工具依赖的索引或数据尚未就绪。"},
        }
    payload = args if isinstance(args, dict) else {}
    try:
        prepared = _prepare_chapters(ctx, name, payload)
        if name == "read_around":
            return {"ok": True, "data": _read_around(ctx, prepared)}
        if name == "read_chapter":
            return {"ok": True, "data": _read_chapter(ctx, prepared)}
        if name == "search_text":
            return {"ok": True, "data": _search_text(ctx, prepared)}
        if name == "search_summaries":
            return {"ok": True, "data": _search_summaries(ctx, prepared)}
        if name == "get_chapter_summary":
            return {"ok": True, "data": _get_chapter_summary(ctx, prepared)}
        if name == "get_character":
            return {"ok": True, "data": _get_character(ctx, prepared)}
        if name == "get_character_timeline":
            return {
                "ok": True,
                "data": _get_character_timeline(ctx, prepared),
            }
        if name == "search_character_changes":
            return {
                "ok": True,
                "data": _search_character_changes(ctx, prepared),
            }
        if name == "resolve_alias":
            return {"ok": True, "data": _shape_alias(ctx, prepared)}
        if name == "list_alias_candidates":
            return {"ok": True, "data": _list_alias(ctx, prepared)}
        if name == "search_lore":
            return {"ok": True, "data": _search_lore(ctx, prepared)}
        return {"ok": False, "error": "unknown_tool", "data": {"message": f"未知工具：{name}"}}
    except ToolError as exc:
        return {"ok": False, "error": exc.code, "data": exc.data}
    except _Ambiguous as exc:
        return {
            "ok": False,
            "error": "ambiguous",
            "data": {"candidates": exc.candidates},
        }
    except BusinessException as exc:
        return {
            "ok": False,
            "error": "tool_error",
            "data": {"message": exc.message},
        }
    except Exception as exc:  # noqa: BLE001 - 工具失败不能拖垮 agent
        return {
            "ok": False,
            "error": "tool_error",
            "data": {"message": f"工具执行失败：{exc}"},
        }


def summary(tool: str, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        data = result.get("data") or {}
        return str(data.get("message") or result.get("error") or "失败")
    data = result.get("data") or {}
    if tool == "read_around":
        return f"第{(data.get('chapter_idx') or 0) + 1}章 {len(data.get('segments') or [])} 段"
    if tool == "read_chapter":
        return f"第{(data.get('chapter_idx') or 0) + 1}章 {len(data.get('text') or '')} 字"
    if tool == "search_text":
        return f"正文 {data.get('count', 0)} 块"
    if tool == "search_summaries":
        return f"摘要 {data.get('count', 0)} 条"
    if tool == "get_chapter_summary":
        return f"第{(data.get('chapter_idx') or 0) + 1}章" + (
            "" if data.get("found") else "（无摘要）"
        )
    if tool == "get_character":
        character = data.get("character") or {}
        return (
            f"{character.get('display_name') or '人物'} · "
            f"截至第{(data.get('as_of_chapter') or 0) + 1}章"
        )
    if tool == "get_character_timeline":
        return f"时间线 {len(data.get('items') or [])}/{data.get('total', 0)} 条"
    if tool == "search_character_changes":
        return f"变化 {data.get('count', 0)} 条"
    if tool == "resolve_alias":
        return f"候选 {data.get('shown', 0)}/{data.get('total', 0)}"
    if tool == "list_alias_candidates":
        return f"候选 {data.get('offset', 0)}-{data.get('offset', 0) + len(data.get('items') or [])}/{data.get('total', 0)}"
    if tool == "search_lore":
        return f"世界观 {data.get('count', 0)} 条"
    return "完成"
