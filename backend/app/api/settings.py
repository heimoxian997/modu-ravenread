"""本地设置、嵌入模型和模型服务接口。"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.schemas.llm import (
    LlmProviderCreate,
    LlmProviderUpdate,
    SetActiveProviderRequest,
)
from app.services import embedding_service, llm_provider_service

router = APIRouter(prefix="/settings", tags=["本地设置"])


@router.get("/embedding", summary="查看本地嵌入模型状态")
def get_embedding_status():
    """只读状态接口：不下载、不加载模型。"""
    return Response.success(data=embedding_service.get_status())


@router.get("/llm", summary="模型服务列表与预设")
def get_llm_settings(db: Session = Depends(get_db)):
    return Response.success(data=llm_provider_service.list_providers(db))


@router.post("/llm/providers", summary="新增模型服务")
def create_llm_provider(
    payload: LlmProviderCreate,
    db: Session = Depends(get_db),
):
    data = llm_provider_service.create_provider(db, payload)
    return Response.success(message="模型服务已保存", data=data)


@router.put("/llm/providers/{provider_id}", summary="修改模型服务")
def update_llm_provider(
    payload: LlmProviderUpdate,
    provider_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    data = llm_provider_service.update_provider(db, provider_id, payload)
    return Response.success(message="模型服务已更新", data=data)


@router.delete("/llm/providers/{provider_id}", summary="删除模型服务")
def delete_llm_provider(
    provider_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    deleted = llm_provider_service.delete_provider(db, provider_id)
    return Response.success(
        message="模型服务已删除", data={"deleted": deleted}
    )


@router.post("/llm/providers/{provider_id}/test", summary="测试模型连通性")
def test_llm_provider(
    provider_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    data = llm_provider_service.test_provider(db, provider_id)
    return Response.success(data=data)


@router.post("/llm/active", summary="设置当前模型")
def set_active_llm_provider(
    payload: SetActiveProviderRequest,
    db: Session = Depends(get_db),
):
    data = llm_provider_service.set_active(db, payload.provider_id)
    return Response.success(message="当前模型已更新", data=data)


@router.get("/status", summary="模型服务顶部状态")
def get_settings_status(db: Session = Depends(get_db)):
    return Response.success(data=llm_provider_service.get_status(db))
