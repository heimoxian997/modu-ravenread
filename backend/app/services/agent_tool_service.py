"""伴读 agent 的工具注册与执行（章级闸门，工具面 11 个）。"""

from __future__ import annotations

import json
from bisect import bisect_right
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
    agent_capability_service,
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

# 搜索工具的条数与体积护栏：limit 是调用方要的，
# 6000 字符是系统能承受的——只放条数不设体积，一次调用就能吃掉整个上下文。
MAX_SEARCH_RETURN_CHARS = 6000
EXACT_DEFAULT_LIMIT = 8
SEARCH_TEXT_LIMIT_RANGE = (1, 8)
SEARCH_LIST_LIMIT_RANGE = (1, 10)
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
            "在正文里找原句/找场景。mode=exact 全文精确扫描，不依赖索引，"
            "返回逐条命中（前后各 40 字）；mode=hybrid 走向量索引，返回命中的"
            "父块全文（每条约 1500 字），索引缺失时自动退回精确扫描。"
            "只覆盖 0–{max} 章。"
        ),
        "args": {
            "query": "string，检索词或短句",
            "mode": "string，hybrid|exact，默认 hybrid",
            "chapter_from": "integer，可选，0–{max}",
            "chapter_to": "integer，可选，0–{max}",
            "limit": "integer，可选，1-8；hybrid 默认 2，exact 默认 8",
        },
    },
    {
        "name": "get_toc",
        "description": (
            "目录：章号、标题、类型与字符范围。只列 0–{max} 章——"
            "章标题本身就是剧透，绝不返回安全章以后的章。"
        ),
        "args": {
            "chapter_from": "integer，可选，默认 0",
            "chapter_to": "integer，可选，默认安全章，不会超过 0–{max}",
            "limit": "integer，可选，默认 200",
        },
    },
    {
        "name": "read_span",
        "description": (
            "按全局字符区间跨章读原文，单次最多 max_chars 字；" "区间自动夹在安全章内。"
        ),
        "args": {
            "char_start": "integer，必填，全局码点偏移",
            "char_end": "integer，必填，全局码点偏移",
            "max_chars": "integer，可选，默认 6000",
        },
    },
    {
        "name": "search_summaries",
        "description": "在章节摘要里做语义检索，默认 3 条，limit 最多 10 条。",
        "args": {
            "query": "string，人物/事件/关键词",
            "limit": "integer，可选，1-10，默认 3",
        },
    },
    {
        "name": "get_chapter_summary",
        "description": (
            "精确取某一章的摘要、关键事件、章末钩子；不走向量。" "只允许 0–{max} 章。"
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
            "按章看一个人物的变化；用 from_chapter / to_chapter 翻页，" "不要按条数翻。"
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
        "description": "按事件语义检索人物变化，返回具体章和 delta，默认 3 条。",
        "args": {
            "query": "string，事件/关系/性格变化",
            "limit": "integer，可选，1-10，默认 3",
        },
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
        "description": "语义检索世界观：地点、门派、物品、设定，默认 3 条，limit 最多 10 条。",
        "args": {
            "query": "string，地名/物品/设定",
            "limit": "integer，可选，1-10，默认 3",
        },
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
    capabilities: "agent_capability_service.AgentCapabilities | None" = None


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
        if "chapter_idx" not in prepared or prepared.get("chapter_idx") in (None, ""):
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
        requested = current if raw in (None, "") else _int_arg(raw, "as_of_chapter")
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
            current if raw_to in (None, "") else _int_arg(raw_to, "to_chapter")
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


def _display_name(db: Session, book_id: int, character: Character, safe: int) -> str:
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
            chapters = sorted({int(row.chapter_idx) for row in observations})
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
                chapters = (
                    [int(character.first_seen_chapter)]
                    if int(character.first_seen_chapter) <= int(as_of)
                    else []
                )
            last_seen = max(chapters) if chapters else None
            if last_seen is None and character.last_seen_chapter is not None:
                last_seen = min(int(character.last_seen_chapter), int(as_of))
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


def _shape_alias(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
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


def _list_alias(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
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


def _resolve_character(ctx: ToolContext, args: dict[str, Any]) -> tuple[Character, str]:
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
            raise BusinessException(message="该人物在当前章之前尚未出现", code=4040)
        return character, _display_name(ctx.db, ctx.book.id, character, as_of)

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
        raise BusinessException(message="人物不存在，或在当前章之前尚未出现", code=4040)
    character = ctx.db.get(Character, int(best["character_id"]))
    if not character:
        raise BusinessException(message="人物不存在", code=4040)
    if (
        character.first_seen_chapter is None
        or int(character.first_seen_chapter) > as_of
    ):
        raise BusinessException(message="该人物在当前章之前尚未出现", code=4040)
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
        return {"text": text, "chapter_idx": chapter} if text else None

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


def _get_character(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
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


def _get_character_timeline(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
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
    data["requested_from_chapter"] = int(args.get("_requested_from") or from_chapter)
    data["requested_to_chapter"] = int(args.get("_requested_to") or to_chapter)
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
    if ctx.chapter.kind != "main":
        raise ToolError("chapter_unavailable", "番外不参与伴读")
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
        "truncated": bool(data.get("truncated"))
        or len(segments) < len(data.get("segments") or []),
    }


def _read_chapter(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    chapter_idx = int(args["_chapter_idx"])
    chapter = agent_context_service.get_chapter_or_404(ctx.db, ctx.book.id, chapter_idx)
    if chapter.kind != "main":
        raise ToolError("chapter_unavailable", "番外不参与伴读")
    try:
        offset = max(0, int(args.get("offset", 0)))
    except (TypeError, ValueError):
        raise ToolError("invalid_arguments", "offset 必须是整数")
    content = text_window_service.get_chapter_content(ctx.db, ctx.book.id, chapter_idx)
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
            next_offset = item_start - int(chapter.char_start) + remain
            break
        picked.append({"offset": item_start, "text": text})
        total += len(text)
    text = "\n".join(item["text"] for item in picked)
    has_more = bool(
        next_offset is not None
        and next_offset < (int(chapter.char_end) - int(chapter.char_start))
    )
    return {
        "chapter_idx": chapter.idx,
        "chapter_title": chapter.title,
        "char_start": picked[0]["offset"] if picked else start_global,
        "char_end": (
            picked[-1]["offset"] + len(picked[-1]["text"]) if picked else start_global
        ),
        "text": text,
        "next_offset": next_offset,
        "has_more": has_more,
        "chapter_offset": offset,
    }


def _get_chapter_summary(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
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


EXACT_SNIPPET_CHARS = 40
EXACT_HITS_PER_CHAPTER = 2
DEFAULT_SPAN_CHARS = 6000


def _int_range_arg(args: dict[str, Any], name: str) -> int | None:
    raw = args.get(name)
    if raw in (None, ""):
        return None
    return _int_arg(raw, name)


def _resolve_limit(args: dict[str, Any], default: int, bounds: tuple[int, int]) -> int:
    """limit 越界一律 clamp，不报错；不传用默认值。"""
    raw = args.get("limit")
    if raw in (None, ""):
        return default
    low, high = bounds
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ToolError("invalid_arguments", "limit 必须是整数", {"parameter": "limit"})
    return max(low, min(high, value))


def _recall_top_k(limit: int) -> int:
    """内部召回深度由 limit 推，limit 越大召回池越宽。"""
    return min(200, max(20, limit * 10))


def _cap_by_chars(
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """体积护栏：加下一条会超预算就在整条边界停，返回 (保留项, 是否截断)。"""
    total = 0
    kept: list[dict[str, Any]] = []
    for item in items:
        size = len(json.dumps(item, ensure_ascii=False))
        if total + size > MAX_SEARCH_RETURN_CHARS:
            return kept, True
        total += size
        kept.append(item)
    return kept, False


def _has_text_index(ctx: ToolContext) -> bool:
    """未传能力信息时按有索引处理；明确没有才退回精确扫描。"""
    return ctx.capabilities is None or bool(ctx.capabilities.text_index)


def _search_text_exact(
    ctx: ToolContext,
    query: str,
    chapter_from: int | None,
    chapter_to: int | None,
    total_limit: int,
) -> dict[str, Any]:
    """全文精确扫描：str.find 找全部出现，按章反查，只留安全章内的。

    条数受 total_limit 管（每章至多 2 条之外的总上限），体积受 6000 字护栏管。
    """
    chapters = (
        ctx.db.query(Chapter)
        .filter(Chapter.book_id == ctx.book.id)
        .order_by(Chapter.idx.asc())
        .all()
    )
    empty = {
        "query": query,
        "mode": "exact",
        "limit": total_limit,
        "items": [],
        "count": 0,
        "truncated": False,
    }
    if not chapters:
        return empty
    safe = int(ctx.safe_chapter)
    lo = 0 if chapter_from is None else max(0, min(int(chapter_from), safe))
    hi = safe if chapter_to is None else max(0, min(int(chapter_to), safe))
    if lo > hi:
        return empty
    starts = [int(c.char_start) for c in chapters]
    ends = [int(c.char_end) for c in chapters]
    source = text_window_service.source_text(int(ctx.book.id))
    scan_end = ends[hi] if hi < len(chapters) else len(source)
    per_chapter: dict[int, int] = {}
    items: list[dict[str, Any]] = []
    limit_truncated = False
    cursor = 0
    while True:
        pos = source.find(query, cursor)
        if pos < 0 or pos >= scan_end:
            break
        if len(items) >= total_limit:
            limit_truncated = True
            break
        cursor = pos + 1
        index = bisect_right(starts, pos) - 1
        if index < 0:
            continue
        chapter_idx = int(chapters[index].idx)
        if chapter_idx < lo or chapter_idx > hi:
            continue
        if per_chapter.get(chapter_idx, 0) >= EXACT_HITS_PER_CHAPTER:
            continue
        per_chapter[chapter_idx] = per_chapter.get(chapter_idx, 0) + 1
        if chapters[index].kind != "main" or pos + len(query) > ends[index]:
            continue
        snippet_from = max(starts[index], pos - EXACT_SNIPPET_CHARS)
        snippet_to = min(ends[index], pos + len(query) + EXACT_SNIPPET_CHARS)
        items.append(
            {
                "chapter_idx": chapter_idx,
                "chapter_title": chapters[index].title,
                "char_start": pos,
                "char_end": pos + len(query),
                "snippet": source[snippet_from:snippet_to],
            }
        )
    items, char_truncated = _cap_by_chars(items)
    return {
        "query": query,
        "mode": "exact",
        "limit": total_limit,
        "items": items,
        "count": len(items),
        "truncated": limit_truncated or char_truncated,
    }


def _search_text(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    mode = str(args.get("mode") or "hybrid").strip()
    if mode not in ("hybrid", "exact"):
        raise ToolError(
            "invalid_arguments",
            "mode 必须是 hybrid 或 exact",
            {"parameter": "mode"},
        )
    chapter_from = _int_range_arg(args, "chapter_from")
    chapter_to = _int_range_arg(args, "chapter_to")
    # 不传 limit 时：hybrid 默认 2 个父块，exact 默认 8 条（片段小，多给些）。
    if mode == "exact" or not _has_text_index(ctx):
        limit = _resolve_limit(args, EXACT_DEFAULT_LIMIT, SEARCH_TEXT_LIMIT_RANGE)
        return _search_text_exact(ctx, query, chapter_from, chapter_to, limit)
    limit = _resolve_limit(args, 2, SEARCH_TEXT_LIMIT_RANGE)
    top_k = _recall_top_k(limit)
    hits = text_index_service.search_text_index(
        ctx.db,
        ctx.book.id,
        query,
        top_k=top_k,
        recall=top_k,
        chapter_idx=min(int(ctx.safe_chapter), chapter_to) if chapter_to is not None else int(ctx.safe_chapter),
        **({"chapter_from": chapter_from} if chapter_from is not None else {}),
    )
    seen: set[Any] = set()
    items = []
    limit_truncated = False
    for hit in hits:
        parent_id = hit.get("parent_id")
        key = (
            ("parent", parent_id)
            if parent_id is not None
            else (
                "chunk",
                hit.get("chunk_id"),
            )
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
                "parent_id": (
                    parent_id if parent_id is not None else hit.get("chunk_id")
                ),
                "char_start": int(
                    hit.get("parent_char_start") or hit.get("char_start") or 0
                ),
                "char_end": int(hit.get("parent_char_end") or hit.get("char_end") or 0),
                "text": parent_text,
                "hit_char_start": int(hit.get("char_start") or 0),
                "hit_char_end": int(hit.get("char_end") or 0),
                "hit_text": child_text[:MAX_SEGMENT_CHARS],
                "score": hit.get("rrf_score"),
            }
        )
        if len(items) >= limit:
            limit_truncated = True
            break
    items, char_truncated = _cap_by_chars(items)
    return {
        "query": query,
        "mode": "hybrid",
        "limit": limit,
        "items": items,
        "count": len(items),
        "truncated": limit_truncated or char_truncated,
    }


def _get_toc(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """目录。章标题即剧透：任何参数组合都不返回安全章以后的章。"""
    safe = int(ctx.safe_chapter)
    chapter_from = _int_range_arg(args, "chapter_from")
    chapter_to = _int_range_arg(args, "chapter_to")
    lo = 0 if chapter_from is None else max(0, min(int(chapter_from), safe))
    hi = safe if chapter_to is None else max(0, min(int(chapter_to), safe))
    try:
        limit = max(1, int(args.get("limit") or 200))
    except (TypeError, ValueError):
        raise ToolError("invalid_arguments", "limit 必须是整数")
    rows = []
    if lo <= hi:
        rows = (
            ctx.db.query(Chapter)
            .filter(
                Chapter.book_id == ctx.book.id,
                Chapter.idx >= lo,
                Chapter.idx <= hi,
                Chapter.kind == "main",
            )
            .order_by(Chapter.idx.asc())
            .limit(limit + 1)
            .all()
        )
    items = [
        {
            "idx": row.idx,
            "title": row.title,
            "kind": row.kind,
            "chars": int(row.char_end) - int(row.char_start),
            "char_start": int(row.char_start),
            "char_end": int(row.char_end),
        }
        for row in rows[:limit]
    ]
    next_from = int(rows[limit].idx) if len(rows) > limit else None
    return {
        "book_id": int(ctx.book.id),
        "safe_chapter": safe,
        "chapter_from": lo,
        "chapter_to": hi,
        "items": items,
        "next_from": next_from,
        "truncated": next_from is not None,
    }


def _read_span(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    char_start = _int_arg(args.get("char_start"), "char_start")
    char_end = _int_arg(args.get("char_end"), "char_end")
    try:
        max_chars = min(6000, max(1, int(args.get("max_chars") or DEFAULT_SPAN_CHARS)))
    except (TypeError, ValueError):
        raise ToolError("invalid_arguments", "max_chars 必须是整数")
    if char_start < 0 or char_end <= char_start:
        raise ToolError(
            "invalid_arguments",
            "需要 0 <= char_start < char_end",
            {"parameter": "char_start"},
        )
    source = text_window_service.source_text(int(ctx.book.id))
    if char_start >= len(source):
        raise ToolError(
            "invalid_arguments",
            "char_start 超出正文范围",
            {"parameter": "char_start"},
        )
    chapter = (
        ctx.db.query(Chapter)
        .filter(
            Chapter.book_id == ctx.book.id,
            Chapter.char_start <= char_start,
            Chapter.char_end > char_start,
        )
        .first()
    )
    if chapter is None:
        raise ToolError(
            "invalid_arguments",
            "char_start 不在任何章节范围内",
            {"parameter": "char_start"},
        )
    if int(chapter.idx) > int(ctx.safe_chapter):
        raise ToolError(
            "spoiler_blocked",
            "起点已在安全章之后，不能读取未读内容。",
            {
                "parameter": "char_start",
                "allowed_max": int(ctx.safe_chapter),
            },
        )
    safe_chapter = agent_context_service.get_chapter_or_404(
        ctx.db, ctx.book.id, int(ctx.safe_chapter)
    )
    if chapter.kind != "main":
        raise ToolError("chapter_unavailable", "番外不参与伴读")
    hard_end = min(char_end, int(safe_chapter.char_end), len(source))
    rows = ctx.db.query(Chapter).filter(Chapter.book_id==ctx.book.id, Chapter.kind=="main",
        Chapter.idx<=ctx.safe_chapter, Chapter.char_end>char_start, Chapter.char_start<hard_end).order_by(Chapter.idx).all()
    pieces, remaining, end = [], max_chars, char_start
    for row in rows:
        begin, finish = max(char_start,row.char_start), min(hard_end,row.char_end)
        take = min(remaining, finish-begin)
        if take>0:
            pieces.append(source[begin:begin+take])
            remaining -= take
            end = begin+take
        if take<finish-begin or remaining==0:
            break
    return {"char_start":char_start,"char_end":end,"text":"".join(pieces),
        "next_start":end if any(min(r.char_end,hard_end)>end for r in rows) else None}



def _search_summaries(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    limit = _resolve_limit(args, 3, SEARCH_LIST_LIMIT_RANGE)
    # 无父块去重，不过采样：top_k 直取 limit，默认调用候选池与 limit 改造前一致。
    hits = summary_index_service.search_summary_index(
        ctx.db,
        ctx.book.id,
        query,
        top_k=limit,
        chapter_idx=int(ctx.chapter.idx),
    )
    items = []
    for hit in hits[:limit]:
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
    items, char_truncated = _cap_by_chars(items)
    return {
        "query": query,
        "limit": limit,
        "items": items,
        "count": len(items),
        "truncated": len(hits) > limit or char_truncated,
    }


def _search_lore(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    limit = _resolve_limit(args, 3, SEARCH_LIST_LIMIT_RANGE)
    hits = entity_index_service.search_lore_index(
        ctx.db,
        ctx.book.id,
        query,
        top_k=limit,
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
        for hit in hits[:limit]
    ]
    items, char_truncated = _cap_by_chars(items)
    return {
        "query": query,
        "limit": limit,
        "items": items,
        "count": len(items),
        "truncated": len(hits) > limit or char_truncated,
    }


def _search_character_changes(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "").strip()
    if not query:
        raise ToolError("invalid_arguments", "query 不能为空")
    limit = _resolve_limit(args, 3, SEARCH_LIST_LIMIT_RANGE)
    found = entity_index_service.search_character_changes(
        ctx.db,
        ctx.book.id,
        query,
        chapter_idx=int(ctx.chapter.idx),
        top_k=limit,
        unique_characters=False,
    )
    items = found[:limit]
    ids = [int(item["character_id"]) for item in items]
    names = _display_names(ctx.db, ctx.book.id, ids, int(ctx.chapter.idx))
    for item, character_id in zip(items, ids):
        item.pop("character_id", None)
        item["character"] = names.get(character_id, item.get("character") or "")
    items, char_truncated = _cap_by_chars(items)
    return {
        "query": query,
        "limit": limit,
        "items": items,
        "count": len(items),
        "truncated": len(found) > limit or char_truncated,
    }


def execute(tool: str, args: dict[str, Any] | None, ctx: ToolContext) -> dict[str, Any]:
    """执行一个工具；业务错误转成 ok=false，不抛到 API。"""
    name = str(tool or "").strip()
    known = {item["name"] for item in SPECS}
    if name not in known:
        return {
            "ok": False,
            "error": "unknown_tool",
            "data": {"message": f"未知工具：{name}"},
        }
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
        if name == "get_toc":
            return {"ok": True, "data": _get_toc(ctx, prepared)}
        if name == "read_span":
            return {"ok": True, "data": _read_span(ctx, prepared)}
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
        return {
            "ok": False,
            "error": "unknown_tool",
            "data": {"message": f"未知工具：{name}"},
        }
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
        return (
            f"第{(data.get('chapter_idx') or 0) + 1}章 {len(data.get('text') or '')} 字"
        )
    if tool == "search_text":
        mode = data.get("mode") or "hybrid"
        unit = "块" if mode == "hybrid" else "处"
        return f"正文 {data.get('count', 0)} {unit}（{mode}）"
    if tool == "get_toc":
        return f"目录 {len(data.get('items') or [])} 章"
    if tool == "read_span":
        return f"区间 {len(data.get('text') or '')} 字"
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
