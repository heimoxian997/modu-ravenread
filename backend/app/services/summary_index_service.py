"""章节摘要向量层（独立 novel_summary collection）。

只索引已有摘要，不生成摘要、不调用 LLM；摘要正文仍在 SQLite。
完整性门控默认拒绝部分索引，显式 allow_partial 才允许试跑。
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

from qdrant_client import QdrantClient, models
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app import config
from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_summary import ChapterSummary
from app.services import embedding_service, text_index_service

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
RRF_K = 60
ENCODE_GROUP_SIZE = 256
UPSERT_BATCH_SIZE = 128

_operation_lock = text_index_service._client_operation_lock
_build_lock = threading.Lock()


def is_building() -> bool:
    return _build_lock.locked()


def get_collection_name() -> str:
    return config.SUMMARY_INDEX_COLLECTION


def get_index_version() -> int:
    return config.SUMMARY_INDEX_VERSION


def _notify_progress(
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


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _book_filter(book_id: int, index_version: int | None = None) -> models.Filter:
    version = get_index_version() if index_version is None else index_version
    return models.Filter(
        must=[
            models.FieldCondition(
                key="book_id", match=models.MatchValue(value=book_id)
            ),
            models.FieldCondition(
                key="index_version", match=models.MatchValue(value=version)
            ),
        ]
    )


def _point_id(book_id: int, chapter_idx: int) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"modu:summary:{book_id}:{chapter_idx}",
        )
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
                    f"当前摘要 collection dense 维度为 {current_dim}，"
                    f"本次嵌入维度为 {dim}。"
                )
            )
        sparse_vectors = info.config.params.sparse_vectors or {}
        if SPARSE_VECTOR_NAME not in sparse_vectors:
            raise BusinessException(
                message=(
                    "摘要 collection 缺少 sparse 向量配置，"
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


def _summary_query(db: Session, book_id: int):
    return (
        db.query(ChapterSummary, Chapter)
        .join(
            Chapter,
            and_(
                Chapter.book_id == ChapterSummary.book_id,
                Chapter.idx == ChapterSummary.chapter_idx,
            ),
        )
        .filter(
            ChapterSummary.book_id == book_id,
            Chapter.kind == "main",
        )
    )


def _counts(db: Session, book_id: int) -> tuple[int, int]:
    total_main = (
        db.query(func.count(Chapter.id))
        .filter(Chapter.book_id == book_id, Chapter.kind == "main")
        .scalar()
        or 0
    )
    summary_count = _summary_query(db, book_id).count()
    return int(total_main), int(summary_count)


def _summary_text(summary: ChapterSummary, chapter: Chapter) -> str:
    key_events = "；".join(str(item) for item in (summary.key_events or []))
    characters = "、".join(str(item) for item in (summary.characters or []))
    return (
        f"【第{chapter.idx + 1}章 {chapter.title}】\n"
        f"摘要：{summary.summary or ''}\n"
        f"关键事件：{key_events}\n"
        f"人物：{characters}\n"
        f"章末钩子：{summary.cliffhanger or ''}"
    )


def get_summary_index_status(db: Session, book_id: int) -> dict[str, Any]:
    """摘要完整度 + 当前版本 point 数；不创建 collection。"""
    _book_or_404(db, book_id)
    total_main, summary_count = _counts(db, book_id)

    client = text_index_service.get_client()
    collection_name = get_collection_name()
    with _operation_lock:
        exists = client.collection_exists(collection_name)
        if exists:
            info = client.get_collection(collection_name)
            dim = _collection_dense_dim(info) or embedding_service.DEFAULT_DIM
            indexed_count = client.count(
                collection_name=collection_name,
                count_filter=_book_filter(book_id),
                exact=True,
            ).count
        else:
            dim = embedding_service.DEFAULT_DIM
            indexed_count = 0

    return {
        "total_main": total_main,
        "summary_count": summary_count,
        "indexed_count": int(indexed_count),
        "complete": summary_count >= total_main,
        "needs_rebuild": int(indexed_count) != summary_count,
        "collection": collection_name,
        "dim": int(dim),
    }


def build_summary_index(
    db: Session,
    book_id: int,
    batch_size: int | None = None,
    allow_partial: bool = False,
    progress: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    """同步重建入口，带非阻塞进程内锁。"""
    if not _build_lock.acquire(blocking=False):
        raise BusinessException(
            message="摘要索引正在重建", code=4090
        )
    try:
        return _build_summary_index_locked(
            db,
            book_id,
            batch_size=batch_size,
            allow_partial=allow_partial,
            progress=progress,
        )
    finally:
        _build_lock.release()


def _build_summary_index_locked(
    db: Session,
    book_id: int,
    batch_size: int | None = None,
    allow_partial: bool = False,
    progress: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    """按书全量重建摘要向量；编码全部成功后才删旧点。"""
    started = time.perf_counter()
    _book_or_404(db, book_id)
    total_main, summary_count = _counts(db, book_id)
    if summary_count == 0:
        raise BusinessException(
            message="还没有摘要，先生成章节摘要", code=4000
        )
    if summary_count < total_main and not allow_partial:
        raise BusinessException(
            message=(
                f"摘要尚未完整（{summary_count}/{total_main}），请继续生成；"
                "如果只想试跑，加 --allow-partial"
            ),
            code=4000,
        )

    rows = _summary_query(db, book_id).order_by(Chapter.idx.asc()).all()
    source_rows = [
        {
            "summary_id": summary.id,
            "chapter_idx": chapter.idx,
            "chapter_title": chapter.title,
            "summary": summary.summary,
            "key_events": list(summary.key_events or []),
            "characters": list(summary.characters or []),
            "cliffhanger": summary.cliffhanger or "",
            "text": _summary_text(summary, chapter),
        }
        for summary, chapter in rows
    ]
    db.rollback()

    if len(source_rows) != summary_count:
        raise BusinessException(message="摘要读取数量不一致，已停止建库。")
    if summary_count < total_main and allow_partial:
        _notify_progress(
            progress,
            "preparing",
            0,
            f"部分索引（{summary_count}/{total_main}），只覆盖已生成章节",
        )
    else:
        _notify_progress(progress, "preparing", 0, "准备摘要索引")

    requested_batch = max(
        1, int(batch_size or config.settings.EMBEDDING_BATCH_SIZE)
    )
    texts = [row["text"] for row in source_rows]

    encode_started = time.perf_counter()
    docs: list[embedding_service.EmbeddedDoc] = []
    for start in range(0, len(texts), ENCODE_GROUP_SIZE):
        group = texts[start : start + ENCODE_GROUP_SIZE]
        docs.extend(
            embedding_service.encode_documents(
                group, batch_size=requested_batch
            )
        )
        processed = min(start + len(group), len(texts))
        _notify_progress(
            progress,
            "encoding",
            5 + int(65 * processed / len(texts)),
            f"编码摘要 {processed}/{len(texts)}",
        )
    if len(docs) != len(source_rows):
        raise BusinessException(message="嵌入返回数量与摘要数量不一致，已停止建库。")
    encode_seconds = time.perf_counter() - encode_started
    if not docs:
        raise BusinessException(message="没有可编码的摘要。")
    dim = len(docs[0].dense)

    client = text_index_service.get_client()
    collection_name = get_collection_name()
    index_version = get_index_version()
    with _operation_lock:
        _ensure_collection(client, collection_name, dim)

    points: list[models.PointStruct] = []
    for row, doc in zip(source_rows, docs):
        indices, values = embedding_service.sparse_to_indices(
            doc.sparse, lambda _: None
        )
        vector: dict[str, Any] = {DENSE_VECTOR_NAME: doc.dense}
        if indices:
            vector[SPARSE_VECTOR_NAME] = models.SparseVector(
                indices=indices, values=values
            )
        points.append(
            models.PointStruct(
                id=_point_id(book_id, row["chapter_idx"]),
                vector=vector,
                payload={
                    "book_id": book_id,
                    "index_version": index_version,
                    "summary_id": row["summary_id"],
                    "chapter_idx": row["chapter_idx"],
                    "chapter_title": row["chapter_title"],
                },
            )
        )

    with _operation_lock:
        client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=_book_filter(book_id, index_version)
            ),
            wait=True,
        )
    _notify_progress(progress, "upserting", 72, "旧摘要向量已清理")

    upsert_started = time.perf_counter()
    batch_total = (len(points) + UPSERT_BATCH_SIZE - 1) // UPSERT_BATCH_SIZE
    for batch_index, start in enumerate(
        range(0, len(points), UPSERT_BATCH_SIZE), start=1
    ):
        with _operation_lock:
            client.upsert(
                collection_name=collection_name,
                points=points[start : start + UPSERT_BATCH_SIZE],
                wait=True,
            )
        _notify_progress(
            progress,
            "upserting",
            72 + int(26 * batch_index / max(1, batch_total)),
            f"写入 {min(start + UPSERT_BATCH_SIZE, len(points))}/"
            f"{len(points)} 个摘要向量",
        )
    upsert_seconds = time.perf_counter() - upsert_started
    total_seconds = time.perf_counter() - started

    last_run = embedding_service.get_last_run_info()
    loaded = embedding_service.get_loaded_model_info()
    model = config.settings.EMBEDDING_MODEL
    device = (
        last_run.get("device")
        or loaded.get("device")
        or config.settings.EMBEDDING_DEVICE
    )
    _notify_progress(progress, "done", 100, "摘要索引完成")

    return {
        "book_id": book_id,
        "summary_count": summary_count,
        "total_main": total_main,
        "indexed_count": len(points),
        "complete": summary_count >= total_main,
        "dim": dim,
        "batch_size": requested_batch,
        "load_seconds": 0.0,
        "encode_seconds": round(encode_seconds, 3),
        "upsert_seconds": round(upsert_seconds, 3),
        "total_seconds": round(total_seconds, 3),
        "collection": collection_name,
        "qdrant_path": text_index_service.get_qdrant_path().as_posix(),
        "model": model,
        "device": device,
    }


def search_summary_index(
    db: Session,
    book_id: int,
    query: str,
    top_k: int = 5,
    recall: int = 20,
) -> list[dict[str, Any]]:
    """dense + sparse 各召回 recall 条，RRF 融合后按 summary_id 回表。"""
    query = (query or "").strip()
    if not query:
        raise BusinessException(message="查询文本不能为空", code=4000)
    if top_k <= 0 or recall <= 0:
        raise BusinessException(message="top_k 和 recall 必须大于 0", code=4000)
    if is_building():
        raise BusinessException(message="索引正在重建", code=4090)
    _book_or_404(db, book_id)

    client = text_index_service.get_client()
    collection_name = get_collection_name()
    with _operation_lock:
        if not client.collection_exists(collection_name):
            raise BusinessException(
                message="摘要索引不存在，请先重建摘要索引。", code=4000
            )
        total_count = client.count(
            collection_name=collection_name, exact=True
        ).count
        if total_count == 0:
            raise BusinessException(
                message="摘要索引为空，请先重建摘要索引。", code=4000
            )

    query_doc = embedding_service.encode_query(query)
    sparse_indices, sparse_values = embedding_service.sparse_to_indices(
        query_doc.sparse, lambda _: None
    )
    query_filter = _book_filter(book_id)

    with _operation_lock:
        dense_hits = client.query_points(
            collection_name=collection_name,
            query=query_doc.dense,
            using=DENSE_VECTOR_NAME,
            query_filter=query_filter,
            limit=recall,
            with_payload=True,
        ).points
        sparse_hits = []
        if sparse_indices:
            sparse_hits = client.query_points(
                collection_name=collection_name,
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
        key = str(point.id)
        item = merged.setdefault(
            key,
            {
                "point": point,
                "dense_score": 0.0,
                "sparse_score": 0.0,
                "rrf_score": 0.0,
            },
        )
        item["dense_score"] = float(point.score)
        item["rrf_score"] += 1.0 / (RRF_K + rank)
    for rank, point in enumerate(sparse_hits, start=1):
        key = str(point.id)
        item = merged.setdefault(
            key,
            {
                "point": point,
                "dense_score": 0.0,
                "sparse_score": 0.0,
                "rrf_score": 0.0,
            },
        )
        item["sparse_score"] = float(point.score)
        item["rrf_score"] += 1.0 / (RRF_K + rank)

    ranked = sorted(
        merged.values(), key=lambda item: item["rrf_score"], reverse=True
    )[:top_k]
    summary_ids = [
        payload["summary_id"]
        for item in ranked
        if isinstance(
            (payload := (item["point"].payload or {})).get("summary_id"), int
        )
    ]
    summaries = (
        db.query(ChapterSummary)
        .filter(
            ChapterSummary.book_id == book_id,
            ChapterSummary.id.in_(summary_ids),
        )
        .all()
        if summary_ids
        else []
    )
    summary_map = {summary.id: summary for summary in summaries}

    results: list[dict[str, Any]] = []
    for item in ranked:
        payload = item["point"].payload or {}
        summary = summary_map.get(payload.get("summary_id"))
        if not summary:
            continue
        results.append(
            {
                "summary_id": summary.id,
                "chapter_idx": summary.chapter_idx,
                "chapter_title": payload.get("chapter_title")
                or summary.title,
                "summary": summary.summary,
                "key_events": list(summary.key_events or []),
                "characters": list(summary.characters or []),
                "cliffhanger": summary.cliffhanger or "",
                "dense_score": round(item["dense_score"], 6),
                "sparse_score": round(item["sparse_score"], 6),
                "rrf_score": round(item["rrf_score"], 6),
            }
        )
    return results


def _recall(db: Session, book_id: int, query: str, recall: int) -> list[dict[str, Any]]:
    """RRF 融合后的全部候选，按融合分降序，不截断。"""
    return search_summary_index(db, book_id, query, top_k=recall, recall=recall)


def search_summary_index_compare(
    db: Session,
    book_id: int,
    query: str,
    top_k: int = 5,
    recall: int = 20,
) -> dict[str, Any]:
    """同一批候选，给出 RRF 与重排两套排序，便于并列对比。

    重排只对 RRF 前 recall 条打分：再往后拉收益递减，延迟却线性上涨。
    模型缺失或打分失败时降级为 RRF 结果，接口不报错。
    """
    items = _recall(db, book_id, query, max(top_k, recall))
    rrf_items = items[:top_k]
    pool = items[:recall]
    result: dict[str, Any] = {
        "query": query,
        "recall": len(pool),
        "rrf_items": rrf_items,
        "rerank_items": [],
        "rerank_applied": False,
        "rerank_error": None,
        "rerank_latency_ms": 0,
    }
    if not pool:
        return result

    from app.services import rerank_service

    scores, error = rerank_service.rerank(
        query, [item["summary"] for item in pool]
    )
    if scores is None or len(scores) != len(pool):
        result["rerank_error"] = error or "重排服务未返回完整分数"
        result["rerank_items"] = rrf_items
        return result

    ranked = sorted(
        zip(pool, scores), key=lambda pair: pair[1], reverse=True
    )
    rrf_rank = {
        item["summary_id"]: index for index, item in enumerate(pool, start=1)
    }
    rerank_items: list[dict[str, Any]] = []
    for index, (item, score) in enumerate(ranked[:top_k], start=1):
        previous = rrf_rank.get(item["summary_id"], 0)
        rerank_items.append(
            {
                **item,
                "rerank_score": round(float(score), 6),
                "rrf_rank": previous,
                "rank_delta": previous - index,
            }
        )
    result["rerank_items"] = rerank_items
    result["rerank_applied"] = True
    result["rerank_latency_ms"] = rerank_service.get_last_run_info().get(
        "latency_ms", 0
    )
    return result
