"""Personal reading bookmarks anchored to source paragraph offsets."""
from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("book_id", "chapter_idx", "char_offset", name="uq_bookmark_position"),)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    chapter_idx: Mapped[int] = mapped_column(Integer)
    char_offset: Mapped[int] = mapped_column(Integer)
    excerpt: Mapped[str] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(80), default="")
