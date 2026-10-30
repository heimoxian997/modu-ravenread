"""伴读 agent 的请求与响应结构。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    chapter_idx: int = Field(ge=0)
    char_offset: int = Field(ge=0)
    session_id: int | None = Field(default=None, ge=1)
    # 可选：指定模型服务；不传用当前启用服务。
    provider_id: int | None = Field(default=None, ge=1)
    spoilers: bool = False
    request_key: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class AgentCitation(BaseModel):
    chapter_idx: int = Field(ge=0)
    chapter_title: str | None = None
    quote: str | None = None


class AgentToolTrace(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    ok: bool = True
    summary: str = ""
    error: str | None = None


class AgentChatResponse(BaseModel):
    session_id: int
    reply: str
    citations: list[AgentCitation] = Field(default_factory=list)
    tool_trace: list[AgentToolTrace] = Field(default_factory=list)
    model: str | None = None
    elapsed_ms: int = 0
    citation_verified: bool = False
    budget_exhausted: str | None = None
    error: str | None = None


class AgentToolRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=50)
    args: dict[str, Any] = Field(default_factory=dict)


class ReaderProgressRequest(BaseModel):
    chapter_idx: int = Field(ge=0)
    char_offset: int = Field(default=0, ge=0)


class AgentMessageItem(BaseModel):
    id: int
    role: str
    content: str
    position_chapter: int | None = None
    position_offset: int | None = None
    safe_chapter: int | None = None
    meta: dict[str, Any] | None = None
    create_time: str | None = None


class AgentSessionResponse(BaseModel):
    session_id: int | None = None
    book_id: int
    messages: list[AgentMessageItem] = Field(default_factory=list)
