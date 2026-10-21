"""草稿上传、解析与确认相关请求结构。"""

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class DraftUploadResponse(BaseModel):
    draft_token: str
    filename: str
    encoding: str
    char_count: int
    head: str


class ParseRequest(BaseModel):
    chapter_samples: list[str] = Field(
        min_length=1,
        max_length=2,
        description="章节标题样例，1–2 个；每个样例可对应一种章节格式",
    )
    extra_samples: list[str] = Field(
        default_factory=list, max_length=2, description="番外名称样例，0–2 个"
    )
    fallback: bool = Field(default=False, description="零命中时是否按约 4000 字自动切分")

    @field_validator("chapter_samples", "extra_samples")
    @classmethod
    def _strip_samples(cls, value: list[str]):
        result = [item.strip() for item in value if item and item.strip()]
        for item in result:
            if len(item) > 200:
                raise ValueError("样例标题过长，请填写完整的单行标题")
        return result


class ConfirmChapter(BaseModel):
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    title: str = ""
    kind: Literal["main", "extra"] = "main"


class BookMetaIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    author: str | None = Field(default=None, max_length=100)
    synopsis: str | None = Field(default=None, max_length=20000)
    protagonists: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str):
        value = value.strip()
        if not value:
            raise ValueError("书名不能为空")
        return value

    @field_validator("author", "synopsis")
    @classmethod
    def _clean_optional_text(cls, value: str | None):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("protagonists")
    @classmethod
    def _clean_protagonists(cls, value: list[str]):
        names: list[str] = []
        for item in value:
            name = (item or "").strip()
            if name and name not in names:
                names.append(name)
        if len(names) > 3:
            raise ValueError("主角最多填写 3 个")
        return names


class ConfirmRequest(BaseModel):
    chapters: list[ConfirmChapter] = Field(min_length=1)
    book: BookMetaIn


class BookPatch(BaseModel):
    """书籍详情页可编辑的元数据子集。"""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    author: str | None = Field(default=None, max_length=100)
    synopsis: str | None = Field(default=None, max_length=20000)
    protagonists: list[str] | None = Field(default=None, max_length=3)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, value: str | None):
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("书名不能为空")
        return value

    @field_validator("author", "synopsis")
    @classmethod
    def _clean_optional_text(cls, value: str | None):
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("protagonists")
    @classmethod
    def _clean_protagonists(cls, value: list[str] | None):
        if value is None:
            return None
        names: list[str] = []
        for item in value:
            name = (item or "").strip()
            if name and name not in names:
                names.append(name)
        if len(names) > 3:
            raise ValueError("主角最多填写 3 个")
        return names
