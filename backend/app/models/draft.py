"""上传草稿表。token 是 API 路径标识，不暴露自增 id。"""

from sqlalchemy import JSON, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Draft(Base):
    __tablename__ = "drafts"
    __table_args__ = {"comment": "上传解析草稿表"}

    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True, comment="草稿令牌"
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False, comment="安全文件名")
    encoding: Mapped[str] = mapped_column(String(32), nullable=False, comment="探测编码")
    char_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, comment="规范化后字符数"
    )
    raw_path: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="相对 DATA_DIR 的源文件路径"
    )
    chapter_samples: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="章节标题样例"
    )
    extra_samples: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False, comment="番外名称样例"
    )
    parsed: Mapped[dict | None] = mapped_column(
        JSON, nullable=True, comment="最近一次解析结果（偏移、警告、统计）"
    )
