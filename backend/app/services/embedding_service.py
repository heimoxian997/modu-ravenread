"""本地 BGE-M3 嵌入服务。

原则：
- 模块导入时不 import torch / FlagEmbedding / numpy；
- 状态查询不加载模型；
- 首次编码时才校验/下载权重并懒加载模型；
- 只提供 dense + sparse 的本地编码，不做远程调用。
"""

from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from app import config
from app.common.exceptions import BusinessException

REQUIRED_MODEL_FILES = ("config.json", "tokenizer.json", "sentencepiece.bpe.model")
MODEL_WEIGHT_FILES = ("pytorch_model.bin", "model.safetensors")
MISSING_WEIGHT_LABEL = "pytorch_model.bin/model.safetensors"
DEFAULT_DIM = 1024
DOWNLOAD_HINT = "缺少模型时：python scripts/download_model.py --mirror"

_embedder = None
_embedder_lock = threading.Lock()
_loaded_model_path: Path | None = None
_loaded_device: str | None = None
_last_run_info: dict[str, Any] = {}

_torch_runtime_cache: dict[str, Any] | None = None
_torch_runtime_lock = threading.Lock()


@dataclass
class EmbeddedDoc:
    """单条文档的嵌入结果。dense 已做 L2 归一化。"""

    dense: list[float]
    sparse: dict[str, float]


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def get_model_dir() -> Path:
    """当前配置的模型目录。"""
    return Path(config.EMBEDDING_MODEL_DIR)


def _missing_model_files(path: Path) -> list[str]:
    missing = [name for name in REQUIRED_MODEL_FILES if not (path / name).is_file()]
    if not any((path / name).is_file() for name in MODEL_WEIGHT_FILES):
        missing.append(MISSING_WEIGHT_LABEL)
    return missing


def is_model_complete(path: Path | None = None) -> bool:
    return not _missing_model_files(path or get_model_dir())


def _read_dim(path: Path) -> int:
    config_path = path / "config.json"
    try:
        content = json.loads(config_path.read_text(encoding="utf-8"))
        hidden_size = int(content.get("hidden_size", DEFAULT_DIM))
        return hidden_size if hidden_size > 0 else DEFAULT_DIM
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return DEFAULT_DIM


def _model_size_mb(path: Path) -> int:
    """只统计必需文件与权重，避免把同目录的 ONNX 副本重复计入。"""
    if not path.exists():
        return 0
    names = list(REQUIRED_MODEL_FILES) + [
        name for name in MODEL_WEIGHT_FILES if (path / name).is_file()
    ]
    size = 0
    try:
        for name in names:
            file_path = path / name
            if file_path.is_file():
                size += file_path.stat().st_size
    except OSError:
        return 0
    return round(size / 1024 / 1024)


def _get_torch_runtime() -> dict[str, Any]:
    """获取 CUDA/GPU 信息。只在状态查询或真正加载时 import torch。"""
    global _torch_runtime_cache
    with _torch_runtime_lock:
        if _torch_runtime_cache is not None:
            return _torch_runtime_cache

        runtime = {
            "cuda_available": False,
            "gpu_name": None,
            "torch_error": None,
        }
        if _module_available("torch"):
            try:
                import torch

                runtime["cuda_available"] = bool(torch.cuda.is_available())
                if runtime["cuda_available"]:
                    runtime["gpu_name"] = torch.cuda.get_device_name(0)
            except Exception as exc:  # 不在状态接口里传播重库异常
                runtime["torch_error"] = str(exc)
        _torch_runtime_cache = runtime
        return runtime


def warmup_runtime() -> None:
    """应用启动时预热 torch/CUDA 状态缓存，避免状态接口首次 import 超时。

    只初始化运行时信息，不加载 bge-m3 权重。
    """
    try:
        _get_torch_runtime()
    except Exception:
        # 状态接口会在 torch 不可用时返回可解释的信息，启动不应失败。
        pass


def _resolve_device() -> str:
    requested = config.settings.EMBEDDING_DEVICE
    if requested == "cpu":
        return "cpu"
    cuda_available = bool(_get_torch_runtime().get("cuda_available"))
    if requested == "cuda" and not cuda_available:
        return "cpu"
    return "cuda" if cuda_available else "cpu"


def get_status() -> dict[str, Any]:
    """模型状态；不下载、不加载模型。"""
    path = get_model_dir()
    missing = _missing_model_files(path)
    runtime = _get_torch_runtime()
    return {
        "model": config.settings.EMBEDDING_MODEL,
        "path": path.as_posix(),
        "downloaded": not missing,
        "missing_files": missing,
        "size_mb": _model_size_mb(path),
        "device": _resolve_device(),
        "cuda_available": bool(runtime.get("cuda_available")),
        "gpu_name": runtime.get("gpu_name"),
        "torch_available": _module_available("torch"),
        "flagembedding_available": _module_available("FlagEmbedding"),
        "dim": _read_dim(path),
        "max_length": config.settings.EMBEDDING_MAX_LENGTH,
        "batch_size": config.settings.EMBEDDING_BATCH_SIZE,
        "hf_endpoint": config.settings.HF_ENDPOINT.strip(),
        "download_hint": DOWNLOAD_HINT,
    }


def _notify(progress: Callable[[str], None] | None, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:
            # 进度展示失败不应影响模型准备
            pass


def ensure_model(progress: Callable[[str], None] | None = None) -> Path:
    """返回完整模型目录；不完整时使用 huggingface_hub 下载。"""
    path = get_model_dir()
    missing = _missing_model_files(path)
    if not missing:
        _notify(progress, f"模型已存在：{path}")
        return path

    _notify(progress, f"模型不完整，缺少：{', '.join(missing)}")
    if not _module_available("huggingface_hub"):
        raise BusinessException(
            message=(
                "缺少 huggingface-hub，无法下载嵌入模型。"
                "请安装 backend/requirements-ai.txt 或手动放置模型权重。"
            )
        )

    endpoint = config.settings.HF_ENDPOINT.strip()
    old_endpoint = os.environ.get("HF_ENDPOINT")
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
    try:
        from huggingface_hub import snapshot_download

        path.mkdir(parents=True, exist_ok=True)
        _notify(progress, f"开始下载 {config.settings.EMBEDDING_MODEL} → {path}")
        try:
            snapshot_download(
                repo_id=config.settings.EMBEDDING_MODEL,
                local_dir=path,
                resume_download=True,
            )
        except Exception as exc:
            mirror_hint = "；可设置 HF_ENDPOINT=https://hf-mirror.com 后重试"
            raise BusinessException(
                message=f"嵌入模型下载失败：{exc}{mirror_hint}"
            ) from exc
    finally:
        if endpoint:
            if old_endpoint is None:
                os.environ.pop("HF_ENDPOINT", None)
            else:
                os.environ["HF_ENDPOINT"] = old_endpoint

    missing = _missing_model_files(path)
    if missing:
        raise BusinessException(
            message=(
                f"模型下载后仍缺少文件：{', '.join(missing)}。"
                "请检查网络、磁盘空间，或设置 HF_ENDPOINT 镜像后重试。"
            ),
            data={"path": path.as_posix(), "missing_files": missing},
        )
    _notify(progress, f"模型准备完成：{path}")
    return path


def _load_model(path: Path, device: str, use_fp16: bool):
    """真正 import FlagEmbedding 并加载模型，单独抽出来便于测试替换。"""
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel(
        str(path),
        use_fp16=use_fp16,
        device=device,
    )


def get_embedder(progress: Callable[[str], None] | None = None):
    """单例懒加载 BGE-M3。"""
    global _embedder, _loaded_model_path, _loaded_device
    with _embedder_lock:
        if _embedder is not None:
            return _embedder

        if not _module_available("FlagEmbedding"):
            raise BusinessException(
                message=(
                    "缺少 FlagEmbedding，无法加载本地 bge-m3。"
                    "请安装 backend/requirements-ai.txt。"
                )
            )

        path = ensure_model(progress)
        device = _resolve_device()
        _notify(progress, f"正在加载 bge-m3（device={device}）…")
        try:
            model = _load_model(
                path=path,
                device=device,
                use_fp16=config.settings.EMBEDDING_USE_FP16,
            )
        except Exception as exc:
            raise BusinessException(
                message=(
                    f"bge-m3 加载失败：{exc}。请检查权重是否完整、"
                    "显存是否足够，或改用 EMBEDDING_DEVICE=cpu。"
                )
            ) from exc

        _embedder = model
        _loaded_model_path = path
        _loaded_device = device
        _notify(progress, f"bge-m3 加载完成（device={device}）")
        return _embedder


def _import_torch():
    try:
        import torch
    except Exception as exc:
        raise BusinessException(
            message="缺少可用的 torch，无法执行本地嵌入。请安装 backend/requirements-ai.txt。"
        ) from exc
    return torch


def _is_cuda_oom(error: Exception) -> bool:
    message = str(error).lower()
    return "cuda" in message and "out of memory" in message


def _empty_cuda_cache(torch_module) -> None:
    try:
        if torch_module.cuda.is_available():
            torch_module.cuda.empty_cache()
    except Exception:
        pass


def _to_dense_rows(dense_vecs, expected_count: int):
    import numpy as np

    array = np.asarray(dense_vecs, dtype="float32")
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.shape[0] != expected_count:
        raise BusinessException(
            message="嵌入模型返回的 dense 行数与输入文档数不一致"
        )
    return array


def _normalize_dense(array):
    import numpy as np

    norms = np.linalg.norm(array, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return array / norms


def _convert_sparse_weights(weights: dict, model) -> dict[str, float]:
    """BGE-M3 的 lexical_weights 可能是 token id；用模型自带方法转回 token 文本。"""
    data = dict(weights)
    if not data:
        return {}

    all_numeric = all(str(token).isdigit() for token in data)
    convert_id_to_token = getattr(model, "convert_id_to_token", None)
    if all_numeric and callable(convert_id_to_token):
        try:
            converted = convert_id_to_token([data])
            if isinstance(converted, list) and converted:
                data = dict(converted[0])
            elif isinstance(converted, dict):
                data = converted
        except Exception:
            pass
    return {str(token): float(weight) for token, weight in data.items()}


def _run_encode_batches(model, texts, batch_size: int, max_length: int):
    dense_rows = []
    sparse_rows: list[dict[str, float]] = []
    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        result = model.encode(
            batch,
            batch_size=len(batch),
            max_length=max_length,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        dense_rows.extend(_to_dense_rows(result["dense_vecs"], len(batch)))
        lexical_weights = result.get("lexical_weights") or [{} for _ in batch]
        if len(lexical_weights) != len(batch):
            raise BusinessException(message="嵌入模型返回的 sparse 行数与输入不一致")
        for weights in lexical_weights:
            sparse_rows.append(_convert_sparse_weights(dict(weights), model))
    return dense_rows, sparse_rows


def encode_documents(
    texts: list[str],
    batch_size: int | None = None,
    max_length: int | None = None,
) -> list[EmbeddedDoc]:
    """把文档编码为 L2 归一化 dense + sparse 词权重。"""
    global _last_run_info
    if not texts:
        _last_run_info = {
            "batch_size": batch_size or config.settings.EMBEDDING_BATCH_SIZE,
            "max_length": max_length or config.settings.EMBEDDING_MAX_LENGTH,
            "device": _resolve_device(),
            "doc_count": 0,
        }
        return []

    texts = [str(text) for text in texts]
    requested_batch = max(1, int(batch_size or config.settings.EMBEDDING_BATCH_SIZE))
    actual_max_length = max(1, int(max_length or config.settings.EMBEDDING_MAX_LENGTH))
    model = get_embedder()
    torch_module = _import_torch()
    current_batch = requested_batch
    started_at = time.perf_counter()

    while True:
        try:
            with torch_module.inference_mode():
                dense_rows, sparse_rows = _run_encode_batches(
                    model, texts, current_batch, actual_max_length
                )
            break
        except RuntimeError as exc:
            if not _is_cuda_oom(exc):
                raise BusinessException(message=f"本地嵌入失败：{exc}") from exc
            _empty_cuda_cache(torch_module)
            if current_batch <= 1:
                raise BusinessException(
                    message=(
                        "CUDA 显存不足，batch=1 仍失败。"
                        "请降低 EMBEDDING_MAX_LENGTH、改用 CPU，或关闭其他占用显存的程序。"
                    )
                ) from exc
            current_batch = max(1, current_batch // 2)

    normalized = _normalize_dense(dense_rows)
    docs = [
        EmbeddedDoc(
            dense=[float(value) for value in normalized[index].tolist()],
            sparse=sparse_rows[index],
        )
        for index in range(len(texts))
    ]

    device = _loaded_device or _resolve_device()
    _last_run_info = {
        "batch_size": current_batch,
        "max_length": actual_max_length,
        "device": device,
        "doc_count": len(texts),
        "elapsed_seconds": round(time.perf_counter() - started_at, 4),
    }
    return docs


def encode_query(text: str) -> EmbeddedDoc:
    """查询向量与文档共用同一模型和 sparse 映射。"""
    return encode_documents([text])[0]


def get_last_run_info() -> dict[str, Any]:
    """最近一次 encode_documents 实际使用的运行参数。"""
    return dict(_last_run_info)


def get_loaded_model_info() -> dict[str, Any]:
    return {
        "path": _loaded_model_path.as_posix() if _loaded_model_path else None,
        "device": _loaded_device,
    }


def sparse_to_indices(
    weights: dict[str, float], token_to_id: Callable[[str], int | None]
) -> tuple[list[int], list[float]]:
    """把稀疏 token 权重映射到整数 index，并合并同 index 权重。"""
    unk_token_id = getattr(token_to_id, "unk_token_id", None)
    merged: dict[int, float] = {}
    for token, weight in weights.items():
        index = None
        try:
            index = token_to_id(str(token))
        except Exception:
            index = None
        if index is None or index == unk_token_id:
            index = zlib.crc32(str(token).encode("utf-8")) & 0xFFFFFFFF
        index = int(index)
        merged[index] = merged.get(index, 0.0) + float(weight)

    ordered = sorted(merged.items())
    return [index for index, _ in ordered], [value for _, value in ordered]
