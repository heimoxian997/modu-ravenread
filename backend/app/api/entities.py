"""人物 / 世界观索引、检索与人工修正接口。"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.schemas.entity_index import (
    CharacterCardUpdate,
    EntityIndexBuildRequest,
    EntitySearchRequest,
    LoreUpdate,
)
from app.services import (
    entity_index_service,
    entity_layer_service,
    entity_override_service,
)

router = APIRouter(prefix="/books", tags=["人物与世界"])


def _unavailable_status(
    db: Session, book_id: int, exc: Exception
) -> dict:
    try:
        counts = entity_index_service.get_entity_counts(db, book_id)
    except BusinessException:
        raise
    return {
        "available": False,
        "total_main": counts["total_main"],
        "chapters_processed": counts["chapters_processed"],
        "complete": (
            counts["chapters_processed"] >= counts["total_main"]
        ),
        "character_deltas": counts["character_deltas"],
        "character_snapshots": counts["character_snapshots"],
        "max_processed_chapter": counts["max_processed_chapter"],
        "character_indexed": 0,
        "character_dirty": entity_override_service.is_dirty(
            db, book_id, "character"
        ),
        "character_needs_rebuild": True,
        "lore_facts": counts["lore_facts"],
        "lore_indexed": 0,
        "lore_dirty": entity_override_service.is_dirty(
            db, book_id, "lore"
        ),
        "lore_needs_rebuild": True,
        "collections": entity_index_service.get_collection_names(),
        "error": str(getattr(exc, "message", exc)),
    }


@router.get(
    "/{book_id}/entity-index",
    summary="人物/世界观索引状态",
)
def entity_index_status(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    try:
        data = entity_index_service.get_entity_index_status(db, book_id)
        return Response.success(
            data={"available": True, **data, "error": None}
        )
    except BusinessException as exc:
        if exc.code == 4040:
            raise
        return Response.success(data=_unavailable_status(db, book_id, exc))
    except Exception as exc:
        return Response.success(data=_unavailable_status(db, book_id, exc))


@router.get(
    "/{book_id}/entities/aliases/{alias}/candidates",
    summary="别名在多个角色之间的候选",
)
def alias_candidates(
    alias: str = Path(..., min_length=1),
    chapter_idx: int | None = Query(
        None, ge=0, description="截至该章（0 基）；不传表示全书视角"
    ),
    limit: int = Query(3, ge=0, le=100, description="0 表示返回全部"),
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    data = entity_layer_service.get_alias_candidates(
        db, book_id, alias, chapter_idx, limit
    )
    return Response.success(data=data)


@router.post(
    "/{book_id}/entity-layer",
    summary="重新加工人物/世界观实体层",
)
def rebuild_entity_layer(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    if entity_index_service.is_building():
        raise BusinessException(message="索引正在重建", code=4090)
    data = entity_layer_service.build_entity_layer(db, book_id)
    return Response.success(message="实体层已重新加工", data=data)


@router.post(
    "/{book_id}/entity-index",
    summary="重建人物/世界观索引",
)
def build_entity_index(
    payload: EntityIndexBuildRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    if entity_index_service.is_building():
        raise BusinessException(message="索引正在重建", code=4090)
    counts = entity_index_service.get_entity_counts(db, book_id)
    if (
        counts["chapters_processed"] < counts["total_main"]
        and not payload.allow_partial
    ):
        raise BusinessException(
            message=(
                "结构化抽取尚未覆盖全书"
                f"（{counts['chapters_processed']}/{counts['total_main']}），"
                "请继续生成；如果只想试跑，勾选允许部分索引"
            ),
            code=4000,
        )
    try:
        data = entity_index_service.build_entity_indexes(
            db,
            book_id,
            layer=payload.layer,
            allow_partial=payload.allow_partial,
        )
    except BusinessException:
        raise
    except Exception as exc:
        raise BusinessException(
            message=f"实体索引重建失败：{exc}", code=500
        ) from exc
    return Response.success(message="索引已重建", data=data)


@router.post(
    "/{book_id}/character-search",
    summary="检索人物卡",
)
def search_characters(
    payload: EntitySearchRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    if entity_index_service.is_building():
        raise BusinessException(message="索引正在重建", code=4090)
    if payload.rerank:
        data = entity_index_service.search_character_index_compare(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            recall=payload.recall,
            chapter_idx=payload.chapter_idx,
        )
    else:
        data = entity_index_service.search_character_index(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            chapter_idx=payload.chapter_idx,
        )
    return Response.success(data=data)


@router.post(
    "/{book_id}/lore-search",
    summary="检索世界观",
)
def search_lore(
    payload: EntitySearchRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    if entity_index_service.is_building():
        raise BusinessException(message="索引正在重建", code=4090)
    if payload.rerank:
        data = entity_index_service.search_lore_index_compare(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            recall=payload.recall,
            chapter_idx=payload.chapter_idx,
        )
    else:
        data = entity_index_service.search_lore_index(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            chapter_idx=payload.chapter_idx,
        )
    return Response.success(data=data)


@router.get(
    "/{book_id}/characters/{character_id}",
    summary="人物卡详情",
)
def get_character(
    book_id: int = Path(..., ge=1),
    character_id: int = Path(..., ge=1),
    chapter_idx: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    return Response.success(
        data=entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=chapter_idx
        )
    )


@router.put(
    "/{book_id}/characters/{character_id}/card",
    summary="保存人物卡修正",
)
def update_character_card(
    payload: CharacterCardUpdate,
    book_id: int = Path(..., ge=1),
    character_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    detail = entity_override_service.get_character_detail(
        db, book_id, character_id
    )
    name = detail["character"]["canonical_name"]

    max_processed = entity_layer_service._max_processed_chapter(db, book_id)
    view_chapter = entity_override_service.clamp_chapter(
        payload.chapter_idx, max_processed
    )
    from_chapter = entity_override_service.clamp_chapter(
        payload.from_chapter
        if payload.from_chapter is not None
        else view_chapter,
        max_processed,
    )
    entity_override_service.upsert_character_override(
        db,
        book_id,
        name,
        identity=payload.identity,
        traits=payload.traits,
        relations=payload.relations,
        known_facts=payload.known_facts,
        voice_sample=payload.voice_sample,
        arc=payload.arc,
        from_chapter=from_chapter,
    )
    return Response.success(
        message=f"人物卡修正已保存（从第 {from_chapter + 1} 章起生效）",
        data=entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=view_chapter
        ),
    )


@router.delete(
    "/{book_id}/characters/{character_id}/card",
    summary="恢复人物卡原始内容",
)
def reset_character_card(
    book_id: int = Path(..., ge=1),
    character_id: int = Path(..., ge=1),
    chapter_idx: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    detail = entity_override_service.get_character_detail(
        db, book_id, character_id
    )
    name = detail["character"]["canonical_name"]
    entity_override_service.delete_character_override(db, book_id, name)
    return Response.success(
        message="已恢复原始人物卡",
        data=entity_override_service.get_character_detail(
            db, book_id, character_id, chapter_idx=chapter_idx
        ),
    )


@router.get(
    "/{book_id}/lore/{lore_id}",
    summary="世界观详情",
)
def get_lore(
    book_id: int = Path(..., ge=1),
    lore_id: int = Path(..., ge=1),
    chapter_idx: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    return Response.success(
        data=entity_override_service.get_lore_detail(
            db, book_id, lore_id, chapter_idx=chapter_idx
        )
    )


@router.put(
    "/{book_id}/lore/{lore_id}",
    summary="保存世界观修正",
)
def update_lore(
    payload: LoreUpdate,
    book_id: int = Path(..., ge=1),
    lore_id: int = Path(..., ge=1),
    chapter_idx: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    detail = entity_override_service.get_lore_detail(
        db, book_id, lore_id, chapter_idx=chapter_idx
    )
    if chapter_idx is not None and not detail.get("available", True):
        raise BusinessException(
            message=(
                f"第 {detail['query_chapter'] + 1} 章还没有这条世界观，"
                "无法在该章修正"
            ),
            code=4000,
        )
    lore = detail["lore"]
    entity_override_service.upsert_lore_override(
        db,
        book_id,
        lore["canonical_name"],
        lore["chapter_idx"],
        category=payload.category,
        aliases=payload.aliases,
        summary=payload.summary,
        details=payload.details,
        entities=payload.entities,
    )
    return Response.success(
        message=f"世界观修正已保存（作用于第 {lore['chapter_idx'] + 1} 章的记录）",
        data=entity_override_service.get_lore_detail(
            db, book_id, lore_id, chapter_idx=chapter_idx
        ),
    )


@router.delete(
    "/{book_id}/lore/{lore_id}",
    summary="恢复世界观原始内容",
)
def reset_lore(
    book_id: int = Path(..., ge=1),
    lore_id: int = Path(..., ge=1),
    chapter_idx: int | None = Query(default=None, ge=0),
    db: Session = Depends(get_db),
):
    detail = entity_override_service.get_lore_detail(
        db, book_id, lore_id, chapter_idx=chapter_idx
    )
    lore = detail["lore"]
    if lore["chapter_idx"] is not None:
        entity_override_service.delete_lore_override(
            db, book_id, lore["canonical_name"], lore["chapter_idx"]
        )
    return Response.success(
        message="已恢复原始世界观",
        data=entity_override_service.get_lore_detail(
            db, book_id, lore_id, chapter_idx=chapter_idx
        ),
    )
