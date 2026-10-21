"""书库、书籍详情与章节阅读片段接口。"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.schemas.draft import BookPatch
from app.services import book_service, text_window_service

router = APIRouter(prefix="/books", tags=["书库"])


@router.get("", summary="书库列表")
def get_book_list(db: Session = Depends(get_db)):
    res = book_service.get_book_list(db)
    return Response.success(data=res)


@router.get("/{book_id}", summary="书籍详情")
def get_book(book_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    res = book_service.get_book_detail(db, book_id)
    return Response.success(data=res)


@router.get("/{book_id}/chapters", summary="章节列表")
def get_chapter_list(book_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    res = book_service.get_chapter_list(db, book_id)
    return Response.success(data=res)


@router.get("/{book_id}/chapters/{idx}", summary="读取章节正文")
def get_chapter_content(
    book_id: int = Path(..., ge=1),
    idx: int = Path(..., ge=0),
    db: Session = Depends(get_db),
):
    res = book_service.get_chapter_content(db, book_id, idx)
    return Response.success(data=res)


@router.get("/{book_id}/chapters/{idx}/around", summary="读取当前位置附近的原文")
def read_around(
    book_id: int = Path(..., ge=1),
    idx: int = Path(..., ge=0),
    anchor: str | None = Query(None, max_length=64, description="读者看到的词"),
    char_offset: int | None = Query(None, ge=0, description="全局码点偏移"),
    radius: int = Query(1, ge=0, le=3, description="前后各取几个块"),
    db: Session = Depends(get_db),
):
    data = text_window_service.read_around(
        db, book_id, idx, anchor=anchor, char_offset=char_offset, radius=radius
    )
    return Response.success(data=data)


@router.patch("/{book_id}", summary="编辑书籍元数据")
def update_book(
    payload: BookPatch,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    res = book_service.update_book(db, book_id, payload)
    return Response.success(message="书籍信息已更新", data=res)


@router.delete("/{book_id}", summary="删除书籍及源文件")
def delete_book(book_id: int = Path(..., ge=1), db: Session = Depends(get_db)):
    book_service.delete_book(db, book_id)
    return Response.success(message="书籍已删除", data={"deleted": True})
