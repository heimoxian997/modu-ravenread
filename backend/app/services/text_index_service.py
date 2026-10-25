"""本地 Qdrant 文本向量层。

只服务子块（chunks.level='child'），正文不写入 payload；检索命中后按
chunk_id 回 SQLite 取子块、父块和章节标题。建库与查询的 sparse 映射
统一使用 CRC32，避免两套 token/id 映射不一致。
"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from qdrant_client import QdrantClient, models
from sqlalchemy.orm import Session

from app import config
from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chunk import Chunk
from app.services import embedding_service

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"
RRF_K = 60
UPSERT_BATCH_SIZE = 128
ENCODE_GROUP_SIZE = 256

_client: QdrantClient | None = None
_client_lock = threading.Lock()
# Qdrant 本地客户端不是线程安全的；upsert、count、query 必须串行执行。
_client_operation_lock = threading.RLock()


def get_collection_name() -> str:
    return config.TEXT_INDEX_COLLECTION


def get_index_version() -> int:
    return config.TEXT_INDEX_VERSION


def get_qdrant_path() -> Path:
    return Path(config.QDRANT_PATH)


def reset_client() -> None:
    """关闭并释放本地 Qdrant 客户端，测试和脚本退出时使用。"""
    global _client
    with _client_operation_lock:
        with _client_lock:
            if _client is not None:
                try:
                    _client.close()
                except Exception:
                    pass
                _client = None


def get_client() -> QdrantClient:
    """本地 Qdrant 客户端单例。"""
    global _client
    with _client_lock:
        if _client is not None:
            return _client

        path = get_qdrant_path()
        try:
            path.mkdir(parents=True, exist_ok=True)
            _client = QdrantClient(path=str(path))
        except Exception as exc:
            raise BusinessException(
                message=(
                    "无法打开本地 Qdrant。请确认后端没有同时占用 data/qdrant，"
                    "本地 Qdrant 同一时间只允许一个进程访问。"
                )
            ) from exc
        return _client


def _book_filter(
    book_id: int,
    index_version: int | None = None,
    chapter_idx: int | None = None,
    chapter_from: int | None = None,
) -> models.Filter:
    version = get_index_version() if index_version is None else index_version
    must = [
        models.FieldCondition(
            key="book_id", match=models.MatchValue(value=book_id)
        ),
        models.FieldCondition(
            key="index_version", match=models.MatchValue(value=version)
        ),
    ]
    if chapter_idx is not None or chapter_from is not None:
        must.append(
            models.FieldCondition(
                key="chapter_idx",
                range=models.Range(lte=chapter_idx, gte=chapter_from),
            )
        )
    return models.Filter(must=must)


def _point_id(book_id: int, chunk_id: int) -> str:
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"modu:text:{book_id}:{chunk_id}")
    )


def _collection_dense_dim(info: models.CollectionInfo) -> int | None:
    vectors = info.config.params.vectors
    if isinstance(vectors, dict):
        dense = vectors.get(DENSE_VECTOR_NAME)
        return int(dense.size) if dense is not None else None
    # 兼容未命名向量集合；本项目正常应使用命名 dense。
    return int(vectors.size) if vectors is not None else None


def _ensure_collection(client: QdrantClient, collection_name: str, dim: int) -> None:
    if client.collection_exists(collection_name):
        info = client.get_collection(collection_name)
        current_dim = _collection_dense_dim(info)
        if current_dim != dim:
            raise BusinessException(
                message=(
                    "嵌入模型已变化，请删除 data/qdrant 后重建。"
                    f"当前 collection dense 维度为 {current_dim}，本次嵌入维度为 {dim}。"
                )
            )
        sparse_vectors = info.config.params.sparse_vectors or {}
        if SPARSE_VECTOR_NAME not in sparse_vectors:
            raise BusinessException(
                message=(
                    "Qdrant collection 缺少 sparse 向量配置，"
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


def _embedding_chunks(db: Session, book_id: int) -> list[dict[str, Any]]:
    rows = (
        db.query(Chunk)
        .filter(
            Chunk.book_id == book_id,
            Chunk.version == get_index_version(),
            Chunk.level == "child",
        )
        .order_by(Chunk.chapter_idx.asc(), Chunk.seq.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "text": row.text,
            "parent_id": row.parent_id,
            "chapter_idx": row.chapter_idx,
            "char_start": row.char_start,
            "char_end": row.char_end,
        }
        for row in rows
    ]


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
        # 进度写库失败不能中断编码/写库。
        pass


def build_text_index(
    db: Session,
    book_id: int,
    batch_size: int | None = None,
    progress: Callable[[str, int, str], None] | None = None,
) -> dict[str, Any]:
    """把一本书的子块编码后写入本地 Qdrant。

    读取完 chunk 后立即 rollback，编码的几十秒内不持有 SQLite 长读事务，
    这样任务线程可以通过独立 Session 持续写进度。
    """
    total_started = time.perf_counter()
    _notify_progress(progress, "preparing", 0, "准备建库")

    try:
        book = db.get(Book, book_id)
        if not book:
            raise BusinessException(message="书籍不存在或已被删除", code=4040)
        chunks = _embedding_chunks(db, book_id)
        if not chunks:
            raise BusinessException(
                message="这本书还没有子块，请先完成正文切块后再建文本索引。"
            )
    finally:
        db.rollback()

    _notify_progress(progress, "preparing", 5, f"准备完成，共 {len(chunks)} 个子块")
    requested_batch = max(
        1,
        int(batch_size or config.settings.EMBEDDING_BATCH_SIZE),
    )
    texts = [chunk["text"] for chunk in chunks]

    _notify_progress(progress, "loading", 6, "检查本地嵌入模型")
    status = embedding_service.get_status()
    load_started = time.perf_counter()
    embedding_service.ensure_model(
        progress=lambda message: _notify_progress(
            progress, "loading", 8, f"模型准备：{message}"
        )
    )
    embedding_service.get_embedder(
        progress=lambda message: _notify_progress(
            progress, "loading", 12, f"模型加载：{message}"
        )
    )
    load_seconds = time.perf_counter() - load_started
    _notify_progress(progress, "loading", 15, "嵌入模型已加载")

    encode_started = time.perf_counter()
    docs: list[embedding_service.EmbeddedDoc] = []
    for start in range(0, len(texts), ENCODE_GROUP_SIZE):
        group = texts[start : start + ENCODE_GROUP_SIZE]
        docs.extend(
            embedding_service.encode_documents(
                group,
                batch_size=requested_batch,
            )
        )
        processed = min(start + len(group), len(texts))
        percent = 15 + int(60 * processed / len(texts))
        _notify_progress(
            progress,
            "encoding",
            percent,
            f"编码 {processed}/{len(texts)} 个子块",
        )

    if len(docs) != len(chunks):
        raise BusinessException(message="嵌入返回数量与子块数量不一致，已停止建库。")
    encode_seconds = time.perf_counter() - encode_started
    _notify_progress(progress, "encoding", 75, "子块编码完成")

    dim = len(docs[0].dense)
    client = get_client()
    collection_name = get_collection_name()
    index_version = get_index_version()
    _notify_progress(progress, "upserting", 75, "准备写入 Qdrant")
    with _client_operation_lock:
        _ensure_collection(client, collection_name, dim)

    points: list[models.PointStruct] = []
    for chunk, doc in zip(chunks, docs):
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
                id=_point_id(book_id, chunk["id"]),
                vector=vector,
                payload={
                    "book_id": book_id,
                    "index_version": index_version,
                    "chunk_id": chunk["id"],
                    "parent_id": chunk["parent_id"],
                    "chapter_idx": chunk["chapter_idx"],
                    "char_start": chunk["char_start"],
                    "char_end": chunk["char_end"],
                },
            )
        )

    # 编码和 point 构造成功后才删旧点，避免失败时清空已有索引。
    with _client_operation_lock:
        client.delete(
            collection_name=collection_name,
            points_selector=models.FilterSelector(
                filter=_book_filter(book_id, index_version)
            ),
            wait=True,
        )
    _notify_progress(progress, "upserting", 76, "旧向量已清理")

    upsert_started = time.perf_counter()
    upsert_batches = (len(points) + UPSERT_BATCH_SIZE - 1) // UPSERT_BATCH_SIZE
    for batch_index, start in enumerate(
        range(0, len(points), UPSERT_BATCH_SIZE), start=1
    ):
        with _client_operation_lock:
            client.upsert(
                collection_name=collection_name,
                points=points[start : start + UPSERT_BATCH_SIZE],
                wait=True,
            )
        percent = 76 + int(22 * batch_index / max(1, upsert_batches))
        _notify_progress(
            progress,
            "upserting",
            min(percent, 98),
            f"写入 {min(start + UPSERT_BATCH_SIZE, len(points))}/{len(points)} 个向量",
        )
    upsert_seconds = time.perf_counter() - upsert_started
    total_seconds = time.perf_counter() - total_started

    return {
        "book_id": book_id,
        "point_count": len(points),
        "dim": dim,
        "batch_size": requested_batch,
        "load_seconds": round(load_seconds, 3),
        "encode_seconds": round(encode_seconds, 3),
        "upsert_seconds": round(upsert_seconds, 3),
        "total_seconds": round(total_seconds, 3),
        "collection": collection_name,
        "qdrant_path": get_qdrant_path().as_posix(),
        "model": status.get("model"),
        "device": status.get("device"),
    }


def get_index_stats(book_id: int) -> dict[str, Any]:
    """查询一本书的本地 Qdrant 索引统计。"""
    client = get_client()
    collection_name = get_collection_name()
    with _client_operation_lock:
        if not client.collection_exists(collection_name):
            return {
                "available": True,
                "exists": False,
                "point_count": 0,
                "dim": 0,
            }

        info = client.get_collection(collection_name)
        dim = _collection_dense_dim(info) or 0
        point_count = client.count(
            collection_name=collection_name,
            count_filter=_book_filter(book_id),
            exact=True,
        ).count
    return {
        "available": True,
        "exists": True,
        "point_count": int(point_count),
        "dim": dim,
    }


def delete_book_index(book_id: int) -> int:
    """删除某本书当前索引版本的 points，返回删除数量。"""
    client = get_client()
    collection_name = get_collection_name()
    with _client_operation_lock:
        if not client.collection_exists(collection_name):
            return 0

        query_filter = _book_filter(book_id)
        count = client.count(
            collection_name=collection_name,
            count_filter=query_filter,
            exact=True,
        ).count
        if count:
            client.delete(
                collection_name=collection_name,
                points_selector=models.FilterSelector(filter=query_filter),
                wait=True,
            )
    return int(count)


def _fetch_result_texts(
    db: Session, book_id: int, chunk_ids: list[int]
) -> tuple[dict[int, Chunk], dict[int, Chunk], dict[int, Chapter]]:
    chunks = (
        db.query(Chunk)
        .filter(Chunk.book_id == book_id, Chunk.id.in_(chunk_ids))
        .all()
    )
    chunk_map = {chunk.id: chunk for chunk in chunks}
    parent_ids = {
        chunk.parent_id for chunk in chunks if chunk.parent_id is not None
    }
    parents = (
        db.query(Chunk)
        .filter(Chunk.book_id == book_id, Chunk.id.in_(parent_ids))
        .all()
        if parent_ids
        else []
    )
    parent_map = {parent.id: parent for parent in parents}
    chapter_indexes = {chunk.chapter_idx for chunk in chunks}
    chapters = (
        db.query(Chapter)
        .filter(
            Chapter.book_id == book_id,
            Chapter.idx.in_(chapter_indexes),
        )
        .all()
        if chapter_indexes
        else []
    )
    chapter_map = {chapter.idx: chapter for chapter in chapters}
    return chunk_map, parent_map, chapter_map


def search_text_index(
    db: Session,
    book_id: int,
    query: str,
    top_k: int = 5,
    recall: int = 20,
    chapter_idx: int | None = None,
    chapter_from: int | None = None,
) -> list[dict[str, Any]]:
    """dense + sparse 各召回 recall 条，RRF 融合后返回 top_k。"""
    query = (query or "").strip()
    if not query:
        raise BusinessException(message="查询文本不能为空")
    if top_k <= 0 or recall <= 0:
        raise BusinessException(message="top_k 和 recall 必须大于 0")

    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)

    client = get_client()
    collection_name = get_collection_name()

    # 本地 Qdrant 的 count/query 不能与建库 upsert 并发调用。
    with _client_operation_lock:
        if not client.collection_exists(collection_name):
            raise BusinessException(
                message="文本索引不存在，请先运行 scripts/build_text_index.py。"
            )
        total_count = client.count(
            collection_name=collection_name, exact=True
        ).count
        if total_count == 0:
            raise BusinessException(
                message="文本索引为空，请先运行 scripts/build_text_index.py。"
            )

    query_doc = embedding_service.encode_query(query)
    sparse_indices, sparse_values = embedding_service.sparse_to_indices(
        query_doc.sparse, lambda _: None
    )
    query_filter = _book_filter(book_id, chapter_idx=chapter_idx, chapter_from=chapter_from)

    with _client_operation_lock:
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
        merged.values(),
        key=lambda item: item["rrf_score"],
        reverse=True,
    )[:top_k]

    chunk_ids = []
    for item in ranked:
        payload = item["point"].payload or {}
        chunk_id = payload.get("chunk_id")
        if isinstance(chunk_id, int):
            chunk_ids.append(chunk_id)
    if not chunk_ids:
        return []

    chunk_map, parent_map, chapter_map = _fetch_result_texts(
        db, book_id, chunk_ids
    )
    results: list[dict[str, Any]] = []
    for item in ranked:
        payload = item["point"].payload or {}
        chunk_id = payload.get("chunk_id")
        chunk = chunk_map.get(chunk_id)
        if not chunk:
            continue
        parent = parent_map.get(chunk.parent_id) if chunk.parent_id else None
        chapter = chapter_map.get(chunk.chapter_idx)
        results.append(
            {
                "chunk_id": chunk.id,
                "parent_id": chunk.parent_id,
                "chapter_idx": chunk.chapter_idx,
                "chapter_title": chapter.title if chapter else "",
                "text": chunk.text,
                "parent_text": parent.text if parent else "",
                "parent_char_start": (
                    parent.char_start if parent else chunk.char_start
                ),
                "parent_char_end": (
                    parent.char_end if parent else chunk.char_end
                ),
                "char_start": chunk.char_start,
                "char_end": chunk.char_end,
                "dense_score": round(item["dense_score"], 6),
                "sparse_score": round(item["sparse_score"], 6),
                "rrf_score": round(item["rrf_score"], 6),
            }
        )
    return results


def _text_document(item: dict[str, Any]) -> str:
    """重排用的文档文本：章节标题 + 子块原文。"""
    title = str(item.get("chapter_title") or "").strip()
    text = str(item.get("text") or "").strip()
    return f"{title}｜{text}" if title else text


def search_text_index_compare(
    db: Session,
    book_id: int,
    query: str,
    top_k: int = 5,
    recall: int = 20,
    chapter_idx: int | None = None,
) -> dict[str, Any]:
    """同一批正文候选，给出 RRF 与重排两套排序。

    重排只对 RRF 前 recall 条打分；模型不可用时降级为 RRF，不抛错。
    """
    pool = search_text_index(
        db,
        book_id,
        query,
        top_k=max(top_k, recall),
        recall=recall,
        **({"chapter_idx": chapter_idx} if chapter_idx is not None else {}),
    )
    result: dict[str, Any] = {
        "query": query,
        "recall": len(pool),
        "rrf_items": pool[:top_k],
        "rerank_items": [],
        "rerank_applied": False,
        "rerank_error": None,
        "rerank_latency_ms": 0,
    }
    if not pool:
        return result

    from app.services import rerank_service

    outcome = rerank_service.compare_items(
        query,
        pool,
        [_text_document(item) for item in pool],
        top_k,
        "chunk_id",
    )
    result["rerank_items"] = outcome["items"]
    result["rerank_applied"] = outcome["applied"]
    result["rerank_error"] = outcome["error"]
    result["rerank_latency_ms"] = outcome["latency_ms"]
    return result
