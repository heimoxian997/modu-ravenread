"""人物卡片的人工修正（按稳定规范名保存）。"""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CharacterCardOverride(Base):
    __tablename__ = "character_card_overrides"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "character_name",
            name="uq_character_card_overrides_book_name",
        ),
        {"comment": "人物卡片人工修正"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    character_name: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="稳定键：人物规范名"
    )
    from_chapter: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="修正从第几章（0-based）起生效",
    )
    identity: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="身份"
    )
    traits: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="性格特质"
    )
    relations: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="关系"
    )
    known_facts: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="已知事实"
    )
    voice_sample: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="代表台词"
    )
    arc: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="人物弧光"
    )
