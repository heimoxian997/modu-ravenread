"""文本索引相关请求结构。"""

from pydantic import BaseModel, Field, field_validator


class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="检索问题")
    top_k: int = Field(default=5, ge=1, le=20, description="返回条数")

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("query 不能为空")
        return value
