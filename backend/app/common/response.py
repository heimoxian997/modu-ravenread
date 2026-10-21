"""统一响应结构。"""

from typing import Any

from pydantic import BaseModel


class Response(BaseModel):
    """统一封装的返回格式：{code, message, data}。"""

    code: int
    message: str
    data: Any = None

    @classmethod
    def success(cls, message: str = "请求成功", data: Any = None):
        return cls(code=200, message=message, data=data)

    @classmethod
    def error(cls, code: int = 500, message: str = "请求失败", data: Any = None):
        return cls(code=code, message=message, data=data)


class PageResponse(BaseModel):
    """分页返回结果，后续索引/搜索结果可复用。"""

    list: Any = []
    total: int = 0
