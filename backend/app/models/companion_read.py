"""跟读任务与实际阅读覆盖；作者 heimoxian。"""
from sqlalchemy import ForeignKey, Integer, String, Text, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class CompanionRead(Base):
    __tablename__ = "companion_reads"
    __table_args__ = (UniqueConstraint("book_id", "chapter_idx", "source_hash"),)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    chapter_idx: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempt_id: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(200), default="")
    covered: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer)
    segments: Mapped[list] = mapped_column(JSON, default=list)
    organization: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
