"""嵌入服务测试：不下载、不加载真实模型。"""

import json
import shutil
import uuid
import zlib
from pathlib import Path

import pytest

from app import config
from app.common.exceptions import BusinessException
from app.services import embedding_service


@pytest.fixture()
def model_tmp():
    """系统临时目录在当前沙箱不可写，测试临时目录放在仓库 tests 下。"""
    root = Path(__file__).parent / ".pytest-data" / "embedding"
    path = root / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _touch_complete_model(path: Path, hidden_size: int = 1024) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text(
        json.dumps({"hidden_size": hidden_size}), encoding="utf-8"
    )
    for name in ("tokenizer.json", "sentencepiece.bpe.model"):
        (path / name).write_text("fake", encoding="utf-8")
    (path / "pytorch_model.bin").write_bytes(b"fake")


import contextlib
import sys
import types


class _FakeTorch:
    """只提供 encode_documents 用到的两个能力。"""

    class cuda:
        @staticmethod
        def is_available():
            return False

        @staticmethod
        def empty_cache():
            pass

    @staticmethod
    def inference_mode():
        return contextlib.nullcontext()


@pytest.fixture()
def fake_torch(monkeypatch):
    monkeypatch.setattr(embedding_service, "_import_torch", lambda: _FakeTorch)
    return _FakeTorch


@pytest.fixture()
def fake_hub(monkeypatch):
    module = types.ModuleType("huggingface_hub")
    module.snapshot_download = lambda **kwargs: None
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)

    real = embedding_service._module_available
    monkeypatch.setattr(
        embedding_service,
        "_module_available",
        lambda name: True if name == "huggingface_hub" else real(name),
    )
    return module


@pytest.fixture()
def cpu_runtime(monkeypatch):
    monkeypatch.setattr(
        embedding_service,
        "_get_torch_runtime",
        lambda: {
            "cuda_available": False,
            "gpu_name": None,
            "torch_error": None,
        },
    )


def test_status_missing_and_complete(model_tmp, monkeypatch, cpu_runtime):
    model_dir = model_tmp / "empty-model"
    monkeypatch.setattr(config, "EMBEDDING_MODEL_DIR", model_dir)
    monkeypatch.setattr(config.settings, "EMBEDDING_DEVICE", "auto")

    status = embedding_service.get_status()
    assert status["downloaded"] is False
    assert "config.json" in status["missing_files"]
    assert "tokenizer.json" in status["missing_files"]
    assert "sentencepiece.bpe.model" in status["missing_files"]
    assert embedding_service.MISSING_WEIGHT_LABEL in status["missing_files"]
    assert status["device"] == "cpu"
    assert status["dim"] == 1024

    _touch_complete_model(model_dir, hidden_size=768)
    status = embedding_service.get_status()
    assert status["downloaded"] is True
    assert status["missing_files"] == []
    assert status["dim"] == 768
    assert status["path"] == model_dir.as_posix()


def test_ensure_model_downloads_with_expected_arguments(
    model_tmp, monkeypatch, fake_hub
):
    model_dir = model_tmp / "download-model"
    monkeypatch.setattr(config, "EMBEDDING_MODEL_DIR", model_dir)
    calls = []
    progress = []

    def fake_download(**kwargs):
        calls.append(kwargs)
        target = Path(kwargs["local_dir"])
        _touch_complete_model(target)

    fake_hub.snapshot_download = fake_download

    path = embedding_service.ensure_model(progress=progress.append)
    assert path == model_dir
    assert calls
    assert calls[0]["repo_id"] == config.settings.EMBEDDING_MODEL
    assert Path(calls[0]["local_dir"]) == model_dir
    assert calls[0]["resume_download"] is True
    assert progress


def test_ensure_model_skips_when_complete(model_tmp, monkeypatch, fake_hub):
    model_dir = model_tmp / "ready-model"
    _touch_complete_model(model_dir)
    monkeypatch.setattr(config, "EMBEDDING_MODEL_DIR", model_dir)

    def should_not_download(**kwargs):
        raise AssertionError("完整模型不应触发下载")

    fake_hub.snapshot_download = should_not_download
    assert embedding_service.ensure_model() == model_dir


def test_ensure_model_reports_missing_after_download(
    model_tmp, monkeypatch, fake_hub
):
    model_dir = model_tmp / "broken-model"
    monkeypatch.setattr(config, "EMBEDDING_MODEL_DIR", model_dir)

    def fake_download(**kwargs):
        target = Path(kwargs["local_dir"])
        target.mkdir(parents=True, exist_ok=True)
        (target / "config.json").write_text("{}", encoding="utf-8")

    fake_hub.snapshot_download = fake_download

    with pytest.raises(BusinessException) as exc_info:
        embedding_service.ensure_model()
    assert "仍缺少" in exc_info.value.message
    assert exc_info.value.data["missing_files"]


def test_sparse_to_indices_prefers_token_id_and_sorts():
    weights = {"a": 0.5, "b": 1.5, "unknown": 2.0}

    def token_to_id(token):
        return {"a": 2, "b": 1}.get(token)

    indices, values = embedding_service.sparse_to_indices(weights, token_to_id)
    expected_unknown = zlib.crc32(b"unknown") & 0xFFFFFFFF

    assert indices == sorted([2, 1, expected_unknown])
    mapping = dict(zip(indices, values))
    assert mapping[1] == 1.5
    assert mapping[2] == 0.5
    assert mapping[expected_unknown] == 2.0


def test_sparse_to_indices_merges_duplicate_mapping():
    def token_to_id(token):
        return 7

    indices, values = embedding_service.sparse_to_indices(
        {"a": 1.0, "b": 2.5}, token_to_id
    )
    assert indices == [7]
    assert values == [3.5]


def test_sparse_to_indices_uses_unk_token_id_fallback():
    def token_to_id(token):
        return 0 if token == "unknown" else 5

    token_to_id.unk_token_id = 0
    expected = zlib.crc32(b"unknown") & 0xFFFFFFFF
    indices, values = embedding_service.sparse_to_indices(
        {"known": 1.0, "unknown": 2.0}, token_to_id
    )
    mapping = dict(zip(indices, values))
    assert mapping[5] == 1.0
    assert mapping[expected] == 2.0


class _FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **kwargs):
        import numpy as np

        self.calls.append((list(texts), kwargs))
        return {
            "dense_vecs": np.asarray([[3.0, 4.0] for _ in texts], dtype="float32"),
            "lexical_weights": [
                {f"token{index}": float(index + 1)} for index in range(len(texts))
            ],
        }


def test_encode_documents_batches_and_l2_normalizes(monkeypatch, fake_torch):
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedding_service, "get_embedder", lambda progress=None: fake)

    docs = embedding_service.encode_documents(
        ["a", "b", "c", "d", "e"], batch_size=2, max_length=16
    )

    assert len(docs) == 5
    assert [len(call[0]) for call in fake.calls] == [2, 2, 1]
    assert all(call[1]["batch_size"] == len(call[0]) for call in fake.calls)
    assert all(call[1]["max_length"] == 16 for call in fake.calls)
    assert all(call[1]["return_dense"] is True for call in fake.calls)
    assert all(call[1]["return_sparse"] is True for call in fake.calls)
    assert all(call[1]["return_colbert_vecs"] is False for call in fake.calls)

    assert len(docs[0].dense) == 2
    assert docs[0].dense == pytest.approx([0.6, 0.8])
    assert docs[0].sparse == {"token0": 1.0}
    assert embedding_service.get_last_run_info()["batch_size"] == 2


def test_encode_documents_converts_numeric_sparse_ids(monkeypatch, fake_torch):
    import numpy as np

    class IdSparseEmbedder:
        def encode(self, texts, **kwargs):
            return {
                "dense_vecs": np.asarray([[1.0, 0.0] for _ in texts], dtype="float32"),
                "lexical_weights": [{"101": 0.5, "202": 1.5} for _ in texts],
            }

        def convert_id_to_token(self, weights):
            mapping = {"101": "林晚", "202": "秦屿"}
            return [
                {mapping[token]: weight for token, weight in item.items()}
                for item in weights
            ]

    monkeypatch.setattr(
        embedding_service, "get_embedder", lambda progress=None: IdSparseEmbedder()
    )
    docs = embedding_service.encode_documents(["测试"], batch_size=1)
    assert docs[0].sparse == {"林晚": 0.5, "秦屿": 1.5}


def test_encode_query_returns_single_doc(monkeypatch, fake_torch):
    fake = _FakeEmbedder()
    monkeypatch.setattr(embedding_service, "get_embedder", lambda progress=None: fake)

    doc = embedding_service.encode_query("查询")
    assert doc.dense == pytest.approx([0.6, 0.8])
    assert doc.sparse == {"token0": 1.0}


def test_encode_documents_halves_batch_on_oom(monkeypatch, fake_torch):
    import numpy as np

    class OomOnceModel:
        def __init__(self):
            self.calls = []

        def encode(self, texts, **kwargs):
            self.calls.append(len(texts))
            if len(self.calls) == 1:
                raise RuntimeError("CUDA out of memory")
            return {
                "dense_vecs": np.asarray([[1.0, 0.0] for _ in texts], dtype="float32"),
                "lexical_weights": [{"t": 1.0} for _ in texts],
            }

    fake = OomOnceModel()
    emptied = []
    monkeypatch.setattr(embedding_service, "get_embedder", lambda progress=None: fake)
    monkeypatch.setattr(
        embedding_service,
        "_empty_cuda_cache",
        lambda torch_module: emptied.append(True),
    )

    docs = embedding_service.encode_documents(["a", "b", "c", "d"], batch_size=4)
    assert len(docs) == 4
    assert fake.calls == [4, 2, 2]
    assert emptied
    assert embedding_service.get_last_run_info()["batch_size"] == 2


def test_encode_documents_oom_at_batch_one_raises(monkeypatch, fake_torch):
    class AlwaysOomModel:
        def encode(self, texts, **kwargs):
            raise RuntimeError("CUDA out of memory")

    monkeypatch.setattr(
        embedding_service, "get_embedder", lambda progress=None: AlwaysOomModel()
    )
    monkeypatch.setattr(
        embedding_service, "_empty_cuda_cache", lambda torch_module: None
    )

    with pytest.raises(BusinessException) as exc_info:
        embedding_service.encode_documents(["a"], batch_size=1)
    assert "显存不足" in exc_info.value.message


def test_status_api_does_not_load_model(client, model_tmp, monkeypatch, cpu_runtime):
    model_dir = model_tmp / "api-model"
    _touch_complete_model(model_dir, hidden_size=1024)
    monkeypatch.setattr(config, "EMBEDDING_MODEL_DIR", model_dir)

    response = client.get("/api/settings/embedding")
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    data = payload["data"]
    assert data["downloaded"] is True
    assert data["device"] == "cpu"
    assert data["dim"] == 1024
    assert data["missing_files"] == []
