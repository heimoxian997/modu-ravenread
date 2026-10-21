"""应用内摘要批量任务：单 daemon 线程 + 章间停止。"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.book import Book
from app.models.extract_job import ExtractJob
from app.models.summary_issue import SummaryIssue
from app.services import chapter_extract_service, llm_provider_service

ACTIVE_STATUSES = ("queued", "running", "stopping")

_job_lock = threading.Lock()
_active_job_id: int | None = None
_stop_events: dict[int, threading.Event] = {}


def ensure_job_schema() -> None:
    """给旧库补 check_provider_id 列；新库由 create_all 建出，本函数是 no-op。"""
    from app.database import engine

    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(extract_jobs)").fetchall()
        if not rows or any(row[1] == "check_provider_id" for row in rows):
            return
        conn.exec_driver_sql(
            "ALTER TABLE extract_jobs ADD COLUMN check_provider_id INTEGER"
        )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(sep=" ", timespec="seconds") if value else None


def job_to_dict(
    job: ExtractJob | None, db: Session | None = None
) -> dict[str, Any] | None:
    if job is None:
        return None
    data = {
        "id": job.id,
        "book_id": job.book_id,
        "status": job.status,
        "stage": job.stage,
        "progress": job.progress,
        "message": job.message,
        "planned": job.planned,
        "generated": job.generated,
        "failed": job.failed,
        "current_chapter_idx": job.current_chapter_idx,
        "next_chapter_idx": job.next_chapter_idx,
        "next_chapter_number": job.next_chapter_number,
        "next_chapter_title": job.next_chapter_title,
        "limit": job.limit,
        "stop_at": job.stop_at,
        "force": job.force,
        "retry_failed": job.retry_failed,
        "provider_name": job.provider_name,
        "model": job.model,
        "error": job.error,
        "create_time": _iso(job.create_time),
        "update_time": _iso(job.update_time),
        "finish_time": _iso(job.finish_time),
        "has_errors": bool(job.failed),
    }
    if db is not None:
        issues = (
            db.query(SummaryIssue)
            .filter(SummaryIssue.book_id == job.book_id)
            .order_by(SummaryIssue.chapter_idx)
            .all()
        )
        data["failed_chapters"] = [
            {"chapter_idx": row.chapter_idx, "error": row.error} for row in issues
        ]
    return data


def _book_or_404(db: Session, book_id: int) -> Book:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    return book


def _job_or_404(db: Session, job_id: int) -> ExtractJob:
    job = db.get(ExtractJob, job_id)
    if not job:
        raise BusinessException(message="摘要任务不存在", code=4040)
    return job


def create_job(
    db: Session,
    book_id: int,
    limit: int | None = None,
    stop_at: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
    provider_id: int | None = None,
    check_provider_id: int | None = None,
) -> ExtractJob:
    """校验、规划并创建 queued 任务；不启动线程。"""
    _book_or_404(db, book_id)
    if limit is not None and stop_at is not None:
        raise BusinessException(
            message="limit 和 stop_at 只能选一个", code=4000
        )
    if limit is not None and limit < 1:
        raise BusinessException(message="limit 必须大于等于 1", code=4000)
    if stop_at is not None and stop_at < 1:
        raise BusinessException(message="stop_at 必须大于等于 1", code=4000)

    with _job_lock:
        if _active_job_id is not None:
            raise BusinessException(
                message="已有摘要任务在进行", code=4090
            )
        existing = (
            db.query(ExtractJob)
            .filter(ExtractJob.status.in_(ACTIVE_STATUSES))
            .first()
        )
        if existing:
            raise BusinessException(
                message="已有摘要任务在进行", code=4090
            )

        plan = chapter_extract_service.plan_batch(
            db,
            book_id,
            limit=limit,
            stop_at=stop_at,
            force=force,
            retry_failed=retry_failed,
        )
        if plan["planned"] == 0:
            raise BusinessException(message="没有待处理章节", code=4000)
        provider = llm_provider_service.get_active_provider_config(
            db, provider_id
        )
        job = ExtractJob(
            book_id=book_id,
            status="queued",
            stage="preparing",
            progress=0,
            message="等待开始",
            planned=plan["planned"],
            generated=0,
            failed=0,
            next_chapter_idx=plan["next_chapter_idx"],
            next_chapter_number=plan["next_chapter_number"],
            next_chapter_title=plan["next_chapter_title"],
            limit=limit,
            stop_at=stop_at,
            force=force,
            retry_failed=retry_failed,
            provider_id=provider_id,
            check_provider_id=check_provider_id,
            provider_name=provider["provider_name"],
            model=provider["model"],
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        return job


def start_job(
    db: Session,
    book_id: int,
    limit: int | None = None,
    stop_at: int | None = None,
    force: bool = False,
    retry_failed: bool = False,
    provider_id: int | None = None,
    check_provider_id: int | None = None,
) -> ExtractJob:
    """创建任务并启动 daemon 工作线程。"""
    global _active_job_id
    job = create_job(
        db,
        book_id,
        limit=limit,
        stop_at=stop_at,
        force=force,
        retry_failed=retry_failed,
        provider_id=provider_id,
        check_provider_id=check_provider_id,
    )
    event = threading.Event()
    with _job_lock:
        if _active_job_id is not None:
            job.status = "failed"
            job.error = "已有摘要任务在进行"
            job.message = "任务失败"
            job.finish_time = datetime.now()
            db.commit()
            raise BusinessException(
                message="已有摘要任务在进行", code=4090
            )
        _active_job_id = job.id
        _stop_events[job.id] = event

    try:
        thread = threading.Thread(
            target=run_job,
            args=(job.id,),
            name=f"summary-job-{job.id}",
            daemon=True,
        )
        thread.start()
    except Exception as exc:
        with _job_lock:
            if _active_job_id == job.id:
                _active_job_id = None
            _stop_events.pop(job.id, None)
        job.status = "failed"
        job.error = str(exc)
        job.message = "任务线程启动失败"
        job.finish_time = datetime.now()
        db.commit()
        raise BusinessException(
            message=f"摘要任务线程启动失败：{exc}"
        ) from exc
    return job


def _update_progress(
    job_id: int,
    done: int,
    total: int,
    chapter_idx: int,
    result: dict[str, Any],
) -> None:
    """独立短 Session 写进度，不覆盖 job.error。"""
    session = SessionLocal()
    try:
        job = session.get(ExtractJob, job_id)
        if not job or job.status not in ("running", "stopping"):
            return
        if result.get("status") == "success":
            job.generated += 1
            suffix = "已成功"
        else:
            job.failed += 1
            error = (result.get("error") or "未知错误")[:180]
            suffix = f"已失败：{error}"
        job.current_chapter_idx = chapter_idx
        job.progress = min(100, int(done / max(1, total) * 100))
        job.message = f"第 {chapter_idx + 1} 章 {suffix}"
        if job.status == "stopping":
            job.message += "（正在停止）"
        try:
            next_info = chapter_extract_service.get_next_pending(
                session, job.book_id
            )
            job.next_chapter_idx = next_info["chapter_idx"]
            job.next_chapter_number = next_info["chapter_number"]
            job.next_chapter_title = next_info["chapter_title"]
        except Exception:
            pass
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def run_job(job_id: int) -> None:
    """工作线程入口；测试可直接同步调用。"""
    global _active_job_id
    db = SessionLocal()
    try:
        job = db.get(ExtractJob, job_id)
        if not job:
            return
        job.status = "running"
        job.stage = "running"
        job.progress = 0
        job.message = "开始生成"
        job.error = None
        job.generated = 0
        job.failed = 0
        job.finish_time = None
        db.commit()

        with _job_lock:
            event = _stop_events.get(job_id)
            if event is None:
                event = threading.Event()
                _stop_events[job_id] = event

        def progress(done, total, chapter_idx, result):
            _update_progress(job_id, done, total, chapter_idx, result)

        result = chapter_extract_service.extract_batch(
            db,
            job.book_id,
            limit=job.limit,
            stop_at=job.stop_at,
            force=job.force,
            retry_failed=job.retry_failed,
            provider_id=job.provider_id,
            check_provider_id=job.check_provider_id,
            progress=progress,
            should_stop=event.is_set,
        )

        db.expire_all()
        job = db.get(ExtractJob, job_id)
        if not job:
            return
        if job.status == "stopping" or result["stopped"]:
            job.status = "stopped"
            job.stage = "stopped"
            job.message = "已停止"
        else:
            job.status = "done"
            job.stage = "done"
            job.progress = 100
            notes = []
            if result["failed"]:
                notes.append(f"{result['failed']} 章失败")
            if result.get("alias_dropped"):
                notes.append(f"修正 {result['alias_dropped']} 条称谓")
            if result.get("alias_flagged"):
                notes.append(f"{result['alias_flagged']} 条称谓待核对")
            if result.get("check_disabled"):
                notes.append("检查模型连续空返回，已跳过后续复核")
            job.message = "批量完成" + (
                f"（{'，'.join(notes)}）" if notes else ""
            )
        job.generated = result["generated"]
        job.failed = result["failed"]
        job.next_chapter_idx = result["next_chapter_idx"]
        job.next_chapter_number = result["next_chapter_number"]
        job.next_chapter_title = result["next_chapter_title"]
        job.finish_time = datetime.now()
        job.error = None
        db.commit()
    except Exception as exc:
        db.rollback()
        job = db.get(ExtractJob, job_id)
        if job:
            error = exc.message if isinstance(exc, BusinessException) else str(exc)
            job.status = "failed"
            job.error = error
            job.message = "批量失败"
            job.finish_time = datetime.now()
            try:
                next_info = chapter_extract_service.get_next_pending(
                    db, job.book_id
                )
                job.next_chapter_idx = next_info["chapter_idx"]
                job.next_chapter_number = next_info["chapter_number"]
                job.next_chapter_title = next_info["chapter_title"]
            except Exception:
                pass
            db.commit()
    finally:
        with _job_lock:
            if _active_job_id == job_id:
                _active_job_id = None
            _stop_events.pop(job_id, None)
        db.close()


def request_stop(db: Session, book_id: int) -> ExtractJob:
    _book_or_404(db, book_id)
    job = (
        db.query(ExtractJob)
        .filter(
            ExtractJob.book_id == book_id,
            ExtractJob.status.in_(ACTIVE_STATUSES),
        )
        .order_by(ExtractJob.id.desc())
        .first()
    )
    if not job:
        raise BusinessException(
            message="当前没有正在运行的摘要任务", code=4000
        )
    job.status = "stopping"
    job.stage = "stopping"
    job.message = "正在停止，当前章完成后停止"
    db.commit()
    with _job_lock:
        event = _stop_events.get(job.id)
        if event is None:
            event = threading.Event()
            _stop_events[job.id] = event
        event.set()
    db.refresh(job)
    return job


def get_latest_job(db: Session, book_id: int) -> ExtractJob | None:
    _book_or_404(db, book_id)
    return (
        db.query(ExtractJob)
        .filter(ExtractJob.book_id == book_id)
        .order_by(ExtractJob.id.desc())
        .first()
    )


def is_book_job_active(db: Session, book_id: int) -> bool:
    return (
        db.query(ExtractJob)
        .filter(
            ExtractJob.book_id == book_id,
            ExtractJob.status.in_(ACTIVE_STATUSES),
        )
        .first()
        is not None
    )


def get_job(db: Session, job_id: int, book_id: int | None = None) -> ExtractJob:
    job = _job_or_404(db, job_id)
    if book_id is not None and job.book_id != book_id:
        raise BusinessException(message="摘要任务不存在", code=4040)
    return job


def mark_interrupted_jobs(db: Session) -> int:
    global _active_job_id
    jobs = (
        db.query(ExtractJob)
        .filter(ExtractJob.status.in_(ACTIVE_STATUSES))
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
        _stop_events.clear()
    return len(jobs)
