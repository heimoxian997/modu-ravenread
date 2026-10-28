"""Role-chat preparation data. None of these tables are original-book facts."""

from sqlalchemy import JSON, ForeignKey, ForeignKeyConstraint, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RoleCharacter(Base):
    __tablename__ = "role_characters"
    __table_args__ = (UniqueConstraint("book_id", "canonical_name"),
                      UniqueConstraint("id", "book_id"))

    book_id: Mapped[int] = mapped_column(ForeignKey("books.id", ondelete="CASCADE"))
    role_key: Mapped[str] = mapped_column(String(40), unique=True)
    canonical_name: Mapped[str] = mapped_column(String(100))
    # No foreign key to the rebuildable characters table.


class RolePreparationJob(Base):
    __tablename__ = "role_preparation_jobs"
    __table_args__ = (ForeignKeyConstraint(
        ["role_id", "book_id"], ["role_characters.id", "role_characters.book_id"],
        ondelete="CASCADE"),)

    book_id: Mapped[int] = mapped_column(Integer, index=True)
    role_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    stage: Mapped[str] = mapped_column(String(20), default="extract")
    # A unique non-null slot serializes role preparation across threads/processes.
    active_slot: Mapped[int | None] = mapped_column(Integer, unique=True, nullable=True)
    run_token: Mapped[str] = mapped_column(String(40))
    source_hash: Mapped[str] = mapped_column(String(64))
    provider_id: Mapped[int] = mapped_column(Integer)
    model_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    plan: Mapped[dict] = mapped_column(JSON)
    max_calls: Mapped[int] = mapped_column(Integer)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    completed_batches: Mapped[int] = mapped_column(Integer, default=0)
    cache_hits: Mapped[int] = mapped_column(Integer, default=0)
    metrics: Mapped[list] = mapped_column(JSON, default=list)
    draft_profile: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RoleExtractionCache(Base):
    __tablename__ = "role_extraction_cache"
    __table_args__ = (UniqueConstraint("role_id", "cache_key"),
                      ForeignKeyConstraint(
                          ["role_id", "book_id"],
                          ["role_characters.id", "role_characters.book_id"], ondelete="CASCADE"))

    book_id: Mapped[int] = mapped_column(Integer)
    role_id: Mapped[int] = mapped_column(Integer)
    cache_key: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16), default="extraction")
    payload: Mapped[dict] = mapped_column(JSON)


class RoleProfileVersion(Base):
    __tablename__ = "role_profile_versions"
    __table_args__ = (UniqueConstraint("role_id", "version"),
                      ForeignKeyConstraint(
                          ["role_id", "book_id"],
                          ["role_characters.id", "role_characters.book_id"], ondelete="CASCADE"))

    book_id: Mapped[int] = mapped_column(Integer)
    role_id: Mapped[int] = mapped_column(Integer, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("role_preparation_jobs.id", ondelete="SET NULL"), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    source_hash: Mapped[str] = mapped_column(String(64))
    prompt_version: Mapped[str] = mapped_column(String(64))
    target: Mapped[dict] = mapped_column(JSON)
    profile: Mapped[dict] = mapped_column(JSON)
    observations: Mapped[list] = mapped_column(JSON)
    coverage: Mapped[dict] = mapped_column(JSON)


class RolePreparationBatch(Base):
    __tablename__ = "role_preparation_batches"
    __table_args__ = (UniqueConstraint("job_id", "number"),)

    job_id: Mapped[int] = mapped_column(ForeignKey("role_preparation_jobs.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    chapter_number: Mapped[int] = mapped_column(Integer)
    scenes: Mapped[list] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(80), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)


class RoleBatchRevision(Base):
    __tablename__ = "role_batch_revisions"
    __table_args__ = (UniqueConstraint("batch_id", "revision"),)

    batch_id: Mapped[int] = mapped_column(ForeignKey("role_preparation_batches.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    result: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16))
