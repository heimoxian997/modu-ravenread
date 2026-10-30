"""Conversation-only memory; never canonical book evidence. Author: heimoxian."""
from sqlalchemy import ForeignKey, String, Integer, Text, JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ConversationMemory(Base):
    __tablename__ = "conversation_memories"
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(20), default="reading")
    safe_chapter: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    supersedes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")


class ConversationDigest(Base):
    __tablename__ = "conversation_digests"
    __table_args__ = (UniqueConstraint("session_id", "source_hash"),)
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(20), default="reading")
    safe_chapter: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64))
    sources: Mapped[list] = mapped_column(JSON)
    content: Mapped[list] = mapped_column(JSON)
    model: Mapped[str] = mapped_column(String(200), default="")


class ConversationRequest(Base):
    __tablename__ = "conversation_requests"
    __table_args__ = (UniqueConstraint("book_id", "request_key"),)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    session_id: Mapped[int] = mapped_column(ForeignKey("agent_sessions.id", ondelete="CASCADE"))
    request_key: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="running")
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
