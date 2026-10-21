"""模型服务配置服务。

预设只用于创建时填充模板；运行时只读取用户保存的 base_url + model + api_key。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.database import engine
from app.models.llm_provider import LlmProvider
from app.schemas.llm import LlmProviderCreate, LlmProviderUpdate
from app.services import llm_service, secret_service

PRESETS = [
    {
        "preset_key": "agnes",
        "name": "Agnes",
        "base_url": "https://api.agnes-ai.cn/v1",
        "model": "agnes-2.5-flash",
        "key_hint": "通常需要",
    },
    {
        "preset_key": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "key_hint": "通常需要",
    },
    {
        "preset_key": "qwen",
        "name": "Qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "key_hint": "通常需要",
    },
    {
        "preset_key": "ollama",
        "name": "Ollama",
        "base_url": "http://127.0.0.1:11434/v1",
        "model": "qwen2.5:7b",
        "key_hint": "可不需要",
    },
    {
        "preset_key": "custom",
        "name": "自定义",
        "base_url": "",
        "model": "",
        "key_hint": "视服务而定",
    },
]
PRESET_MAP = {item["preset_key"]: item for item in PRESETS}


def presets() -> list[dict[str, Any]]:
    result = []
    for item in PRESETS:
        result.append(
            {
                **item,
                "is_local": llm_service.is_local_url(item["base_url"]),
            }
        )
    return result


def mask_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) < 12:
        return "****"
    return f"{value[:3]}****{value[-4:]}"


def _normalize_preset(value: str | None) -> str:
    key = (value or "custom").strip().lower()
    return key if key in PRESET_MAP else "custom"


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _provider_or_404(db: Session, provider_id: int) -> LlmProvider:
    provider = db.get(LlmProvider, provider_id)
    if not provider:
        raise BusinessException(message="模型服务不存在或已被删除", code=4040)
    return provider


def _ensure_name_available(
    db: Session, name: str, exclude_id: int | None = None
) -> None:
    query = db.query(LlmProvider).filter(LlmProvider.name == name)
    if exclude_id is not None:
        query = query.filter(LlmProvider.id != exclude_id)
    if query.first():
        raise BusinessException(message="名称已存在", code=4000)


def _encrypted_key_for_mask(provider: LlmProvider) -> str | None:
    if not provider.api_key_enc:
        return None
    try:
        return secret_service.decrypt_api_key(provider.api_key_enc)
    except BusinessException:
        return None


def ensure_provider_schema() -> None:
    """给旧库补 disable_thinking 列。

    SQLAlchemy 的 create_all 只建新表，不会给已有表加列；这里用 PRAGMA 判断，
    只在缺列时 ALTER 一次，测试库由 create_all 建好，走不到 ALTER。
    """
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(llm_providers)")
        }
        if "disable_thinking" not in columns:
            conn.exec_driver_sql(
                "ALTER TABLE llm_providers "
                "ADD COLUMN disable_thinking BOOLEAN NOT NULL DEFAULT 0"
            )


def provider_to_dict(provider: LlmProvider) -> dict[str, Any]:
    plain_key = _encrypted_key_for_mask(provider)
    return {
        "id": provider.id,
        "name": provider.name,
        "preset_key": provider.preset_key,
        "base_url": provider.base_url,
        "model": provider.model,
        "is_active": bool(provider.is_active),
        "is_local": llm_service.is_local_url(provider.base_url),
        "has_key": bool(provider.api_key_enc),
        "masked_key": mask_key(plain_key),
        "disable_thinking": bool(provider.disable_thinking),
    }


def list_providers(db: Session) -> dict[str, Any]:
    providers = (
        db.query(LlmProvider)
        .order_by(LlmProvider.is_active.desc(), LlmProvider.id.asc())
        .all()
    )
    active = next((item for item in providers if item.is_active), None)
    return {
        "providers": [provider_to_dict(item) for item in providers],
        "active_id": active.id if active else None,
        "presets": presets(),
    }


def create_provider(
    db: Session, payload: LlmProviderCreate
) -> dict[str, Any]:
    preset_key = _normalize_preset(payload.preset_key)
    preset = PRESET_MAP[preset_key]
    name = _clean(payload.name) or preset["name"]
    _ensure_name_available(db, name)

    base_url = _clean(payload.base_url)
    model = _clean(payload.model)
    if preset_key != "custom":
        base_url = base_url or preset["base_url"]
        model = model or preset["model"]
    if not base_url:
        raise BusinessException(
            message="自定义服务必须填写 base_url 和 model", code=4000
        )
    if not model:
        raise BusinessException(
            message="自定义服务必须填写 base_url 和 model", code=4000
        )

    normalized_base_url = llm_service.normalize_base_url(base_url)
    api_key = _clean(payload.api_key)
    provider = LlmProvider(
        name=name,
        preset_key=preset_key,
        base_url=normalized_base_url,
        model=model,
        api_key_enc=secret_service.encrypt_api_key(api_key) if api_key else None,
        is_active=False,
        disable_thinking=bool(payload.disable_thinking),
    )
    db.add(provider)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessException(message="名称已存在", code=4000) from exc
    db.refresh(provider)
    return provider_to_dict(provider)


def update_provider(
    db: Session, provider_id: int, payload: LlmProviderUpdate
) -> dict[str, Any]:
    provider = _provider_or_404(db, provider_id)
    data = payload.model_dump(exclude_unset=True)

    if data.get("name") is not None:
        name = _clean(data["name"])
        if not name:
            raise BusinessException(message="名称不能为空", code=4000)
        _ensure_name_available(db, name, exclude_id=provider_id)
        provider.name = name

    if data.get("preset_key") is not None:
        provider.preset_key = _normalize_preset(data["preset_key"])

    if data.get("base_url") is not None:
        base_url = _clean(data["base_url"])
        if not base_url:
            raise BusinessException(message="base_url 不能为空", code=4000)
        provider.base_url = llm_service.normalize_base_url(base_url)

    if data.get("model") is not None:
        model = _clean(data["model"])
        if not model:
            raise BusinessException(message="model 不能为空", code=4000)
        provider.model = model

    if data.get("disable_thinking") is not None:
        provider.disable_thinking = bool(data["disable_thinking"])

    if payload.clear_key:
        provider.api_key_enc = None
    else:
        api_key = _clean(payload.api_key)
        if api_key:
            provider.api_key_enc = secret_service.encrypt_api_key(api_key)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise BusinessException(message="名称已存在", code=4000) from exc
    db.refresh(provider)
    return provider_to_dict(provider)


def delete_provider(db: Session, provider_id: int) -> int:
    provider = _provider_or_404(db, provider_id)
    db.delete(provider)
    db.commit()
    return provider_id


def set_active(
    db: Session, provider_id: int | None
) -> dict[str, Any]:
    target = None
    if provider_id is not None:
        target = _provider_or_404(db, provider_id)
    for provider in db.query(LlmProvider).all():
        provider.is_active = bool(target and provider.id == target.id)
    db.commit()
    return {"active_id": target.id if target else None}


def get_status(db: Session) -> dict[str, Any]:
    provider_count = db.query(LlmProvider).count()
    active = (
        db.query(LlmProvider).filter(LlmProvider.is_active.is_(True)).first()
    )
    active_has_key = bool(active and active.api_key_enc)
    is_local = bool(active and llm_service.is_local_url(active.base_url))
    return {
        "provider_count": provider_count,
        "llm_configured": active is not None,
        "cloud_enabled": bool(active and not is_local and active_has_key),
        "active_has_key": active_has_key,
        "active_provider": (
            {
                "id": active.id,
                "name": active.name,
                "preset_key": active.preset_key,
                "is_local": is_local,
            }
            if active
            else None
        ),
    }


def get_active_provider_config(
    db: Session, provider_id: int | None = None
) -> dict[str, Any]:
    """返回内部调用配置（含明文 Key），只供服务层使用，不暴露给 API。"""
    if provider_id is None:
        provider = (
            db.query(LlmProvider)
            .filter(LlmProvider.is_active.is_(True))
            .first()
        )
        if not provider:
            raise BusinessException(
                message="尚未配置模型服务，请先在设置页添加并启用模型。",
                code=4000,
            )
    else:
        provider = _provider_or_404(db, provider_id)

    api_key = secret_service.decrypt_api_key(provider.api_key_enc)
    if not api_key and not llm_service.is_local_url(provider.base_url):
        raise BusinessException(
            message="当前模型缺少 API Key，请先到设置页填写。",
            code=4000,
        )
    return {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "base_url": provider.base_url,
        "model": provider.model,
        "api_key": api_key,
        "is_local": llm_service.is_local_url(provider.base_url),
        "disable_thinking": bool(provider.disable_thinking),
    }


def test_provider(
    db: Session, provider_id: int, client=None
) -> dict[str, Any]:
    provider = _provider_or_404(db, provider_id)
    try:
        api_key = secret_service.decrypt_api_key(provider.api_key_enc)
    except BusinessException as exc:
        return {
            "ok": False,
            "latency_ms": 0,
            "reply": None,
            "model": provider.model,
            "error": exc.message,
        }
    return llm_service.test_provider(
        {
            "base_url": provider.base_url,
            "model": provider.model,
            "api_key": api_key,
        },
        client=client,
    )
