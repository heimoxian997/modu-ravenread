"""摘要编辑请求结构。"""

from typing import Any

from pydantic import BaseModel


class SummaryUpdate(BaseModel):
    summary: str | None = None
    key_events: list[Any] | None = None
    characters: list[Any] | None = None
    cliffhanger: str | None = None


class ExtractionParsedPatch(BaseModel):
    """只允许改抽取结果里的这两个字段。

    这里不写具体类型，交给 service 校验，保证非法结构一律返回 4000 而不是 422。
    """

    characters: Any | None = None
    lore: Any | None = None
