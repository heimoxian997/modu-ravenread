"""世界观条目的人工修正（按规范名 + 章节稳定匹配）。"""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LoreOverride(Base):
    __tablename__ = "lore_overrides"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "canonical_name",
            "chapter_idx",
            name="uq_lore_overrides_book_name_chapter",
        ),
        {"comment": "世界观条目人工修正"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    canonical_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="稳定键：规范名"
    )
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="稳定键：出处章节 idx"
    )
    category: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="类型"
    )
    aliases: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="别名"
    )
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="概述"
    )
    details: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="细节"
    )
    entities: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="关联实体"
    )
