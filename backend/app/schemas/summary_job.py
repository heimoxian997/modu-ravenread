"""摘要批量任务请求结构。"""

from pydantic import BaseModel, Field


class BatchJobCreate(BaseModel):
    limit: int | None = Field(default=None, ge=1)
    stop_at: int | None = Field(default=None, ge=1)
    force: bool = False
    retry_failed: bool = False
    provider_id: int | None = Field(default=None, ge=1)
    # 单章称谓自检用哪个模型；留空只跑零成本规则层
    check_provider_id: int | None = Field(default=None, ge=1)
    # limit 与 stop_at 的互斥在 service 层返回业务码 4000。
