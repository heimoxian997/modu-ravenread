"""人物逐章提及。"""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CharacterMention(Base):
    __tablename__ = "character_mentions"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "character_id",
            "chapter_idx",
            name="uq_character_mentions_book_character_chapter",
        ),
        {"comment": "人物逐章提及"},
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
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="章节 idx"
    )
    aliases: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="本章使用的别名"
    )
    role: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="本章身份/角色"
    )
