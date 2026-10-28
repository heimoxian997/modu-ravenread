"""Frontend-facing role preparation requests; never accept reader-scope overrides."""

from typing import Literal

from pydantic import Field, model_validator

from app.schemas.role_preparation import Contract, Name, ExtractionBatch


class RolePlanCreate(Contract):
    name: Name
    mode: Literal["targeted", "full"] = "targeted"
    batch_chars: int = Field(default=8000, ge=512, le=12000)


class RoleJobCreate(RolePlanCreate):
    # Compatibility for the older, combined preparation endpoint.
    provider_id: int | None = Field(default=None, ge=1)
    max_calls: int = Field(default=64, ge=1, le=512)
    request_interval_seconds: int = Field(default=0, ge=0, le=3600)


class RoleJobResume(Contract):
    max_calls: int | None = Field(default=None, ge=1, le=512)
    request_interval_seconds: int | None = Field(default=None, ge=0, le=3600)


class RoleSynthesisRun(Contract):
    limit: int = Field(default=1, ge=1, le=1000)
    preview_key: str | None = Field(default=None, min_length=64, max_length=64)
    retry_failed: bool = False


class RoleSynthesisEdit(Contract):
    cache_key: str = Field(min_length=64, max_length=64)
    revision: int = Field(ge=1)
    raw_content: str = Field(min_length=1, max_length=100000)


class RoleBatchRun(Contract):
    limit: int | None = Field(default=None, ge=1, le=10000)
    stop_at: int | None = Field(default=None, ge=1, le=10000)
    batch_number: int | None = Field(default=None, ge=1, le=10000)
    retry_failed: bool = False
    force: bool = False

    @model_validator(mode="after")
    def one_selection(self):
        if sum(value is not None for value in (self.limit, self.stop_at, self.batch_number)) + self.retry_failed != 1:
            raise ValueError("选择生成批数、截止批号、一个批次或重试全部失败批次")
        if self.force and self.batch_number is None:
            raise ValueError("重新生成仅支持指定批次")
        return self


class RoleBatchEdit(Contract):
    revision: int = Field(ge=0)
    result: ExtractionBatch
