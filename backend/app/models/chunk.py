"""正文切块表。

先固定 version=1，为后续索引版本预留；parent_id 只保存父块 id，
不加自引用外键，删除书籍时依靠 book_id 外键级联清理。
"""

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "version",
            "chapter_idx",
            "level",
            "seq",
            name="uq_chunks_book_version_chapter_level_seq",
        ),
        Index(
            "ix_chunks_book_version_chapter",
            "book_id",
            "version",
            "chapter_idx",
        ),
        {"comment": "正文切块表"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    version: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="索引版本，当前固定为 1"
    )
    chapter_idx: Mapped[int] = mapped_column(Integer, nullable=False, comment="章节序号")
    level: Mapped[str] = mapped_column(
        String(8), nullable=False, comment="层级：parent / child"
    )
    parent_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="子块所属父块 id；父块为空"
    )
    seq: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="章内顺序，父块/子块各自从 0 递增"
    )
    text: Mapped[str] = mapped_column(Text, nullable=False, comment="块内原文")
    char_start: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="源文件全局起始偏移（码点）"
    )
    char_end: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="源文件全局结束偏移（码点）"
    )
