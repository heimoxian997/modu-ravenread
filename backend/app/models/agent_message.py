"""伴读 agent 的消息。"""

from sqlalchemy import (
    JSON,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    __table_args__ = (
        {"comment": "伴读 agent 的消息、位置与工具轨迹"},
    )

    session_id: Mapped[int] = mapped_column(
        ForeignKey("agent_sessions.id", ondelete="CASCADE"),
        nullable=False,
        comment="所属会话",
    )
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="user / assistant"
    )
    content: Mapped[str] = mapped_column(
        Text, nullable=False, comment="问题或回答"
    )
    position_chapter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="提问时的章节 idx"
    )
    position_offset: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="提问时的全局码点偏移"
    )
    safe_chapter: Mapped[int | None] = mapped_column(
        Integer, nullable=True, comment="提问时的安全章 idx"
    )
    meta: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="citations / tool_trace / model / error"
    )
