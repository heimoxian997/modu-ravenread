"""正文切块接口。"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.services import chunk_service

router = APIRouter(prefix="/books", tags=["正文切块"])


@router.post("/{book_id}/chunks", summary="生成或重建正文切块")
def build_chunks(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    res = chunk_service.build_chunks(db, book_id)
    return Response.success(message="正文切块完成", data=res)


@router.get("/{book_id}/chunks/overview", summary="切块概览与统计")
def get_chunk_overview(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    res = chunk_service.get_overview(db, book_id)
    return Response.success(data=res)


@router.get("/{book_id}/chunks", summary="查看某章父块与子块")
def list_chapter_chunks(
    book_id: int = Path(..., ge=1),
    chapter_idx: int = Query(..., ge=0),
    db: Session = Depends(get_db),
):
    res = chunk_service.list_chapter_chunks(db, book_id, chapter_idx)
    return Response.success(data=res)


@router.delete("/{book_id}/chunks", summary="清除本书切块")
def clear_chunks(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    res = chunk_service.clear_chunks(db, book_id)
    return Response.success(message="切块已清除", data=res)
