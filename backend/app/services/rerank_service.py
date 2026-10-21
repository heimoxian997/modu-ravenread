"""本地 cross-encoder 重排（bge-reranker-v2-m3）。

原则和 embedding_service 一致：
- 模块导入时不 import torch / FlagEmbedding；
- 状态查询不加载模型；
- 首次打分才懒加载，加载失败不抛给调用方，由检索侧降级回 RRF。

重排的输入是 (query, 文档) 成对打分，能把「字面像但意思不对」的候选压下去，
这是双塔向量召回做不到的。
"""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path
from typing import Any

from app import config

REQUIRED_FILES = ("config.json", "model.safetensors")
MAX_PASSAGE_CHARS = 512
MAX_BATCH = 32

_reranker = None
_rerank_lock = threading.Lock()
_loaded_path: Path | None = None
_loaded_device: str | None = None
_last_error: str | None = None
_last_run_info: dict[str, Any] = {}


def get_model_dir() -> Path:
    return Path(config.RERANK_MODEL_DIR)


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def is_model_complete(path: Path | None = None) -> bool:
    target = path or get_model_dir()
    return all((target / name).exists() for name in REQUIRED_FILES)


def is_available() -> bool:
    return is_model_complete() and _module_available("FlagEmbedding")


def _resolve_device() -> str:
    try:
        from app.services import embedding_service

        return embedding_service._resolve_device()  # noqa: SLF001
    except Exception:
        return "cpu"


def get_status() -> dict[str, Any]:
    path = get_model_dir()
    return {
        "path": path.as_posix(),
        "downloaded": is_model_complete(path),
        "available": is_available(),
        "loaded": _reranker is not None,
        "device": _loaded_device or _resolve_device(),
        "last_error": _last_error,
    }


def _load(path: Path):
    """加载重排模型；失败抛异常，由 rerank 捕获成可读错误。"""
    global _reranker, _loaded_path, _loaded_device, _last_error
    with _rerank_lock:
        if _reranker is not None and _loaded_path == path:
            return _reranker
        from FlagEmbedding import FlagReranker

        device = _resolve_device()
        use_fp16 = device == "cuda"
        _reranker = FlagReranker(
            path.as_posix(),
            use_fp16=use_fp16,
            device=device,
            query_max_length=256,
            passage_max_length=MAX_PASSAGE_CHARS,
        )
        _loaded_path = path
        _loaded_device = device
        _last_error = None
        return _reranker


def rerank(
    query: str, documents: list[str], batch_size: int = MAX_BATCH
) -> tuple[list[float] | None, str | None]:
    """成对打分。返回 (每个文档的分数, 错误信息)；出错时分数为 None。"""
    global _last_error, _last_run_info
    query = (query or "").strip()
    if not query or not documents:
        return [], None
    path = get_model_dir()
    if not is_model_complete(path):
        _last_error = f"重排模型缺失：{path.as_posix()}"
        return None, _last_error
    if not _module_available("FlagEmbedding"):
        _last_error = "未安装 FlagEmbedding，无法重排"
        return None, _last_error

    started = time.perf_counter()
    try:
        model = _load(path)
    except Exception as exc:  # 模型损坏、显存不足等
        _last_error = f"重排模型加载失败：{exc}"
        return None, _last_error

    scores: list[float] = []
    try:
        for start in range(0, len(documents), max(1, batch_size)):
            batch = documents[start : start + batch_size]
            pairs = [
                [query, (doc or "")[:MAX_PASSAGE_CHARS]] for doc in batch
            ]
            raw = model.compute_score(pairs, normalize=True)
            if isinstance(raw, (int, float)):
                raw = [raw]
            scores.extend(float(value) for value in raw)
    except Exception as exc:
        _last_error = f"重排打分失败：{exc}"
        return None, _last_error

    _last_error = None
    _last_run_info = {
        "query_chars": len(query),
        "documents": len(documents),
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "device": _loaded_device,
    }
    return scores, None


def compare_items(
    query: str,
    items: list[dict[str, Any]],
    documents: list[str],
    top_k: int,
    key: str,
) -> dict[str, Any]:
    """给同一批候选算出两套排序，供页面左右对比。

    返回 {"items": 重排后的前 top_k, "applied": bool, "error": str|None,
    "latency_ms": int}。模型缺失或打分失败时 applied=False 且 items 退回原序，
    调用方不需要处理异常。
    """
    if not items:
        return {"items": [], "applied": False, "error": None, "latency_ms": 0}

    scores, error = rerank(query, documents)
    if scores is None or len(scores) != len(items):
        return {
            "items": items[:top_k],
            "applied": False,
            "error": error or "重排服务未返回完整分数",
            "latency_ms": 0,
        }

    ranked = sorted(
        zip(items, scores), key=lambda pair: pair[1], reverse=True
    )
    rrf_rank = {
        item.get(key): index for index, item in enumerate(items, start=1)
    }
    result: list[dict[str, Any]] = []
    for index, (item, score) in enumerate(ranked[:top_k], start=1):
        previous = rrf_rank.get(item.get(key), 0)
        result.append(
            {
                **item,
                "rerank_score": round(float(score), 6),
                "rrf_rank": previous,
                "rank_delta": previous - index,
            }
        )
    return {
        "items": result,
        "applied": True,
        "error": None,
        "latency_ms": get_last_run_info().get("latency_ms", 0),
    }


def get_last_run_info() -> dict[str, Any]:
    return dict(_last_run_info)


def reset() -> None:
    """测试用：清掉已加载的模型句柄。"""
    global _reranker, _loaded_path, _loaded_device, _last_error
    with _rerank_lock:
        _reranker = None
        _loaded_path = None
        _loaded_device = None
        _last_error = None
