"""世界观条目（按章保留出处）。"""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LoreFact(Base):
    __tablename__ = "lore_facts"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "canonical_name",
            "chapter_idx",
            name="uq_lore_facts_book_name_chapter",
        ),
        {"comment": "世界观条目"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    canonical_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="规范名"
    )
    category: Mapped[str | None] = mapped_column(
        String(50), nullable=True, comment="location/organization/item/..."
    )
    aliases: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="别名"
    )
    summary: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="摘要"
    )
    details: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="细节"
    )
    entities: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="关联实体"
    )
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="出处章节 idx"
    )
