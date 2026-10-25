"""阅读笔记接口；供 MCP 外部宿主写入与查询。

业务错误按工具信封返回（HTTP 200 + ok=false），让 MCP 客户端拿到
结构化的错误码而不是网关错误。
"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.schemas.note import NoteQueryRequest, NoteUpsertRequest
from app.services import note_service
from app.services.note_service import NoteError

router = APIRouter(prefix="/books", tags=["笔记"])


@router.post(
    "/{book_id}/notes/upsert",
    summary="写入或更新一条阅读笔记",
)
def upsert_note(
    payload: NoteUpsertRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    try:
        data = note_service.upsert(db, book_id, payload.model_dump())
    except NoteError as exc:
        data = {"ok": False, "error": exc.code, "data": exc.data}
    return Response.success(data=data)


@router.post(
    "/{book_id}/notes/query",
    summary="查询阅读笔记（带覆盖度与缺口）",
)
def query_notes(
    payload: NoteQueryRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    try:
        data = note_service.query(db, book_id, payload.model_dump())
    except NoteError as exc:
        data = {"ok": False, "error": exc.code, "data": exc.data}
    return Response.success(data=data)
