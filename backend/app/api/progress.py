"""阅读位置接口；供阅读器同步和 MCP 外部宿主使用。"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.schemas.agent import ReaderProgressRequest
from app.services import reader_progress_service

router = APIRouter(tags=["伴读"])


@router.get("/books/{book_id}/progress", summary="读取某本书的最近位置")
def get_progress(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    return Response.success(data=reader_progress_service.get(db, book_id))


@router.put("/books/{book_id}/progress", summary="同步某本书的最近位置")
def put_progress(
    payload: ReaderProgressRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    data = reader_progress_service.put(
        db,
        book_id,
        payload.chapter_idx,
        payload.char_offset,
    )
    return Response.success(data=data)


@router.get("/reading-context", summary="最近阅读的书与位置")
def get_reading_context(db: Session = Depends(get_db)):
    return Response.success(data=reader_progress_service.latest(db))
