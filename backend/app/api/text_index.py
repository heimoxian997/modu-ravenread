"""文本索引任务与检索接口。"""

from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.schemas.text_index import TextSearchRequest
from app.services import text_index_job_service

router = APIRouter(prefix="/books", tags=["文本索引"])


@router.get("/{book_id}/text-index", summary="文本索引状态与最新任务")
def get_text_index_status(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    res = text_index_job_service.get_status(db, book_id)
    return Response.success(data=res)


@router.post("/{book_id}/text-index", summary="启动文本索引任务")
def start_text_index(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    job = text_index_job_service.start_job(db, book_id)
    return Response.success(
        message="索引任务已启动",
        data=text_index_job_service.job_to_dict(job),
    )


@router.post("/{book_id}/search", summary="检索本地文本索引")
def search_text_index(
    payload: TextSearchRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    if payload.rerank:
        res = text_index_job_service.search_compare(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            recall=payload.recall,
            chapter_idx=payload.chapter_idx,
        )
    else:
        res = text_index_job_service.search(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            chapter_idx=payload.chapter_idx,
        )
    return Response.success(data=res)


@router.delete("/{book_id}/text-index", summary="删除本地文本索引")
def delete_text_index(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    deleted = text_index_job_service.delete_index(db, book_id)
    return Response.success(message="文本索引已删除", data={"deleted": deleted})
