"""伴读 agent 的模型调用适配：沿用项目现有的 OpenAI 兼容客户端。"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.services import llm_provider_service, llm_service

MODEL_MAX_TOKENS = 1200
MODEL_TIMEOUT = 60
MODEL_TEMPERATURE = 0.2


def provider_config(db: Session, provider_id: int | None = None) -> dict[str, Any]:
    return llm_provider_service.get_active_provider_config(db, provider_id)


def call_model(
    db: Session,
    provider_id: int | None,
    messages: list[dict[str, str]],
    timeout: int = MODEL_TIMEOUT,
) -> dict[str, Any]:
    """调用一次模型；错误转成可读结果，不抛 HTTP 异常。"""
    try:
        provider = provider_config(db, provider_id)
    except BusinessException as exc:
        return {
            "content": None,
            "model": None,
            "error": exc.message,
        }
    result = llm_service.chat_completion(
        {**provider, "disable_thinking": True},
        messages,
        max_tokens=MODEL_MAX_TOKENS,
        temperature=MODEL_TEMPERATURE,
        timeout=timeout,
    )
    return {
        "content": result.get("content"),
        "model": provider.get("model"),
        "error": result.get("error"),
    }
