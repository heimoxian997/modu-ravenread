"""上传草稿相关接口。"""

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.schemas.draft import ConfirmRequest, ParseRequest
from app.services import draft_service

router = APIRouter(prefix="/drafts", tags=["上传草稿"])


@router.post("", summary="上传 txt 并创建草稿")
def upload_draft(file: UploadFile = File(...), db: Session = Depends(get_db)):
    res = draft_service.create_draft(db, file)
    return Response.success(message="上传成功", data=res)


@router.post("/{token}/parse", summary="推导章节规则并解析全文")
def parse_draft(
    token: str,
    payload: ParseRequest,
    db: Session = Depends(get_db),
):
    res = draft_service.parse_draft(db, token, payload)
    return Response.success(message="解析完成", data=res)


@router.get("/{token}/content", summary="读取草稿源文件片段")
def get_draft_content(
    token: str,
    start: int = Query(..., ge=0),
    end: int = Query(..., ge=0),
    db: Session = Depends(get_db),
):
    res = draft_service.get_draft_content(db, token, start, end)
    return Response.success(data=res)


@router.post("/{token}/confirm", summary="确认边界并写入书库")
def confirm_draft(
    token: str,
    payload: ConfirmRequest,
    db: Session = Depends(get_db),
):
    res = draft_service.confirm_draft(db, token, payload)
    return Response.success(message="书籍已保存到本机", data=res)
