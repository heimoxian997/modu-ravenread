"""Immutable, spoiler-gated revisions. Legacy notes remain untouched for review."""
from sqlalchemy import ForeignKey, Integer, String, JSON, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class ReadingNoteRevision(Base):
    __tablename__ = "reading_note_revisions"
    __table_args__ = (
        UniqueConstraint("book_id", "note_id", "version", name="uq_reading_note_revision"),
        Index("ix_reading_note_gate", "book_id", "knowledge_idx", "kind"),
    )
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    note_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16))
    knowledge_idx: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)


class LegacyNoteImport(Base):
    __tablename__ = "legacy_note_imports"
    __table_args__ = (UniqueConstraint("book_id", "legacy_id", name="uq_legacy_note_import"),)
    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    legacy_id: Mapped[int] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"))
    note_id: Mapped[str] = mapped_column(String(64))
