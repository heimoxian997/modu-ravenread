"""伴读 agent 的模型调用适配：沿用项目现有的 OpenAI 兼容客户端。"""

from __future__ import annotations

from typing import Any
from contextvars import ContextVar

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.services import llm_provider_service, llm_service

MODEL_MAX_TOKENS = 1200
MODEL_TIMEOUT = 60
MODEL_TEMPERATURE = 0.2
_run = ContextVar("companion_model_run", default=None)


def begin_run(request_id=None):
    return _run.set({"provider": None, "request_id": request_id})


def end_run(token):
    _run.reset(token)


def is_cancelled():
    state = _run.get()
    if not state or not state.get("request_id"):
        return False
    from app.database import SessionLocal
    from app.models.conversation_memory import ConversationRequest
    with SessionLocal() as db:
        row = db.get(ConversationRequest, state["request_id"])
        return row is None or row.status != "running"


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
        state = _run.get()
        if is_cancelled():
            return {"content": None, "model": None, "error": "请求已取消"}
        if sum(len(str(m.get("content", ""))) for m in messages) > 64000:
            return {"content": None, "model": None, "error": "本轮上下文额度已用完，请缩小问题范围"}
        if state is not None and state["provider"] is None:
            state["provider"] = dict(provider_config(db, provider_id))
        provider = state["provider"] if state is not None else provider_config(db, provider_id)
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
        cancel_requested=is_cancelled if state is not None else None,
    )
    return {
        "content": result.get("content"),
        "model": provider.get("model"),
        "error": result.get("error"),
    }
