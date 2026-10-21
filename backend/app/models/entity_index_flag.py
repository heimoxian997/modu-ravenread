"""人物/世界观索引的脏标记。"""

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class EntityIndexFlag(Base):
    __tablename__ = "entity_index_flags"
    __table_args__ = (
        UniqueConstraint(
            "book_id",
            "layer",
            name="uq_entity_index_flags_book_layer",
        ),
        {"comment": "实体索引脏标记"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    layer: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="character / lore"
    )
    dirty: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否需要重建"
    )
