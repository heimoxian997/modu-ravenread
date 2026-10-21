"""文本索引任务服务。

FastAPI 请求线程只负责创建/查询任务，实际建库在单一 daemon 线程中执行。
SQLite 保存任务状态，Qdrant 保存向量；全局只允许一个建库任务。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app import config
from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.chunk import Chunk
from app.models.index_job import IndexJob
from app.services import embedding_service, text_index_service

JOB_KIND = "text_vector"
ACTIVE_STATUSES = ("queued", "running")

logger = logging.getLogger(__name__)
_job_lock = threading.Lock()
_active_job_id: int | None = None


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _iso_time(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def job_to_dict(job: IndexJob | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "book_id": job.book_id,
        "kind": job.kind,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "message": job.message,
        "point_count": job.point_count,
        "error": job.error,
        "create_time": _iso_time(job.create_time),
        "update_time": _iso_time(job.update_time),
        "finish_time": _iso_time(job.finish_time),
    }


def create_job(db: Session, book_id: int) -> IndexJob:
    """创建一个 queued 任务；全局仅允许一个 queued/running 任务。"""
    _book_or_404(db, book_id)
    child_count = (
        db.query(Chunk)
        .filter(
            Chunk.book_id == book_id,
            Chunk.version == config.TEXT_INDEX_VERSION,
            Chunk.level == "child",
        )
        .count()
    )
    if child_count == 0:
        raise BusinessException(
            message="这本书还没有子块，请先完成正文切块后再建文本索引。"
        )

    with _job_lock:
        if _active_job_id is not None:
            raise BusinessException(
                message="已有索引任务在进行，请等待完成", code=4090
            )
        existing = (
            db.query(IndexJob)
            .filter(IndexJob.status.in_(ACTIVE_STATUSES))
            .first()
        )
        if existing:
            raise BusinessException(
                message="已有索引任务在进行，请等待完成", code=4090
            )

        job = IndexJob(
            book_id=book_id,
            kind=JOB_KIND,
            status="queued",
            stage="preparing",
            progress=0,
            message="等待开始",
            point_count=0,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job


def start_job(db: Session, book_id: int) -> IndexJob:
    """创建任务并启动后台线程。"""
    global _active_job_id
    job = create_job(db, book_id)
    with _job_lock:
        if _active_job_id is not None:
            # 理论上不会发生；发生则释放刚创建的任务，避免悬挂 queued。
            job.status = "failed"
            job.message = "已有任务在进行"
            job.error = "已有索引任务在进行"
            job.finish_time = datetime.now()
            db.commit()
            raise BusinessException(
                message="已有索引任务在进行，请等待完成", code=4090
            )
        _active_job_id = job.id

    try:
        thread = threading.Thread(
            target=run_job,
            args=(job.id,),
            name=f"text-index-job-{job.id}",
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        with _job_lock:
            if _active_job_id == job.id:
                _active_job_id = None
        job.status = "failed"
        job.message = "任务线程启动失败"
        job.error = str(exc)
        job.finish_time = datetime.now()
        db.commit()
        raise BusinessException(message=f"索引任务线程启动失败：{exc}") from exc

    return job


def _update_progress(job_id: int, stage: str, percent: int, message: str) -> None:
    """用独立短 Session 写进度；失败不影响建库。"""
    session = SessionLocal()
    try:
        job = session.get(IndexJob, job_id)
        if not job or job.status not in ACTIVE_STATUSES:
            return
        job.status = "running"
        job.stage = stage
        job.progress = max(0, min(100, int(percent)))
        job.message = message
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def run_job(job_id: int) -> None:
    """后台线程入口；测试也可以直接同步调用。"""
    global _active_job_id
    db = SessionLocal()
    try:
        job = db.get(IndexJob, job_id)
        if not job:
            return
        job.status = "running"
        job.stage = "preparing"
        job.progress = 0
        job.message = "准备建库"
        job.error = None
        job.point_count = 0
        job.finish_time = None
        db.commit()
        db.refresh(job)
        book_id = job.book_id

        def progress(stage: str, percent: int, message: str) -> None:
            _update_progress(job_id, stage, percent, message)

        result = text_index_service.build_text_index(
            db, book_id, progress=progress
        )

        db.expire_all()
        job = db.get(IndexJob, job_id)
        if not job:
            return
        job.status = "done"
        job.stage = "done"
        job.progress = 100
        job.point_count = int(result.get("point_count", 0))
        job.message = "建库完成"
        job.error = None
        job.finish_time = datetime.now()
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(IndexJob, job_id)
        if job:
            error = exc.message if isinstance(exc, BusinessException) else str(exc)
            job.status = "failed"
            job.error = error
            job.message = "建库失败"
            job.finish_time = datetime.now()
            db.commit()
    finally:
        with _job_lock:
            if _active_job_id == job_id:
                _active_job_id = None
        db.close()


def is_busy(book_id: int) -> bool:
    """内存中有活动任务，或数据库中有该书的 queued/running 任务。"""
    with _job_lock:
        if _active_job_id is not None:
            return True

    db = SessionLocal()
    try:
        return (
            db.query(IndexJob)
            .filter(
                IndexJob.book_id == book_id,
                IndexJob.status.in_(ACTIVE_STATUSES),
            )
            .first()
            is not None
        )
    finally:
        db.close()


def get_latest_job(db: Session, book_id: int) -> IndexJob | None:
    return (
        db.query(IndexJob)
        .filter(IndexJob.book_id == book_id, IndexJob.kind == JOB_KIND)
        .order_by(IndexJob.id.desc())
        .first()
    )


def get_status(db: Session, book_id: int) -> dict[str, Any]:
    """状态接口数据；Qdrant 不可用时返回 available=false，不抛 500。"""
    _book_or_404(db, book_id)
    latest = get_latest_job(db, book_id)

    available = True
    stats_error: str | None = None
    try:
        stats = text_index_service.get_index_stats(book_id)
    except Exception:
        # 建库写入与状态轮询可能紧邻发生；给 Qdrant 一次短暂重试，
        # 避免把内部 IndexError 之类的瞬时异常直接显示成红色错误。
        time.sleep(0.2)
        try:
            stats = text_index_service.get_index_stats(book_id)
        except BusinessException as exc:
            available = False
            stats_error = exc.message
            stats = {"point_count": 0, "dim": 0}
        except Exception:
            logger.exception("读取本地 Qdrant 索引状态失败")
            available = False
            stats_error = "本地 Qdrant 暂时不可用，请刷新重试。"
            stats = {"point_count": 0, "dim": 0}

    try:
        embedding_status = embedding_service.get_status()
        model = embedding_status.get("model")
        device = embedding_status.get("device")
    except Exception:
        model = config.settings.EMBEDDING_MODEL
        device = config.settings.EMBEDDING_DEVICE

    point_count = int(stats.get("point_count", 0))
    warning = None
    if (
        latest is not None
        and latest.status in ("interrupted", "failed")
        and latest.stage == "upserting"
    ):
        warning = "索引可能不完整，请重新建库"

    data = {
        "built": available and point_count > 0,
        "available": available,
        "point_count": point_count,
        "dim": int(stats.get("dim", 0)),
        "collection": text_index_service.get_collection_name(),
        "qdrant_path": text_index_service.get_qdrant_path().as_posix(),
        "model": model,
        "device": device,
        "busy": is_busy(book_id),
        "job": job_to_dict(latest),
        "warning": warning,
    }
    if not available:
        data["error"] = stats_error or "本地 Qdrant 不可用"
    return data


def mark_interrupted_jobs(db: Session) -> int:
    """服务启动时把 queued/running 任务改为 interrupted。"""
    global _active_job_id
    jobs = (
        db.query(IndexJob)
        .filter(IndexJob.status.in_(ACTIVE_STATUSES))
        .all()
    )
    now = datetime.now()
    for job in jobs:
        job.status = "interrupted"
        job.message = "服务重启，任务已中断"
        job.finish_time = now
    db.commit()
    with _job_lock:
        _active_job_id = None
    return len(jobs)


def search(
    db: Session, book_id: int, query: str, top_k: int = 5
) -> list[dict[str, Any]]:
    if is_busy(book_id):
        raise BusinessException(
            message="已有索引任务在进行，请等待完成", code=4090
        )
    return text_index_service.search_text_index(db, book_id, query, top_k=top_k)


def delete_index(db: Session, book_id: int) -> int:
    _book_or_404(db, book_id)
    if is_busy(book_id):
        raise BusinessException(
            message="已有索引任务在进行，请等待完成", code=4090
        )
    return text_index_service.delete_book_index(book_id)
