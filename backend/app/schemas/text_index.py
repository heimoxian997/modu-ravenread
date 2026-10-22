"""文本索引相关请求结构。"""

from pydantic import BaseModel, Field, field_validator


class TextSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="检索问题")
    top_k: int = Field(default=5, ge=1, le=20, description="返回条数")
    # 打开后返回 RRF 与重排两套排序，便于对比；关掉时响应结构和以前一致
    rerank: bool = False
    recall: int = Field(default=20, ge=1, le=50, description="重排前召回条数")
    # 只返回该章及以前的内容；None 表示不限制
    chapter_idx: int | None = Field(
        default=None, ge=0, description="安全章 idx，只检索 <= 它的章节"
    )

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("query 不能为空")
        return value
