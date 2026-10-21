"""数据库连接与会话管理。

本地单用户应用只需要一个 SQLite 文件，连接开启外键约束。
"""

from datetime import datetime
from pathlib import Path

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from app.config import settings

# SQLite 在 FastAPI 的线程池中会被多个线程使用，因此关闭线程检查。
connect_args = {"check_same_thread": False, "timeout": 30}
if settings.DATABASE_URL and settings.DATABASE_URL.startswith("sqlite:///"):
    db_file = Path(settings.DATABASE_URL.removeprefix("sqlite:///"))
    db_file.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    """每个新连接都开启外键，否则 SQLite 默认不执行级联删除。"""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    """FastAPI 数据库会话依赖。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class Base(DeclarativeBase):
    """所有表的公共字段约定。"""

    id: Mapped[int] = mapped_column(
        primary_key=True, autoincrement=True, comment="主键ID", sort_order=-1
    )
    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, comment="创建时间"
    )
    update_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间"
    )
