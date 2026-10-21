"""人物快照与世界观事实的独立 Qdrant 向量层。

只读取实体层与抽取覆盖统计；不调用 LLM、不改文本/摘要 collection。
"""

from __future__ import annotations

import hashlib
import threading
import time
import uuid
from typing import Any, Callable

from qdrant_client import QdrantClient, models
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import config
from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.lore_fact import LoreFact
from app.services import (
    embedding_service,
    entity_layer_service,
    entity_override_service,
    text_index_service,
)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
RRF_K = 60
ENCODE_GROUP_SIZE = 256
UPSERT_BATCH_SIZE = 128
LAYERS = {"all", "character", "lore"}

_operation_lock = text_index_service._client_operation_lock
_build_lock = threading.Lock()


def is_building() -> bool:
    return _build_lock.locked()


def get_collection_names() -> dict[str, str]:
    return {
        "character": config.CHARACTER_INDEX_COLLECTION,
        "lore": config.LORE_INDEX_COLLECTION,
    }


def get_index_version(layer: str) -> int:
    if layer == "character":
        return config.CHARACTER_INDEX_VERSION
    return config.LORE_INDEX_VERSION


def _version_key(layer: str) -> str:
    return "key_version" if layer == "character" else "index_version"


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _collection_name(layer: str) -> str:
    return get_collection_names()[layer]


def _point_id(layer: str, book_id: int, row_id: int) -> str:
    namespace = "modu:character" if layer == "character" else "modu:lore"
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"{namespace}:{book_id}:{row_id}")
    )


def _book_filter(
    book_id: int, layer: str = "lore"
) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="book_id", match=models.MatchValue(value=book_id)
            ),
            models.FieldCondition(
                key=_version_key(layer),
                match=models.MatchValue(value=get_index_version(layer)),
            ),
        ]
    )


def _book_only_filter(book_id: int) -> models.Filter:
    return models.Filter(
        must=[
            models.FieldCondition(
                key="book_id", match=models.MatchValue(value=book_id)
            )
        ]
    )


def _collection_dense_dim(info: models.CollectionInfo) -> int | None:
    vectors = info.config.params.vectors
    if isinstance(vectors, dict):
        dense = vectors.get(DENSE_VECTOR_NAME)
        return int(dense.size) if dense is not None else None
    return int(vectors.size) if vectors is not None else None


def _ensure_collection(
    client: QdrantClient, collection_name: str, dim: int
) -> None:
    if client.collection_exists(collection_name):
        info = client.get_collection(collection_name)
        current_dim = _collection_dense_dim(info)
        if current_dim != dim:
            raise BusinessException(
                message=(
                    "嵌入模型已变化，请删除 data/qdrant 后重建。"
                    f"当前 {collection_name} dense 维度为 {current_dim}，"
                    f"本次嵌入维度为 {dim}。"
                )
            )
        sparse_vectors = info.config.params.sparse_vectors or {}
        if SPARSE_VECTOR_NAME not in sparse_vectors:
            raise BusinessException(
                message=(
                    f"{collection_name} 缺少 sparse 向量配置，"
                    "请删除 data/qdrant 后重建。"
                )
            )
        return
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            DENSE_VECTOR_NAME: models.VectorParams(
                size=dim, distance=models.Distance.COSINE
            )
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: models.SparseVectorParams()
        },
    )


def _notify(
    progress: Callable[[str, int, str], None] | None,
    stage: str,
    percent: int,
    message: str,
) -> None:
    if progress is None:
        return
    try:
        progress(stage, max(0, min(100, int(percent))), message)
    except Exception:
        pass


def _counts(db: Session, book_id: int) -> dict[str, int]:
    total_main = (
        db.query(func.count(Chapter.id))
        .filter(Chapter.book_id == book_id, Chapter.kind == "main")
        .scalar()
        or 0
    )
    chapters_processed = (
        db.query(func.count(ChapterExtraction.id))
        .filter(
            ChapterExtraction.book_id == book_id,
            ChapterExtraction.status == "success",
        )
        .scalar()
        or 0
    )
    character_deltas = (
        db.query(func.count(CharacterChapterDelta.id))
        .filter(CharacterChapterDelta.book_id == book_id)
        .scalar()
        or 0
    )
    lore_facts = (
        db.query(func.count(LoreFact.id))
        .filter(LoreFact.book_id == book_id)
        .scalar()
        or 0
    )
    return {
        "total_main": int(total_main),
        "chapters_processed": int(chapters_processed),
        "character_deltas": int(character_deltas),
        "character_snapshots": int(character_deltas),
        "lore_facts": int(lore_facts),
        "max_processed_chapter": entity_layer_service._max_processed_chapter(
            db, book_id
        ),
    }


def _indexed_count(
    client: QdrantClient, layer: str, book_id: int
) -> int:
    collection = _collection_name(layer)
    if not client.collection_exists(collection):
        return 0
    return int(
        client.count(
            collection_name=collection,
            count_filter=_book_filter(book_id, layer),
            exact=True,
        ).count
    )


def _text_hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _indexed_fingerprints(
    client: QdrantClient, layer: str, book_id: int
) -> dict[int, str]:
    collection = _collection_name(layer)
    if not client.collection_exists(collection):
        return {}
    payload_key = "delta_id" if layer == "character" else "lore_id"
    fingerprints: dict[int, str] = {}
    offset = None
    with _operation_lock:
        while True:
            points, offset = client.scroll(
                collection_name=collection,
                scroll_filter=_book_filter(book_id, layer),
                limit=256,
                offset=offset,
                with_payload=[payload_key, "source_hash"],
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                value = payload.get(payload_key)
                if value is not None:
                    fingerprints[int(value)] = str(
                        payload.get("source_hash") or ""
                    )
            if offset is None:
                break
    return fingerprints


def get_entity_index_status(db: Session, book_id: int) -> dict[str, Any]:
    _book_or_404(db, book_id)
    counts = _counts(db, book_id)
    character_source = (
        {
            row["delta_id"]: row["text_hash"]
            for row in _read_character_rows(db, book_id)
        }
        if counts["character_deltas"]
        else {}
    )
    lore_source = (
        {
            row["lore_id"]: row["text_hash"]
            for row in _read_lore_rows(db, book_id)
        }
        if counts["lore_facts"]
        else {}
    )
    client = text_index_service.get_client()
    with _operation_lock:
        character_indexed = _indexed_count(client, "character", book_id)
        lore_indexed = _indexed_count(client, "lore", book_id)
    character_fingerprints = (
        _indexed_fingerprints(client, "character", book_id)
        if character_indexed == counts["character_deltas"]
        else {}
    )
    lore_fingerprints = (
        _indexed_fingerprints(client, "lore", book_id)
        if lore_indexed == counts["lore_facts"]
        else {}
    )
    character_dirty = entity_override_service.is_dirty(
        db, book_id, "character"
    )
    lore_dirty = entity_override_service.is_dirty(db, book_id, "lore")
    return {
        "total_main": counts["total_main"],
        "chapters_processed": counts["chapters_processed"],
        "complete": counts["chapters_processed"] >= counts["total_main"],
        "character_deltas": counts["character_deltas"],
        "character_snapshots": counts["character_deltas"],
        "character_indexed": character_indexed,
        "character_dirty": character_dirty,
        "character_needs_rebuild": (
            character_indexed != counts["character_deltas"]
            or character_fingerprints != character_source
            or character_dirty
        ),
        "lore_facts": counts["lore_facts"],
        "lore_indexed": lore_indexed,
        "lore_dirty": lore_dirty,
        "lore_needs_rebuild": (
            lore_indexed != counts["lore_facts"]
            or lore_fingerprints != lore_source
            or lore_dirty
        ),
        "max_processed_chapter": counts["max_processed_chapter"],
        "collections": get_collection_names(),
    }


def get_entity_counts(db: Session, book_id: int) -> dict[str, int]:
    _book_or_404(db, book_id)
    return _counts(db, book_id)


def _lore_text(row: dict[str, Any]) -> str:
    lines = [
        f"【世界观 · {row['canonical_name']} · "
        f"{row['category'] or '未分类'} · 第{row['chapter_idx'] + 1}章】"
    ]
    if row["aliases"]:
        lines.append("别名：" + "、".join(str(v) for v in row["aliases"]))
    if row["summary"]:
        lines.append(f"概述：{row['summary']}")
    if row["details"]:
        lines.append("细节：" + "、".join(str(v) for v in row["details"][:10]))
    if row["entities"]:
        lines.append("关联：" + "、".join(str(v) for v in row["entities"]))
    return "\n".join(lines)


def _character_delta_text(row: dict[str, Any]) -> str:
    lines = [
        f"【人物变化 · {row['character']} · "
        f"第{row['chapter_idx'] + 1}章】"
    ]
    if row["identity"]:
        lines.append(f"身份：{row['identity']}")
    if row["traits_add"]:
        lines.append("新增性格：" + "、".join(str(v) for v in row["traits_add"]))
    if row["relations_add"]:
        lines.append("新增关系：" + "、".join(str(v) for v in row["relations_add"]))
    if row["known_facts_add"]:
        lines.append("新增事实：" + "、".join(str(v) for v in row["known_facts_add"]))
    if row["voice_sample"]:
        lines.append(f"代表台词：{row['voice_sample']}")
    if row["arc"]:
        lines.append(f"人物弧光：{row['arc']}")
    return "\n".join(lines)


def _read_character_rows(
    db: Session, book_id: int
) -> list[dict[str, Any]]:
    rows = (
        db.query(CharacterChapterDelta, Character)
        .join(Character, Character.id == CharacterChapterDelta.character_id)
        .filter(CharacterChapterDelta.book_id == book_id)
        .order_by(
            CharacterChapterDelta.chapter_idx.asc(),
            CharacterChapterDelta.character_id.asc(),
        )
        .all()
    )
    result = []
    for delta, character in rows:
        item = {
            "delta_id": delta.id,
            "character_id": character.id,
            "character": character.canonical_name,
            "chapter_idx": delta.chapter_idx,
            "first_seen_chapter": character.first_seen_chapter,
            "identity": delta.identity or "",
            "traits_add": list(delta.traits_add or []),
            "relations_add": list(delta.relations_add or []),
            "known_facts_add": list(delta.known_facts_add or []),
            "voice_sample": delta.voice_sample or "",
            "arc": delta.arc or "",
        }
        item["text"] = _character_delta_text(item)
        item["text_hash"] = _text_hash(item["text"])
        result.append(item)
    return result


def _read_lore_rows(db: Session, book_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(LoreFact)
        .filter(LoreFact.book_id == book_id)
        .order_by(LoreFact.chapter_idx.asc(), LoreFact.id.asc())
        .all()
    )
    override_map = entity_override_service.list_lore_overrides(db, book_id)
    result = []
    for row in rows:
        item = {
            "lore_id": row.id,
            "canonical_name": row.canonical_name,
            "category": row.category or "",
            "chapter_idx": row.chapter_idx,
            "aliases": list(row.aliases or []),
            "summary": row.summary or "",
            "details": list(row.details or [])[:10],
            "entities": list(row.entities or []),
        }
        override = override_map.get((row.canonical_name, row.chapter_idx))
        if override:
            effective = entity_override_service.apply_lore_override(
                item, override
            )
            for field in entity_override_service.LORE_FIELDS:
                item[field] = effective[field]
        item["text"] = _lore_text(item)
        item["text_hash"] = _text_hash(item["text"])
        result.append(item)
    return result


def _clear_layer(
    client: QdrantClient, layer: str, book_id: int
) -> None:
    collection = _collection_name(layer)
    with _operation_lock:
        if not client.collection_exists(collection):
            return
        client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=(
                    _book_only_filter(book_id)
                    if layer == "character"
                    else _book_filter(book_id, "lore")
                )
            ),
            wait=True,
        )


def _index_layer(
    client: QdrantClient,
    layer: str,
    book_id: int,
    rows: list[dict[str, Any]],
    batch_size: int,
    progress: Callable[[str, int, str], None] | None,
) -> tuple[int, float]:
    started = time.perf_counter()
    collection = _collection_name(layer)
    if not rows:
        _clear_layer(client, layer, book_id)
        return 0, time.perf_counter() - started

    _notify(progress, layer, 2, f"{layer} 编码 {len(rows)} 条")
    texts = [row["text"] for row in rows]
    docs: list[embedding_service.EmbeddedDoc] = []
    for start in range(0, len(texts), ENCODE_GROUP_SIZE):
        group = texts[start : start + ENCODE_GROUP_SIZE]
        docs.extend(
            embedding_service.encode_documents(group, batch_size=batch_size)
        )
        processed = min(start + len(group), len(texts))
        _notify(
            progress,
            layer,
            5 + int(65 * processed / len(texts)),
            f"{layer} 编码 {processed}/{len(texts)}",
        )
    if len(docs) != len(rows):
        raise BusinessException(
            message=f"{layer} 嵌入数量与源数量不一致，已停止建库。"
        )
    dim = len(docs[0].dense)

    with _operation_lock:
        _ensure_collection(client, collection, dim)
        client.delete(
            collection_name=collection,
            points_selector=models.FilterSelector(
                filter=(
                    _book_only_filter(book_id)
                    if layer == "character"
                    else _book_filter(book_id, "lore")
                )
            ),
            wait=True,
        )

    points: list[models.PointStruct] = []
    for row, doc in zip(rows, docs):
        indices, values = embedding_service.sparse_to_indices(
            doc.sparse, lambda _: None
        )
        vector: dict[str, Any] = {DENSE_VECTOR_NAME: doc.dense}
        if indices:
            vector[SPARSE_VECTOR_NAME] = models.SparseVector(
                indices=indices, values=values
            )
        if layer == "character":
            payload = {
                "book_id": book_id,
                "key_version": get_index_version("character"),
                "delta_id": row["delta_id"],
                "character_id": row["character_id"],
                "character": row["character"],
                "chapter_idx": row["chapter_idx"],
                "source_hash": row["text_hash"],
            }
            point_id = _point_id(layer, book_id, row["delta_id"])
        else:
            payload = {
                "book_id": book_id,
                "index_version": get_index_version("lore"),
                "lore_id": row["lore_id"],
                "canonical_name": row["canonical_name"],
                "category": row["category"],
                "chapter_idx": row["chapter_idx"],
                "source_hash": row["text_hash"],
            }
            point_id = _point_id(layer, book_id, row["lore_id"])
        points.append(
            models.PointStruct(id=point_id, vector=vector, payload=payload)
        )

    with _operation_lock:
        for start in range(0, len(points), UPSERT_BATCH_SIZE):
            client.upsert(
                collection_name=collection,
                points=points[start : start + UPSERT_BATCH_SIZE],
                wait=True,
            )
    _notify(progress, layer, 100, f"{layer} 完成 {len(points)} 个 point")
    return len(points), time.perf_counter() - started


def build_entity_indexes(
    db: Session,
    book_id: int,
    layer: str = "all",
    allow_partial: bool = False,
    batch_size: int | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    if layer not in LAYERS:
        raise BusinessException(
            message="layer 只支持 all / character / lore", code=4000
        )
    if not _build_lock.acquire(blocking=False):
        raise BusinessException(message="索引正在重建", code=4090)
    try:
        _book_or_404(db, book_id)
        counts = _counts(db, book_id)
        if counts["chapters_processed"] == 0:
            raise BusinessException(
                message="先生成章节结构化抽取", code=4000
            )
        if (
            counts["chapters_processed"] < counts["total_main"]
            and not allow_partial
        ):
            raise BusinessException(
                message=(
                    f"结构化抽取尚未覆盖全书（{counts['chapters_processed']}/"
                    f"{counts['total_main']}），请继续生成；"
                    "如果只想试跑，加 --allow-partial"
                ),
                code=4000,
            )

        requested_batch = max(
            1, int(batch_size or config.settings.EMBEDDING_BATCH_SIZE)
        )
        requested_layers = (
            ["character", "lore"] if layer == "all" else [layer]
        )
        character_rows = (
            _read_character_rows(db, book_id)
            if "character" in requested_layers
            else []
        )
        lore_rows = (
            _read_lore_rows(db, book_id) if "lore" in requested_layers else []
        )
        db.rollback()

        client = text_index_service.get_client()
        character_count = 0
        lore_count = 0
        character_seconds = 0.0
        lore_seconds = 0.0
        if "character" in requested_layers:
            character_count, character_seconds = _index_layer(
                client,
                "character",
                book_id,
                character_rows,
                requested_batch,
                progress,
            )
            entity_override_service.clear_dirty(db, book_id, "character")
        if "lore" in requested_layers:
            lore_count, lore_seconds = _index_layer(
                client,
                "lore",
                book_id,
                lore_rows,
                requested_batch,
                progress,
            )
            entity_override_service.clear_dirty(db, book_id, "lore")

        return {
            "book_id": book_id,
            "layer": layer,
            "character_count": character_count,
            "character_seconds": round(character_seconds, 3),
            "lore_count": lore_count,
            "lore_seconds": round(lore_seconds, 3),
            "complete": (
                counts["chapters_processed"] >= counts["total_main"]
            ),
            "collections": get_collection_names(),
            "qdrant_path": text_index_service.get_qdrant_path().as_posix(),
        }
    finally:
        _build_lock.release()


def _query_vectors(
    db: Session,
    book_id: int,
    layer: str,
    query: str,
    recall: int,
    chapter_idx: int | None = None,
) -> list[dict[str, Any]]:
    query = (query or "").strip()
    if not query:
        raise BusinessException(message="查询文本不能为空", code=4000)
    if recall <= 0:
        raise BusinessException(message="recall 必须大于 0", code=4000)
    if is_building():
        raise BusinessException(message="索引正在重建", code=4090)
    _book_or_404(db, book_id)

    client = text_index_service.get_client()
    collection = _collection_name(layer)
    with _operation_lock:
        if not client.collection_exists(collection):
            raise BusinessException(
                message="实体索引不存在，请先运行 build_entity_indexes.py。",
                code=4000,
            )
        total = client.count(collection_name=collection, exact=True).count
        if total == 0:
            raise BusinessException(
                message="实体索引为空，请先运行 build_entity_indexes.py。",
                code=4000,
            )

    query_doc = embedding_service.encode_query(query)
    sparse_indices, sparse_values = embedding_service.sparse_to_indices(
        query_doc.sparse, lambda _: None
    )
    conditions = [
        models.FieldCondition(
            key="book_id", match=models.MatchValue(value=book_id)
        ),
        models.FieldCondition(
            key=_version_key(layer),
            match=models.MatchValue(value=get_index_version(layer)),
        ),
    ]
    if chapter_idx is not None:
        conditions.append(
            models.FieldCondition(
                key="chapter_idx",
                range=models.Range(lte=int(chapter_idx)),
            )
        )
    query_filter = models.Filter(must=conditions)
    with _operation_lock:
        dense_hits = client.query_points(
            collection_name=collection,
            query=query_doc.dense,
            using=DENSE_VECTOR_NAME,
            query_filter=query_filter,
            limit=recall,
            with_payload=True,
        ).points
        sparse_hits = []
        if sparse_indices:
            sparse_hits = client.query_points(
                collection_name=collection,
                query=models.SparseVector(
                    indices=sparse_indices, values=sparse_values
                ),
                using=SPARSE_VECTOR_NAME,
                query_filter=query_filter,
                limit=recall,
                with_payload=True,
            ).points

    merged: dict[str, dict[str, Any]] = {}
    for rank, point in enumerate(dense_hits, start=1):
        item = merged.setdefault(
            str(point.id),
            {"point": point, "dense_score": 0.0, "sparse_score": 0.0, "rrf": 0.0},
        )
        item["dense_score"] = float(point.score)
        item["rrf"] += 1.0 / (RRF_K + rank)
    for rank, point in enumerate(sparse_hits, start=1):
        item = merged.setdefault(
            str(point.id),
            {"point": point, "dense_score": 0.0, "sparse_score": 0.0, "rrf": 0.0},
        )
        item["sparse_score"] = float(point.score)
        item["rrf"] += 1.0 / (RRF_K + rank)
    positive = [
        item
        for item in merged.values()
        if item["dense_score"] > 0 or item["sparse_score"] > 0
    ]
    return sorted(positive, key=lambda item: item["rrf"], reverse=True)


def search_character_changes(
    db: Session,
    book_id: int,
    query: str,
    chapter_idx: int,
    top_k: int = 5,
    recall: int = 20,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise BusinessException(message="top_k 必须大于 0", code=4000)
    if chapter_idx is None:
        chapter_idx = entity_layer_service._max_processed_chapter(
            db, book_id
        )
    ranked = _query_vectors(
        db, book_id, "character", query, recall, chapter_idx=int(chapter_idx)
    )
    selected: list[dict[str, Any]] = []
    seen_characters: set[int] = set()
    for item in ranked:
        payload = item["point"].payload or {}
        character_id = payload.get("character_id")
        if character_id in seen_characters:
            continue
        seen_characters.add(character_id)
        selected.append(item)
        if len(selected) >= top_k:
            break
    if not selected:
        return []

    delta_ids = [
        (item["point"].payload or {}).get("delta_id")
        for item in selected
    ]
    rows = (
        db.query(CharacterChapterDelta, Character)
        .join(Character, Character.id == CharacterChapterDelta.character_id)
        .filter(
            CharacterChapterDelta.book_id == book_id,
            CharacterChapterDelta.id.in_(delta_ids),
        )
        .all()
    )
    delta_map = {
        delta.id: (delta, character) for delta, character in rows
    }
    results = []
    for item in selected:
        payload = item["point"].payload or {}
        pair = delta_map.get(payload.get("delta_id"))
        if not pair:
            continue
        delta, character = pair
        results.append(
            {
                "character_id": character.id,
                "character": character.canonical_name,
                "chapter_idx": delta.chapter_idx,
                "delta_id": delta.id,
                "identity": delta.identity,
                "traits_add": list(delta.traits_add or []),
                "relations_add": list(delta.relations_add or []),
                "known_facts_add": list(delta.known_facts_add or []),
                "voice_sample": delta.voice_sample,
                "arc": delta.arc,
                "dense_score": round(item["dense_score"], 6),
                "sparse_score": round(item["sparse_score"], 6),
                "rrf_score": round(item["rrf"], 6),
            }
        )
    return results


def search_character_index(
    db: Session,
    book_id: int,
    query: str,
    top_k: int = 5,
    recall: int = 20,
    chapter_idx: int | None = None,
) -> list[dict[str, Any]]:
    """14 页面兼容：用增量命中，按指定章返回当时的完整卡。"""
    max_processed = entity_layer_service._max_processed_chapter(db, book_id)
    query_chapter = entity_override_service.clamp_chapter(
        chapter_idx, max_processed
    )
    hits = search_character_changes(
        db, book_id, query, query_chapter, top_k=top_k, recall=recall
    )
    if not hits:
        return []
    overrides = entity_override_service.list_character_overrides(
        db, book_id
    )
    character_ids = {hit["character_id"] for hit in hits}
    characters = {
        row.id: row
        for row in db.query(Character)
        .filter(Character.id.in_(character_ids))
        .all()
    }
    results = []
    for hit in hits:
        card = entity_layer_service.get_character_card(
            db, book_id, hit["character_id"], query_chapter
        )
        if not card.get("available"):
            continue
        base = {
            "identity": (card["identity"] or {}).get("text"),
            "traits": [item["text"] for item in card["traits"]],
            "relations": [item["text"] for item in card["relations"]],
            "known_facts": [item["text"] for item in card["known_facts"]],
            "voice_sample": (card["voice_sample"] or {}).get("text"),
            "arc": (card["arc"] or {}).get("text"),
        }
        character = characters[hit["character_id"]]
        override = overrides.get(character.canonical_name)
        override_from = (
            int(override.get("from_chapter") or 0) if override else None
        )
        effective = entity_override_service.apply_character_override(
            base, override, query_chapter
        )
        results.append(
            {
                "character_id": hit["character_id"],
                "character": hit["character"],
                "as_of_chapter": card["as_of_chapter"],
                "query_chapter": query_chapter,
                "first_seen_chapter": character.first_seen_chapter,
                "snapshot_id": hit["delta_id"],
                "delta_id": hit["delta_id"],
                "identity": effective["identity"],
                "traits": effective["traits"] or [],
                "relations": effective["relations"] or [],
                "known_facts": effective["known_facts"] or [],
                "voice_sample": effective["voice_sample"],
                "arc": effective["arc"],
                "edited": override is not None
                and override_from is not None
                and query_chapter >= override_from,
                "dense_score": hit["dense_score"],
                "sparse_score": hit["sparse_score"],
                "rrf_score": hit["rrf_score"],
            }
        )
    return results


def search_lore_index(
    db: Session,
    book_id: int,
    query: str,
    top_k: int = 5,
    recall: int = 20,
    chapter_idx: int | None = None,
) -> list[dict[str, Any]]:
    if top_k <= 0:
        raise BusinessException(message="top_k 必须大于 0", code=4000)
    max_processed = entity_layer_service._max_processed_chapter(db, book_id)
    effective = (
        max_processed
        if chapter_idx is None
        else max(0, min(int(chapter_idx), max_processed))
    )
    ranked = _query_vectors(
        db, book_id, "lore", query, recall, chapter_idx=effective
    )[:recall]
    if not ranked:
        return []
    lore_ids = [
        (item["point"].payload or {}).get("lore_id") for item in ranked
    ]
    facts = (
        db.query(LoreFact)
        .filter(
            LoreFact.book_id == book_id,
            LoreFact.id.in_(lore_ids),
        )
        .all()
    )
    fact_map = {fact.id: fact for fact in facts}
    order: list[str] = []
    scores: dict[str, dict[str, Any]] = {}
    for item in ranked:
        payload = item["point"].payload or {}
        fact = fact_map.get(payload.get("lore_id"))
        if not fact:
            continue
        name = fact.canonical_name
        if name not in scores:
            order.append(name)
            scores[name] = {
                "dense": item["dense_score"],
                "sparse": item["sparse_score"],
                "rrf": item["rrf"],
            }
    overrides = entity_override_service.list_lore_overrides(db, book_id)
    results = []
    for name in order:
        card = entity_layer_service.get_lore_card(
            db, book_id, name, effective
        )
        if not card["available"]:
            continue
        base = {
            field: card[field]
            for field in entity_override_service.LORE_FIELDS
        }
        override = overrides.get((name, card["as_of_chapter"]))
        merged = entity_override_service.apply_lore_override(base, override)
        score = scores[name]
        results.append(
            {
                "lore_id": card["lore_id"],
                "canonical_name": name,
                "category": merged["category"],
                "chapter_idx": card["as_of_chapter"],
                "aliases": merged["aliases"] or [],
                "summary": merged["summary"],
                "details": merged["details"] or [],
                "entities": merged["entities"] or [],
                "edited": override is not None,
                "as_of_chapter": card["as_of_chapter"],
                "occurrences": card["occurrences"],
                "query_chapter": card["query_chapter"],
                "dense_score": round(score["dense"], 6),
                "sparse_score": round(score["sparse"], 6),
                "rrf_score": round(score["rrf"], 6),
            }
        )
        if len(results) >= top_k:
            break
    return results
