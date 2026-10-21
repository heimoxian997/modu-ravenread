"""模型服务配置请求结构。"""

from pydantic import BaseModel, Field


class LlmProviderCreate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    preset_key: str = Field(default="custom", max_length=32)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    disable_thinking: bool = False


class LlmProviderUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    preset_key: str | None = Field(default=None, max_length=32)
    base_url: str | None = Field(default=None, max_length=500)
    model: str | None = Field(default=None, max_length=200)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_key: bool = False
    disable_thinking: bool | None = None


class SetActiveProviderRequest(BaseModel):
    provider_id: int | None = Field(default=None, ge=1)
