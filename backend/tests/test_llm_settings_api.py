"""模型服务设置 API 测试。"""

from __future__ import annotations

import json

import pytest

from app.database import SessionLocal
from app.models.llm_provider import LlmProvider
from app.services import llm_provider_service, secret_service


@pytest.fixture(autouse=True)
def reset_secret_cache():
    secret_service.reset_cache()
    yield
    secret_service.reset_cache()


def test_llm_settings_full_flow(client, monkeypatch):
    payload = {
        "name": "我的 DeepSeek",
        "preset_key": "deepseek",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-chat",
        "api_key": "sk-api-1234567890",
    }
    created = client.post("/api/settings/llm/providers", json=payload)
    assert created.status_code == 200
    body = created.json()
    assert body["code"] == 200
    provider = body["data"]
    assert provider["base_url"] == "https://api.deepseek.com/v1"
    assert provider["masked_key"] == "sk-****7890"
    assert "api_key_enc" not in json.dumps(body)
    assert "sk-api-1234567890" not in json.dumps(body)
    provider_id = provider["id"]

    listed = client.get("/api/settings/llm").json()
    assert listed["code"] == 200
    assert listed["data"]["active_id"] is None
    assert len(listed["data"]["presets"]) == 5
    assert listed["data"]["providers"][0]["has_key"] is True

    updated = client.put(
        f"/api/settings/llm/providers/{provider_id}",
        json={"name": "重命名服务", "api_key": ""},
    ).json()
    assert updated["code"] == 200
    assert updated["data"]["name"] == "重命名服务"
    assert updated["data"]["has_key"] is True

    active = client.post(
        "/api/settings/llm/active", json={"provider_id": provider_id}
    ).json()
    assert active["code"] == 200
    status = client.get("/api/settings/status").json()["data"]
    assert status["provider_count"] == 1
    assert status["llm_configured"] is True
    assert status["cloud_enabled"] is True
    assert status["active_has_key"] is True
    assert status["active_provider"]["name"] == "重命名服务"

    monkeypatch.setattr(
        llm_provider_service,
        "test_provider",
        lambda db, pid, client=None: {
            "ok": True,
            "latency_ms": 42,
            "reply": "OK",
            "model": "deepseek-chat",
            "error": None,
        },
    )
    tested = client.post(
        f"/api/settings/llm/providers/{provider_id}/test"
    ).json()
    assert tested["code"] == 200
    assert tested["data"]["ok"] is True
    assert tested["data"]["latency_ms"] == 42

    cleared = client.put(
        f"/api/settings/llm/providers/{provider_id}",
        json={"clear_key": True},
    ).json()["data"]
    assert cleared["has_key"] is False
    assert cleared["masked_key"] is None
    after_clear = client.get("/api/settings/status").json()["data"]
    assert after_clear["active_has_key"] is False
    assert after_clear["cloud_enabled"] is False

    deleted = client.delete(
        f"/api/settings/llm/providers/{provider_id}"
    ).json()
    assert deleted["code"] == 200
    assert deleted["data"]["deleted"] == provider_id
    final_status = client.get("/api/settings/status").json()["data"]
    assert final_status["active_provider"] is None
    assert final_status["llm_configured"] is False


def test_llm_settings_errors(client):
    first = client.post(
        "/api/settings/llm/providers",
        json={
            "name": "重复服务",
            "preset_key": "custom",
            "base_url": "https://example.com/v1",
            "model": "demo",
        },
    ).json()
    assert first["code"] == 200

    duplicate = client.post(
        "/api/settings/llm/providers",
        json={
            "name": "重复服务",
            "preset_key": "custom",
            "base_url": "https://example.com/v2",
            "model": "demo2",
        },
    ).json()
    assert duplicate["code"] == 4000
    assert duplicate["message"] == "名称已存在"

    missing = 99999
    assert (
        client.put(
            f"/api/settings/llm/providers/{missing}", json={"name": "不存在"}
        ).json()["code"]
        == 4040
    )
    assert (
        client.delete(
            f"/api/settings/llm/providers/{missing}"
        ).json()["code"]
        == 4040
    )
    assert (
        client.post(
            f"/api/settings/llm/providers/{missing}/test"
        ).json()["code"]
        == 4040
    )
    assert (
        client.post(
            "/api/settings/llm/active", json={"provider_id": missing}
        ).json()["code"]
        == 4040
    )


def test_llm_api_does_not_store_plain_key(client):
    created = client.post(
        "/api/settings/llm/providers",
        json={
            "name": "加密检查",
            "preset_key": "custom",
            "base_url": "https://example.com/v1",
            "model": "demo",
            "api_key": "sk-plain-should-not-appear",
        },
    ).json()["data"]
    with SessionLocal() as db:
        row = db.get(LlmProvider, created["id"])
        assert row.api_key_enc
        assert "sk-plain-should-not-appear" not in row.api_key_enc
    assert "sk-plain-should-not-appear" not in json.dumps(created)
