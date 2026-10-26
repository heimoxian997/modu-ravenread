from pydantic import BaseModel, ConfigDict, Field


class BookmarkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    chapter_idx: int = Field(ge=0, strict=True)
    char_offset: int = Field(ge=0, strict=True)
    label: str = Field(default="", max_length=80)


class BookmarkPatch(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(max_length=80)
