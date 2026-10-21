"""章节结构化抽取结果。

保留模型原始返回与完整 parsed 结构；解析失败时也可以只写 raw。
"""

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


class ChapterExtraction(Base):
    __tablename__ = "chapter_extractions"
    __table_args__ = (
        UniqueConstraint(
            "book_id", "chapter_idx", name="uq_chapter_extractions_book_chapter"
        ),
        {"comment": "章节结构化抽取结果"},
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
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="success / parse_failed"
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
    text_truncated: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="章节正文是否截断"
    )
    output_trimmed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="输出是否按预算裁剪"
    )
    elapsed_ms: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="总耗时毫秒"
    )
    raw_content: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="模型原始返回"
    )
    parsed: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="完整 JSON 结构"
    )
    alias_check: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="本章别名自检记录"
    )
