"""应用内章节摘要批量任务表。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ExtractJob(Base):
    __tablename__ = "extract_jobs"
    __table_args__ = (
        Index("ix_extract_jobs_book_id_id", "book_id", "id"),
        {"comment": "章节摘要批量任务"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    status: Mapped[str] = mapped_column(
        String(16), default="queued", nullable=False, comment="任务状态"
    )
    stage: Mapped[str] = mapped_column(
        String(16), default="preparing", nullable=False, comment="阶段"
    )
    progress: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="进度 0-100"
    )
    message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="当前说明"
    )
    planned: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="本次计划章数"
    )
    generated: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="已成功章数"
    )
    failed: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="已失败章数"
    )
    current_chapter_idx: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="当前章节 idx"
    )
    next_chapter_idx: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="下一待处理章 idx"
    )
    next_chapter_number: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="下一待处理章 1-based 序号"
    )
    next_chapter_title: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="下一待处理章标题"
    )
    limit: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="本次限量"
    )
    stop_at: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="绝对停止章 1-based"
    )
    force: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否强制重跑"
    )
    retry_failed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否补失败章"
    )
    provider_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="指定模型服务"
    )
    check_provider_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="称谓自检模型服务；空表示只跑规则"
    )
    provider_name: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="服务展示名"
    )
    model: Mapped[str | None] = mapped_column(
        String(200), nullable=True, comment="模型名"
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="失败原因"
    )
    finish_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="结束时间"
    )
