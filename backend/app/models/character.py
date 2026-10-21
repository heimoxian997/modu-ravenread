"""书籍人物规范实体。"""

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "canonical_name", name="uq_characters_book_name"
        ),
        {"comment": "书籍人物规范名"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    canonical_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="规范名"
    )
    first_seen_chapter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="最早出现章 idx"
    )
    last_seen_chapter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="最后出现章 idx"
    )
    mention_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="被提及章数"
    )
    is_protagonist: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否主角"
    )
    source: Mapped[str] = mapped_column(
        String(20), default="extracted", nullable=False, comment="来源"
    )
