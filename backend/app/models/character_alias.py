"""人物别名候选（多对多，不自动决定身份）。"""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CharacterAlias(Base):
    __tablename__ = "character_aliases"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "alias",
            "character_id",
            name="uq_character_aliases_book_alias_character",
        ),
        Index("ix_character_aliases_book_alias", "book_id", "alias"),
        {"comment": "人物别名候选聚合"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
        comment="人物",
    )
    alias: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="别名"
    )
    first_seen_chapter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="最早出现章 idx"
    )
    last_seen_chapter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="最后出现章 idx"
    )
    occurrence_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="出现次数"
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="mention / new_alias / mixed"
    )
