"""人物 / 别称 / 提及 / 快照 / 世界观离线重放。

别名是候选证据：允许多个角色共享一个 alias，不自动合并角色。
只读 chapter_extractions.parsed；不调用 LLM、不写 Qdrant。
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.database import Base
from app.models.book import Book
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_alias_observation import CharacterAliasObservation
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.character_mention import CharacterMention
from app.models.lore_fact import LoreFact
from app.services import entity_override_service

MAX_ALIAS_DEPTH = 10
MAX_TRAITS = 40
MAX_RELATIONS = 40
MAX_FACTS = 80
MAX_LORE_DETAILS = 10
CARD_TRAIT_LIMIT = 12
CARD_RELATION_LIMIT = 12
CARD_FACT_LIMIT = 20
CARD_TEXT_LIMIT = 120

ALIAS_DEFAULT_LIMIT = 3
ALIAS_MAX_LIMIT = 100
ALIAS_SCORE_CHAPTER = 0.45
ALIAS_SCORE_TOTAL = 0.35
ALIAS_SCORE_RECENCY = 0.15
ALIAS_SCORE_PROTAGONIST = 0.05
ALIAS_ELIGIBLE_TOTAL_SHARE = 0.2
ALIAS_ELIGIBLE_RECENT_CHAPTERS = 5

PRONOUNS = {
    "他",
    "她",
    "它",
    "我",
    "你",
    "您",
    "他们",
    "她们",
    "它们",
    "我们",
    "你们",
    "自己",
    "对方",
    "那人",
    "此人",
    "这女子",
    "那女子",
    "众人",
    "大家",
    "有人",
    "某人",
}


@dataclass
class _CharacterState:
    canonical_name: str
    first_seen: int | None = None
    last_seen: int | None = None
    mention_count: int = 0
    is_protagonist: bool = False
    identity: str = ""
    traits: list = field(default_factory=list)
    relations: list = field(default_factory=list)
    known_facts: list = field(default_factory=list)
    voice_sample: str = ""
    arc: str = ""


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _is_pronoun(value: Any) -> bool:
    return _text(value) in PRONOUNS


def _valid_alias(alias: str, canonical: str) -> bool:
    """只排除空/代词/同名；不按长度、不按称谓表过滤。"""
    return bool(alias and canonical and alias != canonical and not _is_pronoun(alias))


def _as_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _list_key(item: Any) -> str | None:
    if item is None:
        return None
    if isinstance(item, str):
        value = item.strip()
        return value or None
    try:
        return json.dumps(item, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(item)


def _merge_list(old: list, new: Any, limit: int | None = None) -> list:
    result = list(old or [])
    seen = {_list_key(item) for item in result}
    for item in _as_list(new):
        key = _list_key(item)
        if key is None or key in seen:
            continue
        result.append(item.strip() if isinstance(item, str) else item)
        seen.add(key)
        if limit is not None and len(result) >= limit:
            break
    return result


def _merge_identity(old: str, new: str) -> str:
    old = _text(old)
    new = _text(new)
    if not new:
        return old
    if not old or old == new:
        return new if not old else old
    if old in new:
        return new
    if new in old:
        return old
    return new


def _added_items(old: list, new: list) -> list:
    old_keys = {_list_key(item) for item in old}
    return [item for item in new if _list_key(item) not in old_keys]


def _apply_delta(state: _CharacterState, delta: dict) -> None:
    identity = _text(delta.get("identity"))
    if identity:
        state.identity = _merge_identity(state.identity, identity)
    for field_name, limit in (
        ("traits", MAX_TRAITS),
        ("relations", MAX_RELATIONS),
        ("known_facts", MAX_FACTS),
    ):
        old = getattr(state, field_name)
        setattr(
            state,
            field_name,
            _merge_list(old, delta.get(field_name), limit),
        )
    for field_name in ("voice_sample", "arc"):
        value = _text(delta.get(field_name))
        if value:
            setattr(state, field_name, value)


def _chapter_alias_map(characters_data: dict):
    raw: dict[str, str] = {}
    for pair in _as_list(characters_data.get("new_aliases")):
        if not isinstance(pair, dict):
            continue
        alias = _text(pair.get("alias"))
        canonical = _text(pair.get("canonical"))
        if _valid_alias(alias, canonical):
            raw.setdefault(alias, canonical)

    def resolve(value: Any) -> str:
        current = _text(value)
        seen: set[str] = set()
        for _ in range(MAX_ALIAS_DEPTH):
            if not current or current in seen:
                break
            seen.add(current)
            target = raw.get(current)
            if not target or target == current:
                break
            current = target
        return current

    return resolve


def _lore_alias_map(chapters: list[tuple[int, dict]]):
    raw: dict[str, str] = {}
    for _, parsed in chapters:
        for item in _as_list(parsed.get("lore")):
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name"))
            for alias_value in _as_list(item.get("aliases")):
                alias = _text(alias_value)
                if _valid_alias(alias, name):
                    raw.setdefault(alias, name)

    def resolve(value: Any) -> str:
        current = _text(value)
        seen: set[str] = set()
        for _ in range(MAX_ALIAS_DEPTH):
            if not current or current in seen:
                break
            seen.add(current)
            target = raw.get(current)
            if not target or target == current:
                break
            current = target
        return current

    return resolve


def _ensure_alias_schema(db: Session) -> None:
    """旧表 UNIQUE(book_id, alias) 不兼容多对多；删除并重建。"""
    rows = db.execute(text("PRAGMA table_info(character_aliases)")).fetchall()
    columns = {row[1] for row in rows}
    if rows and "occurrence_count" not in columns:
        db.execute(text("DROP TABLE character_aliases"))
        db.commit()
    snapshot_rows = db.execute(
        text("PRAGMA table_info(character_snapshots)")
    ).fetchall()
    if snapshot_rows:
        db.execute(text("DROP TABLE character_snapshots"))
        db.commit()
    Base.metadata.create_all(bind=db.get_bind())


def _load_chapters(db: Session, book_id: int) -> tuple[Book, list[tuple[int, dict]]]:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    rows = (
        db.query(ChapterExtraction)
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.status == "success",
        )
        .order_by(ChapterExtraction.chapter_idx.asc())
        .all()
    )
    if not rows:
        raise BusinessException(
            message="没有成功抽取的章节，先生成章节摘要", code=4000
        )
    chapters: list[tuple[int, dict]] = []
    for row in rows:
        parsed = _as_dict(row.parsed)
        if parsed:
            chapters.append((row.chapter_idx, parsed))
    if not chapters:
        raise BusinessException(message="没有可用的成功抽取章节", code=4000)
    return book, chapters


def _normalize_protagonist(value: Any) -> str:
    text_value = _text(value)
    text_value = re.sub(r"[（(][^）)]*[）)]", "", text_value)
    return re.sub(r"\s+", "", text_value)


def build_entity_layer(db: Session, book_id: int) -> dict[str, Any]:
    """整书事务重建人物、别名观测、快照与 lore。"""
    _ensure_alias_schema(db)
    book, chapters = _load_chapters(db, book_id)
    resolve_lore = _lore_alias_map(chapters)

    characters: dict[str, _CharacterState] = {}
    # (alias, canonical, chapter_idx) -> {count, sources}
    observations: dict[tuple[str, str, int], dict[str, Any]] = {}
    mention_rows: list[dict[str, Any]] = []
    delta_rows: list[dict[str, Any]] = []
    lore_rows: list[dict[str, Any]] = []
    pronouns_skipped = 0

    def ensure(name: str, chapter_idx: int | None = None) -> _CharacterState | None:
        name = _text(name)
        if not name:
            return None
        state = characters.get(name)
        if state is None:
            state = _CharacterState(canonical_name=name)
            characters[name] = state
        if chapter_idx is not None:
            if state.first_seen is None or chapter_idx < state.first_seen:
                state.first_seen = chapter_idx
            if state.last_seen is None or chapter_idx > state.last_seen:
                state.last_seen = chapter_idx
        return state

    def add_observation(
        alias_value: Any, canonical: str, chapter_idx: int, source: str
    ) -> None:
        nonlocal pronouns_skipped
        alias = _text(alias_value)
        if not alias:
            return
        if _is_pronoun(alias):
            pronouns_skipped += 1
            return
        if alias == canonical:
            return
        key = (alias, canonical, chapter_idx)
        item = observations.setdefault(
            key, {"count": 0, "sources": set()}
        )
        item["count"] += 1
        item["sources"].add(source)

    for chapter_idx, parsed in chapters:
        characters_data = _as_dict(parsed.get("characters"))
        resolve_chapter = _chapter_alias_map(characters_data)

        # new_aliases 只在当前章参与名称解析，并保留为候选证据。
        for pair in _as_list(characters_data.get("new_aliases")):
            if not isinstance(pair, dict):
                continue
            alias = _text(pair.get("alias"))
            canonical_raw = _text(pair.get("canonical"))
            if _is_pronoun(alias):
                pronouns_skipped += 1
                continue
            canonical = resolve_chapter(canonical_raw)
            if not canonical:
                continue
            if _is_pronoun(canonical):
                pronouns_skipped += 1
                continue
            if not _valid_alias(alias, canonical):
                continue
            ensure(canonical, chapter_idx)
            add_observation(alias, canonical, chapter_idx, "new_alias")

        chapter_mentions: dict[str, dict[str, Any]] = {}
        for mention in _as_list(characters_data.get("mentions")):
            if not isinstance(mention, dict):
                continue
            name = resolve_chapter(mention.get("name"))
            if not name:
                continue
            if _is_pronoun(name):
                pronouns_skipped += 1
                continue
            ensure(name, chapter_idx)
            row = chapter_mentions.setdefault(
                name, {"aliases": [], "role": ""}
            )
            for alias_value in _as_list(mention.get("aliases")):
                alias = _text(alias_value)
                if not alias:
                    continue
                if _is_pronoun(alias):
                    pronouns_skipped += 1
                    continue
                if alias == name:
                    continue
                row["aliases"] = _merge_list(row["aliases"], [alias])
                add_observation(alias, name, chapter_idx, "mention")
            role = _text(mention.get("role"))
            if role and not row["role"]:
                row["role"] = role
        for name, row in chapter_mentions.items():
            characters[name].mention_count += 1
            mention_rows.append(
                {
                    "canonical": name,
                    "chapter_idx": chapter_idx,
                    "aliases": row["aliases"],
                    "role": row["role"] or None,
                }
            )

        delta_groups: dict[str, list[dict]] = {}
        for delta in _as_list(characters_data.get("card_deltas")):
            if not isinstance(delta, dict):
                continue
            name = resolve_chapter(delta.get("character"))
            if not name:
                continue
            if _is_pronoun(name):
                pronouns_skipped += 1
                continue
            ensure(name, chapter_idx)
            delta_groups.setdefault(name, []).append(delta)
        for name, deltas in delta_groups.items():
            state = characters[name]
            before_identity = state.identity
            before_traits = list(state.traits)
            before_relations = list(state.relations)
            before_facts = list(state.known_facts)
            before_voice = state.voice_sample
            before_arc = state.arc
            for delta in deltas:
                _apply_delta(state, delta)
            identity = (
                state.identity
                if state.identity != before_identity
                else None
            )
            traits_add = _added_items(before_traits, state.traits)
            relations_add = _added_items(
                before_relations, state.relations
            )
            facts_add = _added_items(before_facts, state.known_facts)
            voice_sample = (
                state.voice_sample
                if state.voice_sample != before_voice
                else None
            )
            arc = state.arc if state.arc != before_arc else None
            if identity is not None or traits_add or relations_add or facts_add or voice_sample is not None or arc is not None:
                delta_rows.append(
                    {
                        "canonical": name,
                        "chapter_idx": chapter_idx,
                        "identity": identity,
                        "traits_add": traits_add,
                        "relations_add": relations_add,
                        "known_facts_add": facts_add,
                        "voice_sample": voice_sample,
                        "arc": arc,
                    }
                )

        chapter_lore: dict[str, dict[str, Any]] = {}
        for item in _as_list(parsed.get("lore")):
            if not isinstance(item, dict):
                continue
            name = resolve_lore(item.get("name"))
            if not name:
                continue
            row = chapter_lore.setdefault(
                name,
                {
                    "canonical_name": name,
                    "chapter_idx": chapter_idx,
                    "category": None,
                    "summary": None,
                    "aliases": [],
                    "details": [],
                    "entities": [],
                },
            )
            row["aliases"] = _merge_list(
                row["aliases"], item.get("aliases")
            )
            summary = _text(item.get("summary"))
            if summary:
                row["summary"] = summary
            category = _text(item.get("category"))
            if category:
                row["category"] = category
            row["details"] = _merge_list(
                row["details"], item.get("details"), MAX_LORE_DETAILS
            )
            row["entities"] = _merge_list(
                row["entities"], item.get("entities")
            )
        lore_rows.extend(chapter_lore.values())

    # observations -> character_aliases 聚合。
    alias_aggregates: dict[tuple[str, str], dict[str, Any]] = {}
    for (alias, canonical, chapter_idx), item in observations.items():
        key = (alias, canonical)
        aggregate = alias_aggregates.setdefault(
            key,
            {
                "alias": alias,
                "canonical": canonical,
                "first_seen": chapter_idx,
                "last_seen": chapter_idx,
                "occurrence_count": 0,
                "sources": set(),
            },
        )
        aggregate["first_seen"] = min(
            aggregate["first_seen"], chapter_idx
        )
        aggregate["last_seen"] = max(
            aggregate["last_seen"], chapter_idx
        )
        aggregate["occurrence_count"] += item["count"]
        aggregate["sources"].update(item["sources"])

    aliases_by_character: dict[str, list[str]] = {}
    for aggregate in alias_aggregates.values():
        aliases_by_character.setdefault(
            aggregate["canonical"], []
        ).append(aggregate["alias"])

    protagonist_names = {
        _normalize_protagonist(value)
        for value in _as_list(book.protagonists)
        if _normalize_protagonist(value)
    }
    for state in characters.values():
        names = [
            state.canonical_name,
            *aliases_by_character.get(state.canonical_name, []),
        ]
        normalized = {_normalize_protagonist(name) for name in names}
        state.is_protagonist = bool(normalized & protagonist_names)

    # 整书事务重建；删除顺序按任务约定。
    db.query(CharacterAliasObservation).filter(
        CharacterAliasObservation.book_id == book_id
    ).delete(synchronize_session=False)
    db.query(CharacterAlias).filter(
        CharacterAlias.book_id == book_id
    ).delete(synchronize_session=False)
    db.query(CharacterMention).filter(
        CharacterMention.book_id == book_id
    ).delete(synchronize_session=False)
    db.query(CharacterChapterDelta).filter(
        CharacterChapterDelta.book_id == book_id
    ).delete(synchronize_session=False)
    db.query(Character).filter(
        Character.book_id == book_id
    ).delete(synchronize_session=False)
    db.query(LoreFact).filter(
        LoreFact.book_id == book_id
    ).delete(synchronize_session=False)
    db.flush()

    character_objects: dict[str, Character] = {}
    for canonical in sorted(characters):
        state = characters[canonical]
        obj = Character(
            book_id=book_id,
            canonical_name=canonical,
            first_seen_chapter=state.first_seen,
            last_seen_chapter=state.last_seen,
            mention_count=state.mention_count,
            is_protagonist=state.is_protagonist,
            source="extracted",
        )
        db.add(obj)
        character_objects[canonical] = obj
    db.flush()

    alias_rows = sorted(
        alias_aggregates.values(),
        key=lambda row: (row["alias"], row["canonical"]),
    )
    for aggregate in alias_rows:
        sources = aggregate["sources"]
        source = (
            next(iter(sources))
            if len(sources) == 1
            else "mixed"
        )
        db.add(
            CharacterAlias(
                book_id=book_id,
                character_id=character_objects[aggregate["canonical"]].id,
                alias=aggregate["alias"],
                first_seen_chapter=aggregate["first_seen"],
                last_seen_chapter=aggregate["last_seen"],
                occurrence_count=aggregate["occurrence_count"],
                source=source,
            )
        )

    for (alias, canonical, chapter_idx), item in sorted(
        observations.items(),
        key=lambda pair: (pair[0][2], pair[0][0], pair[0][1]),
    ):
        sources = item["sources"]
        source = "new_alias" if "new_alias" in sources else "mention"
        db.add(
            CharacterAliasObservation(
                book_id=book_id,
                character_id=character_objects[canonical].id,
                alias=alias,
                chapter_idx=chapter_idx,
                count=item["count"],
                source=source,
            )
        )

    mention_rows.sort(
        key=lambda row: (row["chapter_idx"], row["canonical"])
    )
    for row in mention_rows:
        db.add(
            CharacterMention(
                book_id=book_id,
                character_id=character_objects[row["canonical"]].id,
                chapter_idx=row["chapter_idx"],
                aliases=row["aliases"],
                role=row["role"],
            )
        )

    delta_rows.sort(
        key=lambda row: (row["chapter_idx"], row["canonical"])
    )
    for row in delta_rows:
        db.add(
            CharacterChapterDelta(
                book_id=book_id,
                character_id=character_objects[row["canonical"]].id,
                chapter_idx=row["chapter_idx"],
                identity=row["identity"],
                traits_add=row["traits_add"],
                relations_add=row["relations_add"],
                known_facts_add=row["known_facts_add"],
                voice_sample=row["voice_sample"],
                arc=row["arc"],
            )
        )

    # lore 里与人物规范名重名的条目丢弃：人物归 characters，不归世界观。
    # 抽取侧只能拦住"本章及以前已登记"的人物，首次出现就只写进 lore 的
    # 名字拦不住；这里用全书人物名再兜一层。
    character_names = set(characters.keys())
    lore_dropped_as_character = 0
    kept_lore: list[dict[str, Any]] = []
    for row in lore_rows:
        if row["canonical_name"] in character_names:
            lore_dropped_as_character += 1
            continue
        kept_lore.append(row)
    lore_rows = kept_lore

    lore_rows.sort(
        key=lambda row: (row["chapter_idx"], row["canonical_name"])
    )
    for row in lore_rows:
        db.add(
            LoreFact(
                book_id=book_id,
                canonical_name=row["canonical_name"],
                category=row["category"],
                aliases=row["aliases"],
                summary=row["summary"],
                details=row["details"],
                entities=row["entities"],
                chapter_idx=row["chapter_idx"],
            )
        )

    db.commit()
    entity_override_service.mark_dirty(db, book_id, "character")
    entity_override_service.mark_dirty(db, book_id, "lore")

    alias_chars: dict[str, dict[str, dict[str, Any]]] = {}
    for aggregate in alias_aggregates.values():
        alias_chars.setdefault(aggregate["alias"], {})[
            aggregate["canonical"]
        ] = aggregate
    multi_alias_count = sum(
        1 for candidates in alias_chars.values() if len(candidates) >= 2
    )
    samples = []
    for alias, candidates in sorted(
        alias_chars.items(),
        key=lambda pair: (-len(pair[1]), pair[0]),
    ):
        if len(candidates) < 2:
            continue
        candidate_rows = []
        for canonical, aggregate in candidates.items():
            candidate_chapters = sorted(
                chapter
                for (a, c, chapter) in observations
                if a == alias and c == canonical
            )
            candidate_rows.append(
                {
                    "character": canonical,
                    "count": aggregate["occurrence_count"],
                    "chapters": candidate_chapters,
                    "last_seen": aggregate["last_seen"],
                }
            )
        candidate_rows.sort(
            key=lambda row: (-row["count"], -row["last_seen"], row["character"])
        )
        samples.append({"alias": alias, "candidates": candidate_rows})
        if len(samples) >= 10:
            break

    return {
        "book_id": book_id,
        "chapters_processed": len(chapters),
        "characters": len(characters),
        "deltas": len(delta_rows),
        "aliases": len(alias_rows),
        "alias_rows": len(alias_rows),
        "alias_observations": len(observations),
        "mentions": len(mention_rows),
        "lore_facts": len(lore_rows),
        "lore_dropped_as_character": lore_dropped_as_character,
        "multi_alias_count": multi_alias_count,
        "multi_alias_samples": samples,
        "pronouns_skipped": pronouns_skipped,
        "max_processed_chapter": chapters[-1][0] if chapters else -1,
    }


def _ambiguous_alias_names(db: Session, book_id: int) -> set[str]:
    """全书里被多个角色共享的称呼。

    这类词（女人、姑娘、小姐）是泛称，拿来当显示名没有区分度，
    宁可回落到规范名。
    """
    distinctive = func.count(func.distinct(CharacterAliasObservation.character_id))
    rows = (
        db.query(CharacterAliasObservation.alias, distinctive)
        .filter(CharacterAliasObservation.book_id == book_id)
        .group_by(CharacterAliasObservation.alias)
        .having(distinctive > 1)
        .all()
    )
    return {_text(row[0]) for row in rows}


def _alias_display_names(
    db: Session,
    book_id: int,
    character_ids: set[int],
    chapter_idx: int | None,
    alias: str,
    blocked: set[str],
) -> dict[int, tuple[str, int]]:
    """每个角色截至第 N 章最常用的称呼，以及它有多少种称呼。

    返回 (最常用称呼, 称呼种类数)。平局时优先当前查询的称呼：这样如果读者看到
    的词就是它最常用的叫法，调用方会回落到规范名，避免出现「姐姐指的是姐姐」
    这种没有区分度的结果。
    """
    if not character_ids:
        return {}
    query = (
        db.query(
            CharacterAliasObservation.character_id,
            CharacterAliasObservation.alias,
            func.sum(CharacterAliasObservation.count),
        )
        .filter(
            CharacterAliasObservation.book_id == book_id,
            CharacterAliasObservation.character_id.in_(character_ids),
        )
    )
    if chapter_idx is not None:
        query = query.filter(
            CharacterAliasObservation.chapter_idx <= int(chapter_idx)
        )
    query = query.group_by(
        CharacterAliasObservation.character_id,
        CharacterAliasObservation.alias,
    )
    rows: dict[int, list[tuple[int, str]]] = {}
    for character_id, name, total in query.all():
        rows.setdefault(int(character_id), []).append(
            (int(total or 0), _text(name))
        )
    result: dict[int, tuple[str, int]] = {}
    for character_id, pairs in rows.items():
        pairs.sort(
            key=lambda item: (
                -item[0],
                1 if item[1] in blocked else 0,
                0 if item[1] == alias else 1,
                item[1],
            )
        )
        result[character_id] = (pairs[0][1], len(pairs))
    return result


def _alias_reason(
    alias: str,
    chapter_idx: int | None,
    candidates: list[dict[str, Any]],
    eligible_count: int,
    chapter_total: int,
) -> str:
    if not candidates:
        if chapter_idx is None:
            return f"全书没有「{alias}」的观测"
        return f"截至第{chapter_idx + 1}章没有「{alias}」的观测"
    best = candidates[0]
    if chapter_idx is None:
        if len(candidates) == 1:
            return f"全书只有「{alias}」指向{best['display_name']}"
        return f"全书有{len(candidates)}人使用「{alias}」，需结合上下文判断"
    scope = f"截至第{chapter_idx + 1}章"
    if len(candidates) == 1:
        return f"{scope}只有「{alias}」指向{best['display_name']}"
    if best["chapter_hits"] == 0:
        return f"本章未出现「{alias}」，按最近出现章回落到{best['display_name']}"
    if eligible_count >= 2:
        return (
            f"本章「{alias}」出现{chapter_total}次，"
            f"{eligible_count}个候选都有出现证据，需结合原文判断"
        )
    return f"{scope}有{len(candidates)}人使用「{alias}」"


def get_alias_candidates(
    db: Session,
    book_id: int,
    alias: str,
    chapter_idx: int | None = None,
    limit: int = ALIAS_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """给出别名的候选角色与证据，不下最终结论。

    所有统计都截断到 chapter_idx（0 基）：后文的观测、后文才出现的称呼都不返回。
    是否歧义看"有资格的候选数"，不看"本章命中几人"——后者会把全书高频的
    主角剪掉，反而更容易错。
    """
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    alias = _text(alias)
    if not alias:
        raise BusinessException(message="别名不能为空", code=4000)
    if limit is None:
        limit = 0
    if limit < 0:
        raise BusinessException(message="limit 不能为负数", code=4000)
    limit = min(int(limit), ALIAS_MAX_LIMIT)
    gate = None if chapter_idx is None else max(0, int(chapter_idx))

    query = (
        db.query(CharacterAliasObservation, Character)
        .join(
            Character,
            Character.id == CharacterAliasObservation.character_id,
        )
        .filter(
            CharacterAliasObservation.book_id == book_id,
            CharacterAliasObservation.alias == alias,
        )
    )
    if gate is not None:
        query = query.filter(CharacterAliasObservation.chapter_idx <= gate)
    rows = query.all()

    grouped: dict[int, dict[str, Any]] = {}
    for observation, character in rows:
        chapter = int(observation.chapter_idx)
        count = int(observation.count or 0)
        item = grouped.setdefault(
            character.id,
            {
                "character_id": character.id,
                "canonical_name": character.canonical_name,
                "first_seen_chapter": chapter,
                "last_seen_chapter": chapter,
                "occurrence_count": 0,
                "chapter_hits": 0,
                "chapters": [],
                "is_protagonist": bool(character.is_protagonist),
            },
        )
        item["first_seen_chapter"] = min(item["first_seen_chapter"], chapter)
        item["last_seen_chapter"] = max(item["last_seen_chapter"], chapter)
        item["occurrence_count"] += count
        item["chapters"].append(chapter)
        if gate is not None and chapter == gate:
            item["chapter_hits"] += count

    candidates = list(grouped.values())
    chapter_total = sum(item["chapter_hits"] for item in candidates)
    max_total = max((item["occurrence_count"] for item in candidates), default=0)
    max_last_seen = max(
        (item["last_seen_chapter"] for item in candidates), default=0
    )
    blocked = _ambiguous_alias_names(db, book_id)
    display_names = _alias_display_names(
        db, book_id, set(grouped), gate, alias, blocked
    )

    for item in candidates:
        item["chapters"] = sorted(set(item["chapters"]))
        used, _ = display_names.get(item["character_id"], ("", 0))
        if used and used != alias and used not in blocked:
            display_name = used
        else:
            display_name = item["canonical_name"] or alias
        item["display_name"] = display_name
        item["display_is_canonical"] = bool(
            item["canonical_name"] and display_name == item["canonical_name"]
        )
        chapter_share = (
            item["chapter_hits"] / chapter_total if chapter_total else 0.0
        )
        total_share = (
            item["occurrence_count"] / max_total if max_total else 0.0
        )
        recency = (gate if gate is not None else max_last_seen) - item[
            "last_seen_chapter"
        ]
        score = (
            ALIAS_SCORE_CHAPTER * chapter_share
            + ALIAS_SCORE_TOTAL * total_share
            + ALIAS_SCORE_RECENCY * math.exp(-max(0, recency) / 5)
            + ALIAS_SCORE_PROTAGONIST * (1.0 if item["is_protagonist"] else 0.0)
        )
        item["chapter_share"] = round(chapter_share, 4)
        item["total_share"] = round(total_share, 4)
        item["recency"] = recency
        item["score"] = round(score, 4)
        item["eligible"] = (
            item["chapter_hits"] > 0
            or total_share >= ALIAS_ELIGIBLE_TOTAL_SHARE
            or recency <= ALIAS_ELIGIBLE_RECENT_CHAPTERS
        )

    candidates.sort(
        key=lambda item: (
            -item["score"],
            -item["occurrence_count"],
            item["display_name"],
        )
    )
    eligible_count = sum(1 for item in candidates if item["eligible"])
    alternatives = candidates[1:] if limit == 0 else candidates[1:limit]
    for item in candidates:
        item.pop("canonical_name", None)
    return {
        "alias": alias,
        "chapter_idx": gate,
        "best": candidates[0] if candidates else None,
        "alternatives": alternatives,
        "candidates": candidates,
        "ambiguous": eligible_count >= 2,
        "eligible_count": eligible_count,
        "chapter_total": chapter_total,
        "total_candidates": len(candidates),
        "truncated": len(candidates) > 1 + len(alternatives),
        "reason": _alias_reason(
            alias, gate, candidates, eligible_count, chapter_total
        ),
    }


def _max_processed_chapter(db: Session, book_id: int) -> int:
    value = (
        db.query(func.max(ChapterExtraction.chapter_idx))
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.status == "success",
        )
        .scalar()
    )
    return int(value) if value is not None else -1


def _resolve_character(
    db: Session, book_id: int, character_id_or_name: int | str
) -> Character:
    character = None
    if isinstance(character_id_or_name, int) or (
        isinstance(character_id_or_name, str)
        and character_id_or_name.isdigit()
    ):
        character = (
            db.query(Character)
            .filter(
                Character.id == int(character_id_or_name),
                Character.book_id == book_id,
            )
            .first()
        )
    if character is None:
        character = (
            db.query(Character)
            .filter(
                Character.book_id == book_id,
                Character.canonical_name == str(character_id_or_name),
            )
            .first()
        )
    if not character:
        raise BusinessException(message="人物不存在", code=4040)
    return character


def _card_text(value: Any) -> str:
    return _text(value)[:CARD_TEXT_LIMIT]


def _cap_evidence(
    evidence: dict[str, dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    items = list(evidence.values())
    if len(items) > limit:
        items = sorted(
            items,
            key=lambda item: (
                item["last_seen_chapter"],
                item["first_seen_chapter"],
                item["text"],
            ),
            reverse=True,
        )[:limit]
    return sorted(
        items,
        key=lambda item: (
            item["first_seen_chapter"],
            item["last_seen_chapter"],
            item["text"],
        ),
    )


def get_character_card(
    db: Session,
    book_id: int,
    character_id_or_name: int | str,
    chapter_idx: int,
) -> dict[str, Any]:
    if not db.get(Book, book_id):
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    character = _resolve_character(db, book_id, character_id_or_name)
    max_processed = _max_processed_chapter(db, book_id)
    query_chapter = int(chapter_idx)
    aliases = [
        row.alias
        for row in db.query(CharacterAlias)
        .filter(CharacterAlias.character_id == character.id)
        .order_by(
            CharacterAlias.occurrence_count.desc(),
            CharacterAlias.alias.asc(),
        )
        .all()
    ]
    all_deltas = (
        db.query(CharacterChapterDelta)
        .filter(
            CharacterChapterDelta.book_id == book_id,
            CharacterChapterDelta.character_id == character.id,
            CharacterChapterDelta.chapter_idx <= max(query_chapter, max_processed),
        )
        .order_by(CharacterChapterDelta.chapter_idx.asc())
        .all()
    )
    capped = max_processed >= 0 and query_chapter > max_processed
    first_delta = all_deltas[0].chapter_idx if all_deltas else None
    if (
        not all_deltas
        or query_chapter < 0
        or (first_delta is not None and query_chapter < first_delta)
    ):
        return {
            "available": False,
            "reason": "not_yet",
            "character": character.canonical_name,
            "query_chapter": query_chapter,
            "as_of_chapter": query_chapter,
            "changed": False,
            "capped": capped,
            "max_processed_chapter": max_processed,
            "identity": None,
            "traits": [],
            "relations": [],
            "known_facts": [],
            "voice_sample": None,
            "arc": None,
            "aliases": aliases,
        }

    effective_chapter = min(query_chapter, max_processed)
    identity = None
    voice_sample = None
    arc = None
    traits: dict[str, dict[str, Any]] = {}
    relations: dict[str, dict[str, Any]] = {}
    facts: dict[str, dict[str, Any]] = {}

    def add_evidence(
        target: dict[str, dict[str, Any]],
        values: list,
        chapter: int,
    ) -> None:
        for value in values:
            text = _card_text(value)
            if not text:
                continue
            if text in target:
                target[text]["last_seen_chapter"] = chapter
            else:
                target[text] = {
                    "text": text,
                    "first_seen_chapter": chapter,
                    "last_seen_chapter": chapter,
                }

    for delta in all_deltas:
        if delta.chapter_idx > effective_chapter:
            break
        if delta.identity:
            identity = {
                "text": _text(delta.identity),
                "chapter_idx": delta.chapter_idx,
            }
        add_evidence(traits, list(delta.traits_add or []), delta.chapter_idx)
        add_evidence(
            relations, list(delta.relations_add or []), delta.chapter_idx
        )
        add_evidence(
            facts, list(delta.known_facts_add or []), delta.chapter_idx
        )
        if delta.voice_sample:
            voice_sample = {
                "text": _text(delta.voice_sample),
                "chapter_idx": delta.chapter_idx,
            }
        if delta.arc:
            arc = {
                "text": _text(delta.arc),
                "chapter_idx": delta.chapter_idx,
            }

    return {
        "available": True,
        "character": character.canonical_name,
        "query_chapter": query_chapter,
        "as_of_chapter": effective_chapter,
        "changed": True,
        "capped": capped,
        "max_processed_chapter": max_processed,
        "identity": identity,
        "traits": _cap_evidence(traits, CARD_TRAIT_LIMIT),
        "relations": _cap_evidence(relations, CARD_RELATION_LIMIT),
        "known_facts": _cap_evidence(facts, CARD_FACT_LIMIT),
        "voice_sample": voice_sample,
        "arc": arc,
        "aliases": aliases,
    }


def get_character_changes(
    db: Session,
    book_id: int,
    character_id_or_name: int | str,
    from_chapter: int,
    to_chapter: int,
) -> dict[str, Any]:
    if not db.get(Book, book_id):
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    character = _resolve_character(db, book_id, character_id_or_name)
    rows = (
        db.query(CharacterChapterDelta)
        .filter(
            CharacterChapterDelta.book_id == book_id,
            CharacterChapterDelta.character_id == character.id,
            CharacterChapterDelta.chapter_idx >= int(from_chapter),
            CharacterChapterDelta.chapter_idx <= int(to_chapter),
        )
        .order_by(CharacterChapterDelta.chapter_idx.asc())
        .all()
    )
    return {
        "character": character.canonical_name,
        "from_chapter": int(from_chapter),
        "to_chapter": int(to_chapter),
        "chapters": [
            {
                "chapter_idx": row.chapter_idx,
                "identity": row.identity or "",
                "traits_add": list(row.traits_add or []),
                "relations_add": list(row.relations_add or []),
                "known_facts_add": list(row.known_facts_add or []),
                "voice_sample": row.voice_sample or "",
                "arc": row.arc or "",
            }
            for row in rows
        ],
    }


def _is_adjacent_transposition(first: str, second: str) -> bool:
    if len(first) != len(second) or len(first) < 2:
        return False
    diffs = [
        index
        for index, (a, b) in enumerate(zip(first, second))
        if a != b
    ]
    return (
        len(diffs) == 2
        and diffs[1] == diffs[0] + 1
        and first[diffs[0]] == second[diffs[1]]
        and first[diffs[1]] == second[diffs[0]]
    )


def _near_name(first: str, second: str) -> bool:
    if first == second:
        return False
    if abs(len(first) - len(second)) > 1:
        return False
    if len(first) == len(second):
        diffs = [
            index
            for index, (a, b) in enumerate(zip(first, second))
            if a != b
        ]
        if len(diffs) == 1:
            return True
        return (
            len(diffs) == 2
            and diffs[1] == diffs[0] + 1
            and first[diffs[0]] == second[diffs[1]]
            and first[diffs[1]] == second[diffs[0]]
        )
    shorter, longer = (first, second) if len(first) < len(second) else (second, first)
    index = 0
    skipped = False
    for char in longer:
        if index < len(shorter) and char == shorter[index]:
            index += 1
        elif skipped:
            return False
        else:
            skipped = True
    return True


def get_merge_suggestions(
    db: Session, book_id: int, min_confidence: str = "medium"
) -> list[dict[str, Any]]:
    if min_confidence not in {"high", "medium", "low"}:
        raise BusinessException(
            message="min_confidence 只支持 high / medium / low", code=4000
        )
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)

    aliases = (
        db.query(CharacterAlias, Character)
        .join(Character, Character.id == CharacterAlias.character_id)
        .filter(CharacterAlias.book_id == book_id)
        .all()
    )
    characters = {
        character.id: character for _, character in aliases
    }
    if len(characters) < 2:
        return []
    aliases_by_char: dict[int, set[str]] = {}
    chars_by_alias: dict[str, set[int]] = {}
    for alias_row, character in aliases:
        aliases_by_char.setdefault(character.id, set()).add(alias_row.alias)
        chars_by_alias.setdefault(alias_row.alias, set()).add(character.id)

    shared_by_pair: dict[tuple[int, int], set[str]] = {}
    for alias, ids in chars_by_alias.items():
        for first, second in combinations(sorted(ids), 2):
            shared_by_pair.setdefault((first, second), set()).add(alias)

    co_map: dict[int, set[int]] = {}
    for chapter_idx, character_id in (
        db.query(CharacterMention.chapter_idx, CharacterMention.character_id)
        .filter(CharacterMention.book_id == book_id)
        .all()
    ):
        co_map.setdefault(chapter_idx, set()).add(character_id)

    level = {"low": 0, "medium": 1, "high": 2}[min_confidence]
    results = []
    for (first_id, second_id), shared in shared_by_pair.items():
        first = characters[first_id]
        second = characters[second_id]
        aliases_a = aliases_by_char.get(first_id, set())
        aliases_b = aliases_by_char.get(second_id, set())
        score = 0
        reasons = []

        shared_names = sorted(shared)
        if second.canonical_name in aliases_a or first.canonical_name in aliases_b:
            score += 3
            reasons.append("规范名互为别名")
            # 规范名成为对方别名，同时还有共享别名，属于强身份证据。
            if shared_names:
                score += 1
        if _near_name(first.canonical_name, second.canonical_name):
            score += 3
            reasons.append("名称相邻换位或编辑距离≤1")
            if _is_adjacent_transposition(
                first.canonical_name, second.canonical_name
            ):
                score += 1
                reasons.append("相邻换位强证据")
        if (
            first.canonical_name != second.canonical_name
            and (
                first.canonical_name in second.canonical_name
                or second.canonical_name in first.canonical_name
            )
        ):
            score += 2
            reasons.append("名称包含")
        score += min(len(shared_names), 2)
        reasons.append("共享别名：" + "、".join(shared_names))

        co_chapters = sorted(
            chapter_idx
            for chapter_idx, ids in co_map.items()
            if first_id in ids and second_id in ids
        )
        if co_chapters:
            score -= 2
            reasons.append("同章共现")
        confidence = "high" if score >= 5 else "medium" if score >= 3 else "low"
        if {"low": 0, "medium": 1, "high": 2}[confidence] < level:
            continue
        results.append(
            {
                "character_a": first.canonical_name,
                "character_b": second.canonical_name,
                "score": score,
                "confidence": confidence,
                "shared_aliases": shared_names,
                "reasons": reasons,
                "co_chapters": co_chapters,
            }
        )

    results.sort(
        key=lambda item: (
            -item["score"],
            item["character_a"],
            item["character_b"],
        )
    )
    return results[:50]


def get_lore_card(
    db: Session,
    book_id: int,
    canonical_name: str,
    chapter_idx: int,
) -> dict[str, Any]:
    """第 N 章时的世界观条目。

    世界观每章写一行，内容是"该章的完整描述"，没有人物卡那种增量列。
    所以取 <= N 的最近一行为主（summary / details / category 用它），
    aliases 与 entities 属稳定属性，向前并集去重。
    """
    if not db.get(Book, book_id):
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    max_processed = _max_processed_chapter(db, book_id)
    query_chapter = int(chapter_idx)
    effective = max(0, min(query_chapter, max_processed))
    rows: list[LoreFact] = []
    if max_processed >= 0:
        rows = (
            db.query(LoreFact)
            .filter(
                LoreFact.book_id == book_id,
                LoreFact.canonical_name == canonical_name,
                LoreFact.chapter_idx <= effective,
            )
            .order_by(LoreFact.chapter_idx.asc(), LoreFact.id.asc())
            .all()
        )
    if not rows:
        return {
            "available": False,
            "reason": "not_yet",
            "canonical_name": canonical_name,
            "query_chapter": query_chapter,
            "as_of_chapter": None,
            "occurrences": [],
            "lore_id": None,
            "category": None,
            "aliases": [],
            "summary": None,
            "details": [],
            "entities": [],
            "capped": query_chapter > max_processed,
            "max_processed_chapter": max_processed,
        }
    base = rows[-1]
    category = None
    for row in reversed(rows):
        if row.category:
            category = row.category
            break
    aliases: list[str] = []
    entities: list[str] = []
    for row in rows:
        for value in row.aliases or []:
            token = _text(value)
            if token and token not in aliases:
                aliases.append(token)
        for value in row.entities or []:
            token = _text(value)
            if token and token not in entities:
                entities.append(token)
    return {
        "available": True,
        "reason": None,
        "canonical_name": canonical_name,
        "query_chapter": query_chapter,
        "as_of_chapter": base.chapter_idx,
        "occurrences": [row.chapter_idx for row in rows],
        "lore_id": base.id,
        "category": category,
        "aliases": aliases,
        "summary": base.summary,
        "details": list(base.details or []),
        "entities": entities,
        "capped": query_chapter > max_processed,
        "max_processed_chapter": max_processed,
    }
