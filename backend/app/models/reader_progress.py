"""读者最近阅读位置；一本书一行，供 MCP / 外部宿主使用。"""

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ReaderProgress(Base):
    __tablename__ = "reader_progress"
    __table_args__ = (
        UniqueConstraint("book_id", name="uq_reader_progress_book"),
        {"comment": "每本书最近阅读位置"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="最近阅读章 idx"
    )
    char_offset: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="全局码点偏移"
    )
