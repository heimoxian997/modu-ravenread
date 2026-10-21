"""人物章节增量事件（不复制完整卡片）。"""

from sqlalchemy import JSON, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CharacterChapterDelta(Base):
    __tablename__ = "character_chapter_deltas"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "character_id",
            "chapter_idx",
            name="uq_character_chapter_deltas_book_character_chapter",
        ),
        Index(
            "ix_character_chapter_deltas_book_character_chapter",
            "book_id",
            "character_id",
            "chapter_idx",
        ),
        {"comment": "人物章节增量"},
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
    identity: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="本章更新后的身份；没变为 NULL"
    )
    traits_add: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="本章新增性格"
    )
    relations_add: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="本章新增关系"
    )
    known_facts_add: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="本章新增事实"
    )
    voice_sample: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="变化才写"
    )
    arc: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="变化才写"
    )
