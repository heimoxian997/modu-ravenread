"""章节摘要与可编辑字段。"""

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChapterSummary(Base):
    __tablename__ = "chapter_summaries"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "chapter_idx", name="uq_chapter_summaries_book_chapter"
        ),
        {"comment": "章节摘要表"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="章节序号，从 0 开始"
    )
    title: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="章节标题"
    )
    summary: Mapped[str] = mapped_column(Text, nullable=False, comment="本章摘要")
    key_events: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="关键事件"
    )
    characters: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="本章出场人物"
    )
    cliffhanger: Mapped[str] = mapped_column(
        Text, default="", nullable=False, comment="章末钩子"
    )
    plot_threads: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="情节线"
    )
    foreshadowing: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="伏笔"
    )
    reveals: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="揭示"
    )
    summary_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="摘要是否截断"
    )
    edited: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否人工编辑"
    )
    prompt_version: Mapped[str] = mapped_column(
        String(50), default="chapter_extract_v1", nullable=False
    )
    provider_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="服务展示名"
    )
    model: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="模型名"
    )
    elapsed_ms: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="抽取耗时毫秒"
    )
