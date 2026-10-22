"""章节摘要抽取与编辑接口。"""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.common.response import Response
from app.database import get_db
from app.schemas.summary import ExtractionParsedPatch, SummaryUpdate
from app.schemas.summary_index import (
    SummaryIndexBuildRequest,
    SummarySearchRequest,
)
from app.schemas.summary_job import BatchJobCreate
from app.services import (
    chapter_extract_service,
    summary_index_service,
    summary_job_service,
)

router = APIRouter(prefix="/books", tags=["章节摘要"])


@router.post(
    "/{book_id}/chapters/{chapter_idx}/extract",
    summary="同步抽取单章结构化摘要",
)
def extract_chapter(
    book_id: int = Path(..., ge=1),
    chapter_idx: int = Path(..., ge=0),
    provider_id: int | None = Query(default=None, ge=1),
    check_provider_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    data = chapter_extract_service.extract_chapter(
        db,
        book_id,
        chapter_idx,
        provider_id=provider_id,
        check_provider_id=check_provider_id,
    )
    return Response.success(data=data)


@router.get(
    "/{book_id}/summaries/overview",
    summary="章节摘要概览",
)
def summaries_overview(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    return Response.success(
        data=chapter_extract_service.get_overview(db, book_id)
    )


@router.get("/{book_id}/summaries", summary="章节摘要列表")
def list_summaries(
    book_id: int = Path(..., ge=1),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    return Response.success(
        data=chapter_extract_service.list_summaries(
            db, book_id, page=page, page_size=page_size, status=status
        )
    )


@router.post(
    "/{book_id}/summaries/jobs",
    summary="启动摘要批量任务",
)
def start_summary_job(
    payload: BatchJobCreate,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    job = summary_job_service.start_job(
        db,
        book_id,
        limit=payload.limit,
        stop_at=payload.stop_at,
        force=payload.force,
        retry_failed=payload.retry_failed,
        provider_id=payload.provider_id,
        check_provider_id=payload.check_provider_id,
    )
    return Response.success(
        message="摘要任务已启动",
        data=summary_job_service.job_to_dict(job, db),
    )


@router.get(
    "/{book_id}/summaries/jobs/latest",
    summary="最新摘要批量任务",
)
def latest_summary_job(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    job = summary_job_service.get_latest_job(db, book_id)
    return Response.success(data=summary_job_service.job_to_dict(job, db))


@router.get(
    "/{book_id}/summaries/jobs/{job_id}",
    summary="指定摘要批量任务",
)
def get_summary_job(
    book_id: int = Path(..., ge=1),
    job_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    job = summary_job_service.get_job(db, job_id, book_id=book_id)
    return Response.success(data=summary_job_service.job_to_dict(job, db))


@router.post(
    "/{book_id}/summaries/jobs/stop",
    summary="停止当前摘要批量任务",
)
def stop_summary_job(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    job = summary_job_service.request_stop(db, book_id)
    return Response.success(
        message="正在停止，当前章完成后停止",
        data=summary_job_service.job_to_dict(job, db),
    )


@router.get(
    "/{book_id}/summary-index",
    summary="摘要向量索引状态",
)
def summary_index_status(
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    try:
        data = summary_index_service.get_summary_index_status(db, book_id)
        return Response.success(data={"available": True, **data, "error": None})
    except BusinessException as exc:
        if exc.code == 4040:
            raise
        return Response.success(
            data={
                "available": False,
                "total_main": 0,
                "summary_count": 0,
                "indexed_count": 0,
                "complete": False,
                "needs_rebuild": False,
                "collection": summary_index_service.get_collection_name(),
                "dim": 1024,
                "error": exc.message,
            }
        )


@router.post(
    "/{book_id}/summary-index",
    summary="重建摘要向量索引",
)
def build_summary_index(
    payload: SummaryIndexBuildRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    if summary_job_service.is_book_job_active(db, book_id):
        raise BusinessException(
            message="摘要正在批量生成，完成后再重建索引", code=4090
        )
    if summary_index_service.is_building():
        raise BusinessException(message="摘要索引正在重建", code=4090)
    data = summary_index_service.build_summary_index(
        db, book_id, allow_partial=payload.allow_partial
    )
    return Response.success(message="摘要索引已重建", data=data)


@router.post(
    "/{book_id}/summary-search",
    summary="检索摘要向量",
)
def search_summary_index(
    payload: SummarySearchRequest,
    book_id: int = Path(..., ge=1),
    db: Session = Depends(get_db),
):
    if summary_index_service.is_building():
        raise BusinessException(message="索引正在重建", code=4090)
    if payload.rerank:
        data = summary_index_service.search_summary_index_compare(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            recall=payload.recall,
            chapter_idx=payload.chapter_idx,
        )
    else:
        data = summary_index_service.search_summary_index(
            db,
            book_id,
            payload.query,
            top_k=payload.top_k,
            chapter_idx=payload.chapter_idx,
        )
    return Response.success(data=data)


@router.get(
    "/{book_id}/summaries/{chapter_idx}",
    summary="章节摘要详情",
)
def get_summary(
    book_id: int = Path(..., ge=1),
    chapter_idx: int = Path(..., ge=0),
    db: Session = Depends(get_db),
):
    return Response.success(
        data=chapter_extract_service.get_summary_detail(
            db, book_id, chapter_idx
        )
    )


@router.patch(
    "/{book_id}/chapters/{chapter_idx}/extraction",
    summary="编辑原始人物结构与世界观条目",
)
def update_extraction(
    payload: ExtractionParsedPatch,
    book_id: int = Path(..., ge=1),
    chapter_idx: int = Path(..., ge=0),
    db: Session = Depends(get_db),
):
    data = chapter_extract_service.update_extraction_parsed(
        db, book_id, chapter_idx, payload
    )
    return Response.success(message="抽取结构已保存", data=data)


@router.put(
    "/{book_id}/summaries/{chapter_idx}",
    summary="编辑章节摘要",
)
def update_summary(
    payload: SummaryUpdate,
    book_id: int = Path(..., ge=1),
    chapter_idx: int = Path(..., ge=0),
    db: Session = Depends(get_db),
):
    data = chapter_extract_service.update_summary(
        db, book_id, chapter_idx, payload
    )
    return Response.success(message="摘要已保存", data=data)
