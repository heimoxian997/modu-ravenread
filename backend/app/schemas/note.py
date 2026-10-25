"""笔记工具的请求模型。"""

from typing import Any

from pydantic import BaseModel, Field


class NoteUpsertRequest(BaseModel):
    type: str = Field(min_length=1, max_length=16)
    subject_key: str = Field(min_length=1, max_length=120)
    quote: str
    payload: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0, le=1)


class NoteQueryRequest(BaseModel):
    type: str | None = Field(default=None, min_length=1, max_length=16)
    entity: str | None = Field(default=None, min_length=1, max_length=120)
    pair: list[str] | None = None
    chapter_from: int | None = Field(default=None, ge=0)
    chapter_to: int | None = Field(default=None, ge=0)
    status: str | None = Field(default=None, min_length=1, max_length=16)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: int = Field(default=0, ge=0)
