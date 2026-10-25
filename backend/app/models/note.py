"""阅读笔记与修复队列。

笔记是唯一由 agent 直接写的数据层：每条必须带能在原文直接定位的
逐字引文，章号由偏移反查得到，写入时超出安全章的标 spoiler=1 存而不返。
"""

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Note(Base):
    __tablename__ = "notes"
    __table_args__ = (
        Index("idx_notes_subject", "book_id", "subject_key", "chapter_idx"),
        Index("idx_notes_type", "book_id", "type", "chapter_idx"),
        Index("idx_notes_status", "book_id", "status", "spoiler"),
        {"comment": "阅读笔记表"},
    )

    note_id: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, comment="n_{type}_{subject}_{hash8}"
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="event | relation | address | fact | correction"
    )
    subject_key: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="ent_x | ent_x|ent_y | ent_x>ent_y"
    )
    chapter_idx: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="由 char_start 反查得到，不接受手填"
    )
    char_start: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="引文在源文件中的起始码点偏移"
    )
    char_end: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="引文在源文件中的结束码点偏移"
    )
    quote: Mapped[str] = mapped_column(
        Text, nullable=False, comment="逐字引文，必须能在原文直接定位"
    )
    payload_json: Mapped[dict] = mapped_column(
        JSON, nullable=False, comment="按类型约束的结构化内容"
    )
    confidence: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.6, comment="置信度 0-1"
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="tentative",
        comment="tentative | confirmed | superseded | rejected",
    )
    spoiler: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="1 = 写入时超出安全章，存而不返"
    )
    supersedes: Mapped[str | None] = mapped_column(
        String(80), nullable=True, comment="取代的旧版本 note_id"
    )
    session_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, comment="写入会话"
    )
    question: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="触发这条笔记的问题"
    )
    created_by: Mapped[str] = mapped_column(
        String(32), nullable=False, default="agent", comment="作者"
    )


class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = ({"comment": "修复队列表"},)

    issue_id: Mapped[str] = mapped_column(
        String(80), nullable=False, unique=True, comment="问题标识"
    )
    book_id: Mapped[int] = mapped_column(
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属书籍",
    )
    target_type: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="entity | summary | lore | note"
    )
    target_id: Mapped[str] = mapped_column(
        String(120), nullable=False, comment="被指目标"
    )
    issue_type: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="问题类型"
    )
    note: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="问题描述"
    )
    evidence_quote: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="证据：原文原句"
    )
    chapter_idx: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="证据所在章"
    )
    proposed_fix_json: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="可直接执行的修复描述"
    )
    rebuild_scope: Mapped[str | None] = mapped_column(
        String(16), nullable=True, comment="none | summary | lore | block | all"
    )
    fix_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="pending",
        comment="pending | approved | applied | rejected",
    )
    applied_at: Mapped[str | None] = mapped_column(
        String(32), nullable=True, comment="执行时间"
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="open", comment="open | closed"
    )
