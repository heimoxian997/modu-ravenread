"""章节摘要失败记录；同一章只保留最新一条。"""

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SummaryIssue(Base):
    __tablename__ = "summary_issues"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "chapter_idx", name="uq_summary_issues_book_chapter"
        ),
        {"comment": "章节摘要失败记录"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="章节序号，从 0 开始"
    )
    error: Mapped[str] = mapped_column(
        Text, nullable=False, comment="失败原因，最多 1000 字"
    )
