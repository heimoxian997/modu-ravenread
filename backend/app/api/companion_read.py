"""显式开启的跟读接口；不向 MCP 暴露全书权限。"""
from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.database import get_db
from app.common.response import Response
from app.models.companion_read import CompanionRead
from app.services import companion_read_service as service, agent_context_service
from app.services import reader_progress_service
from app.common.exceptions import BusinessException

router = APIRouter(prefix="/books", tags=["跟读"])


class Start(BaseModel):
    chapter_idx: int = Field(ge=0)
    retry: bool = False


@router.get("/{book_id}/companion-read/{chapter_idx}")
def status(book_id: int, chapter_idx: int, db: Session = Depends(get_db)):
    progress = reader_progress_service.get(db, book_id)
    if not progress.get("available") or chapter_idx > progress["chapter_idx"]:
        raise BusinessException(message="该章节超出当前阅读进度", code=4000)
    text = service.chapter_text(db, book_id, chapter_idx)
    _, row = service.find(db, book_id, chapter_idx, text)
    return Response.success(data=service.snapshot(row) if row else None)


@router.post("/{book_id}/companion-read")
def start(book_id: int, payload: Start, tasks: BackgroundTasks, db: Session = Depends(get_db)):
    progress = reader_progress_service.get(db, book_id)
    if not progress.get("available") or payload.chapter_idx > progress["chapter_idx"]:
        raise BusinessException(message="请先在阅读器打开该章节，再开启跟读", code=4000)
    state, work = service.prepare(db, book_id, payload.chapter_idx, payload.retry)
    if work:
        tasks.add_task(service.run, state["id"], *work)
    return Response.success(data={**state, "started_here": work is not None})


@router.delete("/{book_id}/companion-read/{job_id}")
def cancel(book_id: int, job_id: int, attempt_id: str, db: Session = Depends(get_db)):
    agent_context_service.book_or_404(db, book_id)
    with service._control_lock:
        changed = db.query(CompanionRead).filter(CompanionRead.book_id == book_id,
            CompanionRead.id == job_id, CompanionRead.attempt_id == attempt_id,
            CompanionRead.status.in_(["queued", "running"])
        ).update({"status": "cancelled"})
        db.commit()
    return Response.success(data={"cancelled": bool(changed)})
