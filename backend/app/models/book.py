"""书籍表。"""

from typing import TYPE_CHECKING

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.chapter import Chapter


class Book(Base):
    __tablename__ = "books"
    __table_args__ = {"comment": "书籍表"}

    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="书名")
    author: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="作者")
    synopsis: Mapped[str | None] = mapped_column(Text, nullable=True, comment="简介/文案")
    protagonists: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="主角名字列表"
    )
    front_matter: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="首个章节标题之前的文字"
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False, comment="原始文件名")
    encoding: Mapped[str] = mapped_column(String(32), nullable=False, comment="源文件编码")
    char_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="全书字符数"
    )
    chapter_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="章节总数"
    )
    main_chapter_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="正文章节数"
    )

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Chapter.idx",
    )
