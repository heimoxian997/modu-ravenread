"""正文切块相关响应结构。"""

from pydantic import BaseModel, Field


class ChunkWarning(BaseModel):
    chapter_idx: int | None = None
    code: str
    message: str


class ChunkBuildStats(BaseModel):
    chapter_count: int = 0
    parent_count: int = 0
    child_count: int = 0
    avg_child_chars: int = 0
    max_child_chars: int = 0
    min_child_chars: int = 0
    warnings: list[ChunkWarning] = Field(default_factory=list)


class ChunkChapterStat(BaseModel):
    idx: int
    title: str
    parent_count: int = 0
    child_count: int = 0


class ChunkOverview(BaseModel):
    built: bool = False
    chapter_count: int = 0
    parent_count: int = 0
    child_count: int = 0
    avg_child_chars: int = 0
    max_child_chars: int = 0
    min_child_chars: int = 0
    chapters: list[ChunkChapterStat] = Field(default_factory=list)
    warnings: list[ChunkWarning] = Field(default_factory=list)


class ChunkNode(BaseModel):
    id: int
    seq: int
    char_start: int
    char_end: int
    text: str


class ParentChunk(ChunkNode):
    children: list[ChunkNode] = Field(default_factory=list)


class ChapterChunks(BaseModel):
    chapter_idx: int
    title: str
    parents: list[ParentChunk] = Field(default_factory=list)


class ClearChunksResponse(BaseModel):
    deleted: int = 0
