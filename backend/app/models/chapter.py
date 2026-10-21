"""章节表。只保存偏移，正文始终从源文件切片。"""

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.book import Book


class Chapter(Base):
    __tablename__ = "chapters"
    __table_args__ = (
        UniqueConstraint("book_id", "idx", name="uq_chapters_book_idx"),
        Index("ix_chapters_book_idx", "book_id", "idx"),
        {"comment": "章节表"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    idx: Mapped[int] = mapped_column(Integer, nullable=False, comment="章节序号，从 0 开始")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="章节标题")
    kind: Mapped[str] = mapped_column(
        String(16), default="main", nullable=False, comment="类型：main 正文 / extra 番外"
    )
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, comment="源文件起始偏移")
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, comment="源文件结束偏移")
    word_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="章节字符数"
    )

    book: Mapped["Book"] = relationship(back_populates="chapters")
