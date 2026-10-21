"""三类检索的重排对比：排序、名次变化、降级。"""

from __future__ import annotations

from app.services import entity_index_service, rerank_service, text_index_service


def _patch_rerank(monkeypatch, scores=None, error=None):
    calls = []

    def fake(query, documents):
        calls.append((query, list(documents)))
        if scores is None:
            return None, error
        return scores, None

    monkeypatch.setattr(rerank_service, "rerank", fake)
    monkeypatch.setattr(
        rerank_service, "get_last_run_info", lambda: {"latency_ms": 12}
    )
    return calls


def test_compare_items_reorders_and_reports_delta(monkeypatch):
    calls = _patch_rerank(monkeypatch, scores=[0.1, 0.9, 0.5])
    items = [{"chunk_id": 1}, {"chunk_id": 2}, {"chunk_id": 3}]
    outcome = rerank_service.compare_items(
        "问题", items, ["a", "b", "c"], 3, "chunk_id"
    )
    assert calls[0][1] == ["a", "b", "c"]
    assert outcome["applied"] is True
    assert outcome["latency_ms"] == 12
    assert [item["chunk_id"] for item in outcome["items"]] == [2, 3, 1]
    first = outcome["items"][0]
    assert first["rerank_score"] == 0.9
    assert first["rrf_rank"] == 2
    assert first["rank_delta"] == 1
    # 第三名被挤到末尾，名次下降
    assert outcome["items"][-1]["rank_delta"] == -2


def test_compare_items_degrades_when_model_missing(monkeypatch):
    _patch_rerank(monkeypatch, error="重排模型缺失：/nope")
    items = [{"chunk_id": 1}, {"chunk_id": 2}]
    outcome = rerank_service.compare_items(
        "问题", items, ["a", "b"], 1, "chunk_id"
    )
    assert outcome["applied"] is False
    assert outcome["error"] == "重排模型缺失：/nope"
    assert outcome["items"] == [{"chunk_id": 1}]


def test_compare_items_handles_score_length_mismatch(monkeypatch):
    _patch_rerank(monkeypatch, scores=[0.5])
    outcome = rerank_service.compare_items(
        "问题", [{"chunk_id": 1}, {"chunk_id": 2}], ["a", "b"], 2, "chunk_id"
    )
    assert outcome["applied"] is False
    assert "完整分数" in outcome["error"]


def test_text_compare_builds_document_from_title_and_text(monkeypatch):
    calls = _patch_rerank(monkeypatch, scores=[0.2, 0.8, 0.4])
    pool = [
        {
            "chunk_id": 11,
            "chapter_title": "第一章",
            "chapter_idx": 0,
            "text": "苹果",
            "dense_score": 0.5,
            "sparse_score": 0.4,
            "rrf_score": 0.03,
        },
        {
            "chunk_id": 12,
            "chapter_title": "第二章",
            "chapter_idx": 1,
            "text": "香蕉",
            "dense_score": 0.4,
            "sparse_score": 0.3,
            "rrf_score": 0.02,
        },
        {
            "chunk_id": 13,
            "chapter_title": "第三章",
            "chapter_idx": 2,
            "text": "橘子",
            "dense_score": 0.3,
            "sparse_score": 0.2,
            "rrf_score": 0.01,
        },
    ]
    monkeypatch.setattr(
        text_index_service,
        "search_text_index",
        lambda db, book_id, query, top_k=5, recall=20: pool[:top_k],
    )
    data = text_index_service.search_text_index_compare(
        None, 1, "水果", top_k=3, recall=3
    )
    assert data["recall"] == 3
    assert data["rrf_items"][0]["chunk_id"] == 11
    assert data["rerank_items"][0]["chunk_id"] == 12
    assert data["rerank_applied"] is True
    assert calls[0][1][0] == "第一章｜苹果"


def test_text_compare_keeps_rrf_when_pool_empty(monkeypatch):
    _patch_rerank(monkeypatch, scores=[0.1])
    monkeypatch.setattr(
        text_index_service,
        "search_text_index",
        lambda db, book_id, query, top_k=5, recall=20: [],
    )
    data = text_index_service.search_text_index_compare(None, 1, "水果")
    assert data["rrf_items"] == []
    assert data["rerank_items"] == []
    assert data["rerank_applied"] is False


def _character(index: int, name: str) -> dict:
    return {
        "character_id": index,
        "character": name,
        "identity": "将门之女",
        "traits": ["冷静"],
        "relations": ["与兄长不睦"],
        "known_facts": ["经营药铺"],
        "arc": "",
        "dense_score": 0.1 * index,
        "sparse_score": 0.05 * index,
        "rrf_score": 0.01 * index,
    }


def test_character_compare_uses_character_id_and_card_text(monkeypatch):
    calls = _patch_rerank(monkeypatch, scores=[0.1, 0.9])
    pool = [_character(1, "韩砚清"), _character(2, "顾青珩")]
    monkeypatch.setattr(
        entity_index_service,
        "search_character_index",
        lambda db, book_id, query, top_k=5, recall=20, chapter_idx=None: pool[
            :top_k
        ],
    )
    data = entity_index_service.search_character_index_compare(
        None, 1, "谁与家人不睦", top_k=2, recall=2
    )
    assert data["rerank_items"][0]["character_id"] == 2
    assert data["rerank_items"][0]["rrf_rank"] == 2
    assert data["rerank_items"][0]["rank_delta"] == 1
    document = calls[0][1][0]
    assert "韩砚清" in document and "与兄长不睦" in document


def test_lore_compare_uses_lore_id(monkeypatch):
    _patch_rerank(monkeypatch, scores=[0.7, 0.2])
    pool = [
        {
            "lore_id": 31,
            "canonical_name": "拾香斋",
            "summary": "卖香料的铺子",
            "details": ["在青石巷"],
            "dense_score": 0.2,
            "sparse_score": 0.1,
            "rrf_score": 0.02,
        },
        {
            "lore_id": 32,
            "canonical_name": "崇明宫",
            "summary": "宫中势力",
            "details": [],
            "dense_score": 0.1,
            "sparse_score": 0.1,
            "rrf_score": 0.01,
        },
    ]
    monkeypatch.setattr(
        entity_index_service,
        "search_lore_index",
        lambda db, book_id, query, top_k=5, recall=20, chapter_idx=None: pool[
            :top_k
        ],
    )
    data = entity_index_service.search_lore_index_compare(
        None, 1, "香料铺子", top_k=2, recall=2
    )
    assert data["rerank_items"][0]["lore_id"] == 31
    assert data["rerank_items"][0]["rrf_rank"] == 1
    assert data["rerank_items"][0]["rank_delta"] == 0
    assert data["rerank_applied"] is True
