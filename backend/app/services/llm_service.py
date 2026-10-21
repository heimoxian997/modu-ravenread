"""OpenAI 兼容模型客户端。

不做服务商分支：所有服务统一请求 normalize_base_url 后的
`POST {base_url}/chat/completions`。本步只用于测试连接。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlparse

import httpx

from app.common.exceptions import BusinessException

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_ERROR_BODY_LIMIT = 500


def normalize_base_url(value: str | None) -> str:
    """去空白、去末尾斜杠，并截掉误粘贴的 /chat/completions。"""
    value = (value or "").strip()
    if not value:
        raise BusinessException(message="base_url 不能为空", code=4000)
    if " " in value or "\n" in value or "\t" in value:
        raise BusinessException(message="base_url 不能包含空白字符", code=4000)

    value = value.rstrip("/")
    suffix = "/chat/completions"
    if value.lower().endswith(suffix):
        value = value[: -len(suffix)].rstrip("/")

    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise BusinessException(
            message="base_url 只允许以 http:// 或 https:// 开头",
            code=4000,
        )
    if not parsed.netloc:
        raise BusinessException(message="base_url 缺少主机名", code=4000)
    return value


def is_local_url(value: str | None) -> bool:
    try:
        host = (urlparse((value or "").strip()).hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS


def _redact(text: str, api_key: str | None) -> str:
    if api_key:
        text = text.replace(api_key, "***")
    return text


def _error_detail(response: httpx.Response, api_key: str | None) -> str:
    detail = response.text or ""
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or detail)
            elif error:
                detail = str(error)
    except ValueError:
        pass
    detail = _redact(detail.strip(), api_key)
    if len(detail) > _ERROR_BODY_LIMIT:
        detail = detail[:_ERROR_BODY_LIMIT] + "…"
    return detail


def chat_completion(
    provider: dict[str, Any],
    messages: list[dict[str, str]],
    max_tokens: int = 16,
    temperature: float = 0,
    timeout: float = 30,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """调用一次 chat completions，任何网络/HTTP/解析错误都转成可读结果。"""
    api_key = provider.get("api_key") or None
    model = provider.get("model") or ""
    started = time.perf_counter()
    try:
        base_url = normalize_base_url(provider.get("base_url"))
        url = f"{base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        body = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        # 思考模型（如 Qwen3）显式关掉推理：结构化抽取不需要推理链，
        # 关掉后耗时和 token 都降一个量级。非思考模型会忽略这个字段。
        if provider.get("disable_thinking"):
            body["enable_thinking"] = False
        owns_client = client is None
        http_client = client or httpx.Client(
            timeout=timeout, follow_redirects=True
        )
        try:
            response = http_client.post(url, headers=headers, json=body)
        finally:
            if owns_client:
                http_client.close()
    except BusinessException as exc:
        return {
            "content": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status_code": None,
            "error": exc.message,
            "error_code": "business",
        }
    except httpx.TimeoutException:
        return {
            "content": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status_code": None,
            "error": f"请求超时（{int(timeout)} 秒），请检查服务地址或网络。",
            "error_code": "timeout",
        }
    except httpx.RequestError as exc:
        return {
            "content": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status_code": None,
            "error": f"无法连接模型服务：{_redact(str(exc), api_key)}",
            "error_code": "request_error",
        }
    except Exception as exc:
        return {
            "content": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "status_code": None,
            "error": f"请求模型服务失败：{_redact(str(exc), api_key)}",
            "error_code": "unknown",
        }

    latency_ms = int((time.perf_counter() - started) * 1000)
    if response.status_code < 200 or response.status_code >= 300:
        detail = _error_detail(response, api_key)
        message = f"模型服务返回 HTTP {response.status_code}"
        if detail:
            message = f"{message}：{detail}"
        return {
            "content": None,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
            "error": message,
            "error_code": "http",
        }

    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        finish_reason = choice.get("finish_reason")
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return {
            "content": None,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
            "error": "模型服务返回格式不正确：缺少 choices[0].message.content。",
            "error_code": "bad_response",
        }

    if content is None:
        return {
            "content": None,
            "latency_ms": latency_ms,
            "status_code": response.status_code,
            "error": "模型服务没有返回文本内容。",
            "error_code": "bad_response",
            "finish_reason": finish_reason,
        }
    return {
        "content": str(content),
        "latency_ms": latency_ms,
        "status_code": response.status_code,
        "error": None,
        "error_code": None,
        "finish_reason": finish_reason,
    }


def test_provider(
    provider: dict[str, Any], client: httpx.Client | None = None
) -> dict[str, Any]:
    """只发送一句“只回复 OK”，不发送任何书稿。"""
    api_key = provider.get("api_key") or None
    model = provider.get("model") or ""
    if not api_key and not is_local_url(provider.get("base_url")):
        return {
            "ok": False,
            "latency_ms": 0,
            "reply": None,
            "model": model,
            "error": "缺少 API Key，请填写后重试。",
        }

    result = chat_completion(
        provider,
        [{"role": "user", "content": "只回复 OK"}],
        max_tokens=8,
        temperature=0,
        client=client,
    )
    if result["error"]:
        return {
            "ok": False,
            "latency_ms": result["latency_ms"],
            "reply": None,
            "model": model,
            "error": result["error"],
        }
    return {
        "ok": True,
        "latency_ms": result["latency_ms"],
        "reply": result["content"],
        "model": model,
        "error": None,
    }
