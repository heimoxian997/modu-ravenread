"""文本索引任务表。

任务状态与进度存在 SQLite；向量本身仍在本地 Qdrant。
同一时间只允许一个全局索引任务，任务线程为 daemon。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class IndexJob(Base):
    __tablename__ = "index_jobs"
    __table_args__ = (
        Index("ix_index_jobs_book_kind", "book_id", "kind"),
        {"comment": "索引任务表"},
    )

    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    kind: Mapped[str] = mapped_column(
        String(32), default="text_vector", nullable=False, comment="任务类型"
    )
    status: Mapped[str] = mapped_column(
        String(16), default="queued", nullable=False, comment="任务状态"
    )
    stage: Mapped[str] = mapped_column(
        String(16), default="preparing", nullable=False, comment="当前阶段"
    )
    progress: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="进度 0-100"
    )
    message: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="当前阶段说明"
    )
    point_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="完成后的向量点数"
    )
    error: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="失败原因"
    )
    finish_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, comment="结束时间"
    )
