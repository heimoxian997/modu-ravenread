"""模型服务配置、密钥与连通性测试（不联网）。"""

from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import httpx
import pytest

from app import config
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.llm_provider import LlmProvider
from app.schemas.llm import LlmProviderCreate, LlmProviderUpdate
from app.services import llm_provider_service, llm_service, secret_service


@pytest.fixture(autouse=True)
def reset_secret_cache():
    secret_service.reset_cache()
    yield
    secret_service.reset_cache()


@pytest.fixture()
def secret_tmp(monkeypatch, tmp_path):
    root = tmp_path / "resources"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(config, "DATA_DIR", root)
    secret_service.reset_cache()
    try:
        yield root
    finally:
        secret_service.reset_cache()
        pass  # 统一由会话资源管理器回收


def _create(**kwargs):
    payload = {
        "preset_key": "custom",
        "base_url": "https://example.com/v1",
        "model": "test-model",
        **kwargs,
    }
    with SessionLocal() as db:
        return llm_provider_service.create_provider(
            db, LlmProviderCreate(**payload)
        )


def test_mask_key():
    assert llm_provider_service.mask_key(None) is None
    assert llm_provider_service.mask_key("short") == "****"
    assert llm_provider_service.mask_key("sk-1234567890abcd") == "sk-****abcd"


def test_secret_round_trip_and_bad_token(secret_tmp):
    token = secret_service.encrypt_api_key("sk-secret-value")
    assert token
    assert "sk-secret-value" not in token
    assert secret_service.decrypt_api_key(token) == "sk-secret-value"
    assert secret_service.get_secret_path().exists()

    with pytest.raises(BusinessException) as exc_info:
        secret_service.decrypt_api_key("not-a-valid-token")
    assert "密钥无法解密" in exc_info.value.message


def test_create_defaults_duplicate_and_custom_validation():
    created = _create(
        name=None,
        preset_key="deepseek",
        base_url=None,
        model=None,
        api_key="sk-test-1234567890",
    )
    assert created["name"] == "DeepSeek"
    assert created["base_url"] == "https://api.deepseek.com/v1"
    assert created["model"] == "deepseek-chat"
    assert created["has_key"] is True
    assert created["masked_key"] == "sk-****7890"
    assert "api_key_enc" not in created
    assert "sk-test-1234567890" not in json.dumps(created)

    with SessionLocal() as db:
        row = db.query(LlmProvider).one()
        assert row.api_key_enc
        assert "sk-test-1234567890" not in row.api_key_enc

    with pytest.raises(BusinessException) as duplicate:
        _create(name="DeepSeek")
    assert duplicate.value.code == 4000
    assert "名称已存在" in duplicate.value.message

    with pytest.raises(BusinessException) as missing:
        _create(preset_key="custom", base_url="", model="")
    assert "base_url 和 model" in missing.value.message


def test_update_preserves_or_clears_key_and_normalizes_url():
    created = _create(name="测试服务", api_key="sk-1234567890abcd")
    with SessionLocal() as db:
        first = db.get(LlmProvider, created["id"])
        original_enc = first.api_key_enc

        updated = llm_provider_service.update_provider(
            db,
            created["id"],
            LlmProviderUpdate(
                base_url="https://example.com/v1/chat/completions/",
                api_key="",
            ),
        )
        assert updated["base_url"] == "https://example.com/v1"
        assert updated["has_key"] is True
        assert db.get(LlmProvider, created["id"]).api_key_enc == original_enc

        cleared = llm_provider_service.update_provider(
            db,
            created["id"],
            LlmProviderUpdate(clear_key=True, api_key=""),
        )
        assert cleared["has_key"] is False
        assert cleared["masked_key"] is None
        assert db.get(LlmProvider, created["id"]).api_key_enc is None

    with SessionLocal() as db:
        with pytest.raises(BusinessException) as invalid:
            llm_provider_service.update_provider(
                db,
                created["id"],
                LlmProviderUpdate(base_url="ftp://example.com/v1"),
            )
        assert "http://" in invalid.value.message


def test_active_is_single_and_delete_clears():
    first = _create(name="服务A")
    second = _create(name="服务B", preset_key="ollama", base_url=None, model=None)

    with SessionLocal() as db:
        llm_provider_service.set_active(db, first["id"])
        rows = db.query(LlmProvider).order_by(LlmProvider.id).all()
        assert [row.is_active for row in rows] == [True, False]

        llm_provider_service.set_active(db, second["id"])
        rows = db.query(LlmProvider).order_by(LlmProvider.id).all()
        assert [row.is_active for row in rows] == [False, True]

        llm_provider_service.delete_provider(db, second["id"])
        rows = db.query(LlmProvider).all()
        assert len(rows) == 1
        assert rows[0].is_active is False


def test_list_never_exposes_encrypted_or_plain_key():
    _create(name="密钥服务", api_key="sk-1234567890abcd")
    with SessionLocal() as db:
        data = llm_provider_service.list_providers(db)
    text = json.dumps(data, ensure_ascii=False)
    assert "api_key_enc" not in text
    assert "sk-1234567890abcd" not in text
    assert data["providers"][0]["has_key"] is True
    assert data["providers"][0]["masked_key"] == "sk-****abcd"
    assert len(data["presets"]) == 5


def test_status_none_local_cloud():
    with SessionLocal() as db:
        empty = llm_provider_service.get_status(db)
    assert empty["provider_count"] == 0
    assert empty["llm_configured"] is False
    assert empty["cloud_enabled"] is False
    assert empty["active_provider"] is None

    local = _create(
        name="本地 Ollama",
        preset_key="ollama",
        base_url=None,
        model=None,
        api_key=None,
    )
    with SessionLocal() as db:
        llm_provider_service.set_active(db, local["id"])
        local_status = llm_provider_service.get_status(db)
    assert local_status["llm_configured"] is True
    assert local_status["active_provider"]["is_local"] is True
    assert local_status["active_has_key"] is False
    assert local_status["cloud_enabled"] is False

    cloud = _create(
        name="云端 DeepSeek",
        preset_key="deepseek",
        base_url=None,
        model=None,
        api_key="sk-cloud-123456",
    )
    with SessionLocal() as db:
        llm_provider_service.set_active(db, cloud["id"])
        cloud_status = llm_provider_service.get_status(db)
    assert cloud_status["active_has_key"] is True
    assert cloud_status["cloud_enabled"] is True
    assert cloud_status["active_provider"]["is_local"] is False


def test_test_provider_success_with_mock_transport():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = llm_service.test_provider(
            {
                "base_url": "https://api.example.com/v1/",
                "model": "demo-model",
                "api_key": "sk-test",
            },
            client=client,
        )

    assert result["ok"] is True
    assert result["reply"] == "OK"
    assert result["error"] is None
    assert seen["url"] == "https://api.example.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["messages"] == [
        {"role": "user", "content": "只回复 OK"}
    ]
    assert seen["body"]["max_tokens"] == 8
    assert seen["body"]["temperature"] == 0


def test_test_provider_errors_are_readable():
    def unauthorized(request):
        return httpx.Response(
            401, json={"error": {"message": "invalid api key"}}
        )

    with httpx.Client(transport=httpx.MockTransport(unauthorized)) as client:
        result = llm_service.test_provider(
            {
                "base_url": "https://api.example.com/v1",
                "model": "demo",
                "api_key": "sk-secret-1234",
            },
            client=client,
        )
    assert result["ok"] is False
    assert "HTTP 401" in result["error"]
    assert "invalid api key" in result["error"]
    assert "sk-secret-1234" not in result["error"]

    def timeout(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with httpx.Client(transport=httpx.MockTransport(timeout)) as client:
        timed_out = llm_service.test_provider(
            {
                "base_url": "https://api.example.com/v1",
                "model": "demo",
                "api_key": "sk-test",
            },
            client=client,
        )
    assert timed_out["ok"] is False
    assert "超时" in timed_out["error"]

    def non_json(request):
        return httpx.Response(200, text="not json")

    with httpx.Client(transport=httpx.MockTransport(non_json)) as client:
        bad_json = llm_service.test_provider(
            {
                "base_url": "https://api.example.com/v1",
                "model": "demo",
                "api_key": "sk-test",
            },
            client=client,
        )
    assert bad_json["ok"] is False
    assert "格式不正确" in bad_json["error"]


def test_missing_key_and_local_without_key():
    def should_not_call(request):
        raise AssertionError("缺少 Key 的云端服务不应发起请求")

    with httpx.Client(
        transport=httpx.MockTransport(should_not_call)
    ) as client:
        cloud = llm_service.test_provider(
            {
                "base_url": "https://api.example.com/v1",
                "model": "demo",
                "api_key": "",
            },
            client=client,
        )
    assert cloud["ok"] is False
    assert "缺少 API Key" in cloud["error"]

    def local_handler(request):
        assert request.headers.get("Authorization") is None
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}
        )

    with httpx.Client(
        transport=httpx.MockTransport(local_handler)
    ) as client:
        local = llm_service.test_provider(
            {
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "qwen2.5:7b",
                "api_key": "",
            },
            client=client,
        )
    assert local["ok"] is True
    assert local["reply"] == "OK"

def test_disable_thinking_flag_round_trip():
    created = _create(
        name="思考模型", api_key="sk-test-1234567890", disable_thinking=True
    )
    assert created["disable_thinking"] is True

    with SessionLocal() as db:
        updated = llm_provider_service.update_provider(
            db, created["id"], LlmProviderUpdate(disable_thinking=False)
        )
    assert updated["disable_thinking"] is False

    with SessionLocal() as db:
        config = llm_provider_service.get_active_provider_config(
            db, created["id"]
        )
    assert config["disable_thinking"] is False


def test_chat_completion_adds_enable_thinking_only_when_disabled():
    seen = []

    def handler(request: httpx.Request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "OK"}}]}
        )

    provider = {
        "base_url": "https://api.example.com/v1",
        "model": "qwen3.7-flash",
        "api_key": "sk-test",
        "disable_thinking": True,
    }
    messages = [{"role": "user", "content": "hi"}]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        llm_service.chat_completion(provider, messages, client=client)
        llm_service.chat_completion(
            {**provider, "disable_thinking": False}, messages, client=client
        )

    assert seen[0]["enable_thinking"] is False
    assert "enable_thinking" not in seen[1]

