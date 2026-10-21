"""模型服务配置表。

只保存 OpenAI 兼容服务的连接元数据；API Key 使用 Fernet 加密后保存。
预设只是前端/创建时的模板，不在运行时参与请求分支。
"""

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class LlmProvider(Base):
    __tablename__ = "llm_providers"
    __table_args__ = {"comment": "模型服务配置表"}

    name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, comment="展示名称"
    )
    preset_key: Mapped[str] = mapped_column(
        String(32), default="custom", nullable=False, comment="预设元数据"
    )
    base_url: Mapped[str] = mapped_column(
        String(500), nullable=False, comment="OpenAI 兼容 base_url"
    )
    model: Mapped[str] = mapped_column(
        String(200), nullable=False, comment="模型名称"
    )
    api_key_enc: Mapped[str | None] = mapped_column(
        Text, nullable=True, comment="Fernet 加密后的 API Key"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, comment="是否为当前模型"
    )
    disable_thinking: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        comment="请求时附加 enable_thinking=false（仅对思考模型生效）",
    )
