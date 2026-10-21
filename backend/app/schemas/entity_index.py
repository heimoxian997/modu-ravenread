"""人物/世界观索引与修正请求结构。"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class EntityIndexBuildRequest(BaseModel):
    layer: Literal["all", "character", "lore"] = "all"
    allow_partial: bool = False


class EntitySearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    chapter_idx: int | None = Field(default=None, ge=0)
    # 打开后返回 RRF 与重排两套排序；关掉时响应仍是原来的数组
    rerank: bool = False
    recall: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("查询文本不能为空")
        return value


class CharacterChangeSearchRequest(BaseModel):
    """按 delta 检索人物变化，不聚合成人物卡。"""

    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=20)
    chapter_idx: int | None = Field(default=None, ge=0)
    recall: int = Field(default=20, ge=1, le=50)

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("查询文本不能为空")
        return value


class CharacterCardUpdate(BaseModel):
    identity: str | None = None
    traits: list[Any] = Field(default_factory=list)
    relations: list[Any] = Field(default_factory=list)
    known_facts: list[Any] = Field(default_factory=list)
    voice_sample: str | None = None
    arc: str | None = None
    # 修正从第几章（0-based）起生效；不传按当前查看章（缺省最新已处理章）处理。
    from_chapter: int | None = Field(default=None, ge=0)
    # 保存后按哪一章回显详情；不传按最新章节。
    chapter_idx: int | None = Field(default=None, ge=0)


class LoreUpdate(BaseModel):
    category: str | None = None
    aliases: list[Any] = Field(default_factory=list)
    summary: str | None = None
    details: list[Any] = Field(default_factory=list)
    entities: list[Any] = Field(default_factory=list)
