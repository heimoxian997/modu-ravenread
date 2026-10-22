"""摘要索引重建与检索请求结构。"""

from pydantic import BaseModel, Field, field_validator


class SummaryIndexBuildRequest(BaseModel):
    allow_partial: bool = False


class SummarySearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    # 打开后返回 RRF 与重排两套排序，便于对比；关掉时响应结构和以前一致
    rerank: bool = False
    recall: int = Field(default=20, ge=1, le=50)
    # 只返回该章及以前的摘要；None 表示不限制
    chapter_idx: int | None = Field(default=None, ge=0)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("查询文本不能为空")
        return value
