"""Derived note search documents and reusable local embedding cache."""
from sqlalchemy import ForeignKey, String, Text, JSON, Float, Integer, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class NoteSearchDocument(Base):
    __tablename__ = "note_search_documents"
    __table_args__ = (
        UniqueConstraint("revision_id", "segment", name="uq_note_search_segment"),
        Index("ix_note_search_hash", "text_hash"),
    )
    revision_id: Mapped[int] = mapped_column(ForeignKey("reading_note_revisions.id", ondelete="CASCADE"))
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"), index=True)
    segment: Mapped[str] = mapped_column(String(80))
    text_hash: Mapped[str] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(Text)


class NoteEmbedding(Base):
    __tablename__ = "note_embeddings"
    __table_args__ = (UniqueConstraint("model_key", "text_hash", name="uq_note_embedding"),)
    model_key: Mapped[str] = mapped_column(String(64))
    text_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    completed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    retry_at: Mapped[float] = mapped_column(Float, default=0)
    error: Mapped[str | None] = mapped_column(String(80), nullable=True)
