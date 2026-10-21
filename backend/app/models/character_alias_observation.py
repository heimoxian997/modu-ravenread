"""人物别名的逐章观测证据。"""

from sqlalchemy import ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CharacterAliasObservation(Base):
    __tablename__ = "character_alias_observations"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "alias",
            "character_id",
            "chapter_idx",
            name="uq_character_alias_observations_book_alias_character_chapter",
        ),
        Index(
            "ix_character_alias_observations_book_alias_chapter",
            "book_id",
            "alias",
            "chapter_idx",
        ),
        {"comment": "人物别名的逐章观测"},
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
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="章节 idx"
    )
    count: Mapped[int] = mapped_column(
        Integer, default=1, nullable=False, comment="本章出现次数"
    )
    source: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="mention / new_alias"
    )
