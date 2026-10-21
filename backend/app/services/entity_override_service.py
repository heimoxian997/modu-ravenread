"""人物卡 / 世界观人工修正与索引脏标记。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_card_override import CharacterCardOverride
from app.models.character_mention import CharacterMention
from app.models.entity_index_flag import EntityIndexFlag
from app.models.lore_fact import LoreFact
from app.models.lore_override import LoreOverride

CHARACTER_FIELDS = (
    "identity",
    "traits",
    "relations",
    "known_facts",
    "voice_sample",
    "arc",
)
LORE_FIELDS = ("category", "aliases", "summary", "details", "entities")
LAYERS = {"character", "lore"}

# 旧行没有生效章：按该书已成功抽取的最大章号回填，避免历史修正突然影响前面所有章节。
_BACKFILL_FROM_CHAPTER = """
UPDATE character_card_overrides
SET from_chapter = COALESCE((
  SELECT MAX(chapter_idx)
  FROM chapter_extractions
  WHERE chapter_extractions.book_id = character_card_overrides.book_id
    AND chapter_extractions.status = 'success'
), 0)
"""


def ensure_override_schema() -> None:
    """给旧库补 from_chapter 列。

    create_all 不会给已存在的表加列，所以这里查一次 PRAGMA。
    只有“刚加列”这一支才回填旧行；新库由 create_all 直接建出该列，本函数是 no-op。
    """
    from app.database import engine

    with engine.begin() as conn:
        rows = conn.exec_driver_sql(
            "PRAGMA table_info(character_card_overrides)"
        ).fetchall()
        if not rows:
            return
        if any(row[1] == "from_chapter" for row in rows):
            return
        conn.exec_driver_sql(
            "ALTER TABLE character_card_overrides "
            "ADD COLUMN from_chapter INTEGER NOT NULL DEFAULT 0"
        )
        conn.exec_driver_sql(_BACKFILL_FROM_CHAPTER)


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _character_or_404(
    db: Session, book_id: int, character_id: int
) -> Character:
    character = (
        db.query(Character)
        .filter(Character.id == character_id, Character.book_id == book_id)
        .first()
    )
    if not character:
        raise BusinessException(message="人物不存在", code=4040)
    return character


def _lore_or_404(db: Session, book_id: int, lore_id: int) -> LoreFact:
    lore = (
        db.query(LoreFact)
        .filter(LoreFact.id == lore_id, LoreFact.book_id == book_id)
        .first()
    )
    if not lore:
        raise BusinessException(message="世界观条目不存在", code=4040)
    return lore


def _character_override_dict(row: CharacterCardOverride) -> dict[str, Any]:
    return {
        "id": row.id,
        "character_name": row.character_name,
        "from_chapter": int(row.from_chapter or 0),
        "identity": row.identity,
        "traits": list(row.traits or []),
        "relations": list(row.relations or []),
        "known_facts": list(row.known_facts or []),
        "voice_sample": row.voice_sample,
        "arc": row.arc,
    }


def _lore_override_dict(row: LoreOverride) -> dict[str, Any]:
    return {
        "id": row.id,
        "canonical_name": row.canonical_name,
        "chapter_idx": row.chapter_idx,
        "category": row.category,
        "aliases": list(row.aliases or []),
        "summary": row.summary,
        "details": list(row.details or []),
        "entities": list(row.entities or []),
    }


def get_character_override(
    db: Session, book_id: int, character_name: str
) -> dict[str, Any] | None:
    row = (
        db.query(CharacterCardOverride)
        .filter(
            CharacterCardOverride.book_id == book_id,
            CharacterCardOverride.character_name == character_name,
        )
        .first()
    )
    return _character_override_dict(row) if row else None


def get_lore_override(
    db: Session, book_id: int, canonical_name: str, chapter_idx: int
) -> dict[str, Any] | None:
    row = (
        db.query(LoreOverride)
        .filter(
            LoreOverride.book_id == book_id,
            LoreOverride.canonical_name == canonical_name,
            LoreOverride.chapter_idx == chapter_idx,
        )
        .first()
    )
    return _lore_override_dict(row) if row else None


def list_character_overrides(
    db: Session, book_id: int
) -> dict[str, dict[str, Any]]:
    rows = (
        db.query(CharacterCardOverride)
        .filter(CharacterCardOverride.book_id == book_id)
        .all()
    )
    return {row.character_name: _character_override_dict(row) for row in rows}


def list_lore_overrides(
    db: Session, book_id: int
) -> dict[tuple[str, int], dict[str, Any]]:
    rows = (
        db.query(LoreOverride)
        .filter(LoreOverride.book_id == book_id)
        .all()
    )
    return {
        (row.canonical_name, row.chapter_idx): _lore_override_dict(row)
        for row in rows
    }


def mark_dirty(db: Session, book_id: int, layer: str) -> None:
    if layer not in LAYERS:
        raise BusinessException(message="layer 不合法", code=4000)
    flag = (
        db.query(EntityIndexFlag)
        .filter(
            EntityIndexFlag.book_id == book_id,
            EntityIndexFlag.layer == layer,
        )
        .first()
    )
    if not flag:
        flag = EntityIndexFlag(book_id=book_id, layer=layer, dirty=True)
        db.add(flag)
    else:
        flag.dirty = True
    db.commit()


def clear_dirty(db: Session, book_id: int, layer: str) -> None:
    if layer not in LAYERS:
        raise BusinessException(message="layer 不合法", code=4000)
    flag = (
        db.query(EntityIndexFlag)
        .filter(
            EntityIndexFlag.book_id == book_id,
            EntityIndexFlag.layer == layer,
        )
        .first()
    )
    if not flag:
        db.add(EntityIndexFlag(book_id=book_id, layer=layer, dirty=False))
    else:
        flag.dirty = False
    db.commit()


def is_dirty(db: Session, book_id: int, layer: str) -> bool:
    flag = (
        db.query(EntityIndexFlag)
        .filter(
            EntityIndexFlag.book_id == book_id,
            EntityIndexFlag.layer == layer,
        )
        .first()
    )
    return bool(flag and flag.dirty)


def upsert_character_override(
    db: Session,
    book_id: int,
    character_name: str,
    identity: str | None = None,
    traits: list[Any] | None = None,
    relations: list[Any] | None = None,
    known_facts: list[Any] | None = None,
    voice_sample: str | None = None,
    arc: str | None = None,
    from_chapter: int = 0,
) -> dict[str, Any]:
    _book_or_404(db, book_id)
    row = (
        db.query(CharacterCardOverride)
        .filter(
            CharacterCardOverride.book_id == book_id,
            CharacterCardOverride.character_name == character_name,
        )
        .first()
    )
    if not row:
        row = CharacterCardOverride(
            book_id=book_id, character_name=character_name
        )
        db.add(row)
    row.from_chapter = max(0, int(from_chapter))
    row.identity = identity
    row.traits = list(traits or [])
    row.relations = list(relations or [])
    row.known_facts = list(known_facts or [])
    row.voice_sample = voice_sample
    row.arc = arc
    mark_dirty(db, book_id, "character")
    return _character_override_dict(row)


def delete_character_override(
    db: Session, book_id: int, character_name: str
) -> None:
    row = (
        db.query(CharacterCardOverride)
        .filter(
            CharacterCardOverride.book_id == book_id,
            CharacterCardOverride.character_name == character_name,
        )
        .first()
    )
    if row:
        db.delete(row)
    mark_dirty(db, book_id, "character")


def upsert_lore_override(
    db: Session,
    book_id: int,
    canonical_name: str,
    chapter_idx: int,
    category: str | None = None,
    aliases: list[Any] | None = None,
    summary: str | None = None,
    details: list[Any] | None = None,
    entities: list[Any] | None = None,
) -> dict[str, Any]:
    _book_or_404(db, book_id)
    row = (
        db.query(LoreOverride)
        .filter(
            LoreOverride.book_id == book_id,
            LoreOverride.canonical_name == canonical_name,
            LoreOverride.chapter_idx == chapter_idx,
        )
        .first()
    )
    if not row:
        row = LoreOverride(
            book_id=book_id,
            canonical_name=canonical_name,
            chapter_idx=chapter_idx,
        )
        db.add(row)
    row.category = category
    row.aliases = list(aliases or [])
    row.summary = summary
    row.details = list(details or [])
    row.entities = list(entities or [])
    mark_dirty(db, book_id, "lore")
    return _lore_override_dict(row)


def delete_lore_override(
    db: Session, book_id: int, canonical_name: str, chapter_idx: int
) -> None:
    row = (
        db.query(LoreOverride)
        .filter(
            LoreOverride.book_id == book_id,
            LoreOverride.canonical_name == canonical_name,
            LoreOverride.chapter_idx == chapter_idx,
        )
        .first()
    )
    if row:
        db.delete(row)
    mark_dirty(db, book_id, "lore")


def apply_character_override(
    snapshot: dict[str, Any],
    override: dict[str, Any] | None,
    chapter_idx: int | None = None,
) -> dict[str, Any]:
    """把人工修正合并进基础卡；修正只从 from_chapter 起生效。"""
    effective = dict(snapshot)
    if not override:
        return effective
    from_chapter = int(override.get("from_chapter") or 0)
    if chapter_idx is not None and chapter_idx < from_chapter:
        return effective
    for field in CHARACTER_FIELDS:
        effective[field] = override.get(field)
    return effective


def apply_lore_override(
    lore: dict[str, Any],
    override: dict[str, Any] | None,
) -> dict[str, Any]:
    effective = dict(lore)
    if override:
        for field in LORE_FIELDS:
            effective[field] = override.get(field)
    return effective


def clamp_chapter(chapter_idx: int | None, max_processed: int) -> int:
    """把查询章号夹到 0..max_processed；没传就取最新已处理章。"""
    limit = max(0, int(max_processed))
    if chapter_idx is None:
        return limit
    return max(0, min(int(chapter_idx), limit))


def get_character_detail(
    db: Session,
    book_id: int,
    character_id: int,
    chapter_idx: int | None = None,
) -> dict[str, Any]:
    character = _character_or_404(db, book_id, character_id)
    from app.services import entity_layer_service

    max_processed = entity_layer_service._max_processed_chapter(
        db, book_id
    )
    query_chapter = clamp_chapter(chapter_idx, max_processed)
    card = entity_layer_service.get_character_card(
        db, book_id, character_id, query_chapter
    )
    if card.get("available"):
        base = {
            "as_of_chapter": card["as_of_chapter"],
            "identity": (card["identity"] or {}).get("text"),
            "traits": [item["text"] for item in card["traits"]],
            "relations": [item["text"] for item in card["relations"]],
            "known_facts": [item["text"] for item in card["known_facts"]],
            "voice_sample": (card["voice_sample"] or {}).get("text"),
            "arc": (card["arc"] or {}).get("text"),
        }
    else:
        base = {
            "as_of_chapter": None,
            "identity": None,
            "traits": [],
            "relations": [],
            "known_facts": [],
            "voice_sample": None,
            "arc": None,
        }
    aliases = (
        db.query(CharacterAlias)
        .filter(
            CharacterAlias.character_id == character_id,
            or_(
                CharacterAlias.first_seen_chapter.is_(None),
                CharacterAlias.first_seen_chapter <= query_chapter,
            ),
        )
        .order_by(
            CharacterAlias.occurrence_count.desc(),
            CharacterAlias.alias.asc(),
        )
        .all()
    )
    alias_details = [
        {
            "alias": row.alias,
            "first_seen_chapter": row.first_seen_chapter,
            "last_seen_chapter": (
                min(row.last_seen_chapter, query_chapter)
                if row.last_seen_chapter is not None
                else None
            ),
            "occurrence_count": row.occurrence_count,
        }
        for row in aliases
    ]
    mention_count_at_chapter = (
        db.query(CharacterMention)
        .filter(
            CharacterMention.book_id == book_id,
            CharacterMention.character_id == character_id,
            CharacterMention.chapter_idx <= query_chapter,
        )
        .count()
    )
    last_seen_at_chapter = (
        min(character.last_seen_chapter, query_chapter)
        if character.last_seen_chapter is not None
        else None
    )
    provenance = {
        "identity": card.get("identity"),
        "traits": card.get("traits") or [],
        "relations": card.get("relations") or [],
        "known_facts": card.get("known_facts") or [],
        "voice_sample": card.get("voice_sample"),
        "arc": card.get("arc"),
    }
    totals = card.get("totals") or {
        "traits": len(provenance["traits"]),
        "relations": len(provenance["relations"]),
        "known_facts": len(provenance["known_facts"]),
    }
    capped_by_limit = card.get("capped_by_limit") or {
        "traits": False,
        "relations": False,
        "known_facts": False,
    }
    override = get_character_override(
        db, book_id, character.canonical_name
    )
    effective = apply_character_override(base, override, query_chapter)
    override_from = (
        int(override.get("from_chapter") or 0) if override else None
    )
    return {
        "character": {
            "id": character.id,
            "canonical_name": character.canonical_name,
            "first_seen_chapter": character.first_seen_chapter,
            "last_seen_chapter": last_seen_at_chapter,
            "last_seen_at_chapter": last_seen_at_chapter,
            "mention_count": mention_count_at_chapter,
            "mention_count_at_chapter": mention_count_at_chapter,
            "is_protagonist": character.is_protagonist,
            "aliases": [row.alias for row in aliases],
            "alias_details": alias_details,
        },
        "latest_snapshot": base,
        "provenance": provenance,
        "totals": totals,
        "capped_by_limit": capped_by_limit,
        "query_chapter": query_chapter,
        "max_processed_chapter": max_processed,
        "override_from_chapter": override_from,
        "effective": {
            "identity": effective["identity"],
            "traits": effective["traits"],
            "relations": effective["relations"],
            "known_facts": effective["known_facts"],
            "voice_sample": effective["voice_sample"],
            "arc": effective["arc"],
        },
        "edited": override is not None
        and override_from is not None
        and query_chapter >= override_from,
    }


def get_lore_detail(
    db: Session,
    book_id: int,
    lore_id: int,
    chapter_idx: int | None = None,
) -> dict[str, Any]:
    lore = _lore_or_404(db, book_id, lore_id)
    from app.services import entity_layer_service

    if chapter_idx is None:
        as_of = lore.chapter_idx
        query_chapter = lore.chapter_idx
        occurrences = [lore.chapter_idx]
        card_id = lore.id
        available = True
        base = {
            "category": lore.category,
            "aliases": list(lore.aliases or []),
            "summary": lore.summary,
            "details": list(lore.details or []),
            "entities": list(lore.entities or []),
        }
    else:
        card = entity_layer_service.get_lore_card(
            db, book_id, lore.canonical_name, chapter_idx
        )
        query_chapter = card["query_chapter"]
        available = bool(card["available"])
        as_of = card["as_of_chapter"]
        occurrences = card["occurrences"]
        card_id = card["lore_id"] or lore.id
        base = {
            "category": card["category"],
            "aliases": card["aliases"],
            "summary": card["summary"],
            "details": card["details"],
            "entities": card["entities"],
        }
    override = (
        get_lore_override(db, book_id, lore.canonical_name, as_of)
        if as_of is not None
        else None
    )
    effective = apply_lore_override(base, override)
    return {
        "lore": {
            "id": card_id,
            "canonical_name": lore.canonical_name,
            "category": lore.category,
            "chapter_idx": as_of,
        },
        "effective": {
            "category": effective["category"],
            "aliases": effective["aliases"],
            "summary": effective["summary"],
            "details": effective["details"],
            "entities": effective["entities"],
        },
        "edited": override is not None,
        "available": available,
        "query_chapter": query_chapter,
        "as_of_chapter": as_of,
        "occurrences": occurrences,
    }
