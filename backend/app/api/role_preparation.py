"""Independent full-book role preparation; not registered as companion/MCP tools."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session

from app.common.response import Response
from app.database import get_db
from app.schemas.role_jobs import RolePlanCreate, RoleJobCreate, RoleJobResume, RoleBatchRun, RoleBatchEdit, RoleSynthesisRun, RoleSynthesisEdit
from app.services import role_book_service as books
from app.services import role_preparation_job_service as jobs
from app.services import role_batch_service as batches

router = APIRouter(prefix="/books/{book_id}/role-chat", tags=["角色准备"])


@router.post("/preparation/plans/preview")
def preview_plan(payload: RolePlanCreate, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.preview(db, book_id, payload))


@router.post("/preparation/plans")
def prepare_plan(payload: RolePlanCreate, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=jobs.job_to_dict(batches.prepare(db, book_id, payload)))


@router.get("/preparation/jobs/{job_id}/batches")
def batch_list(book_id: int = Path(ge=1), job_id: int = Path(ge=1), offset: int = Query(0, ge=0),
               limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return Response.success(data=batches.list_batches(db, book_id, job_id, offset, limit))


@router.get("/preparation/jobs/{job_id}/batches/{number}")
def batch_detail(book_id: int = Path(ge=1), job_id: int = Path(ge=1), number: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.batch_detail(db, book_id, job_id, number))


@router.put("/preparation/jobs/{job_id}/batches/{number}")
def edit_batch(payload: RoleBatchEdit, book_id: int = Path(ge=1), job_id: int = Path(ge=1), number: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.edit_batch(db, book_id, job_id, number, payload))


@router.post("/preparation/jobs/{job_id}/extract")
def extract_batches(payload: RoleBatchRun, book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=jobs.job_to_dict(batches.extract(db, book_id, job_id, payload)))


@router.post("/preparation/jobs/{job_id}/synthesize")
def synthesize_profile(payload: RoleSynthesisRun, book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=jobs.job_to_dict(batches.synthesize(db, book_id, job_id, payload)))


@router.get("/preparation/jobs/{job_id}/synthesis/preview")
def synthesis_preview(book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.synthesis_preview(db, book_id, job_id))


@router.get("/preparation/jobs/{job_id}/synthesis/response")
def synthesis_response(book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.synthesis_response(db, book_id, job_id))


@router.put("/preparation/jobs/{job_id}/synthesis/response")
def edit_synthesis_response(payload: RoleSynthesisEdit, book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.edit_synthesis_response(db, book_id, job_id, payload))


@router.delete("/preparation/jobs/{job_id}")
def delete_plan(book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.delete_plan(db, book_id, job_id))


@router.delete("/roles/{role_id}")
def delete_role(book_id: int = Path(ge=1), role_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=batches.delete_role(db, book_id, role_id))


@router.get("/characters")
def characters(book_id: int = Path(ge=1), query: str = Query("", max_length=100),
               offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100),
               db: Session = Depends(get_db)):
    return Response.success(data=books.characters(db, book_id, query, offset, limit))


@router.post("/preparation/preview")
def preview(payload: RoleJobCreate, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=jobs.preview(db, book_id, payload))


@router.post("/preparation/jobs")
def start(payload: RoleJobCreate, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    job = jobs.start_job(db, book_id, payload)
    return Response.success(data=jobs.job_to_dict(job))


@router.get("/preparation/jobs")
def list_jobs(book_id: int = Path(ge=1), offset: int = Query(0, ge=0),
              limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return Response.success(data=jobs.list_jobs(db, book_id, offset, limit))


@router.get("/roles")
def list_roles(book_id: int = Path(ge=1), offset: int = Query(0, ge=0),
               limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    return Response.success(data=jobs.list_roles(db, book_id, offset, limit))


@router.get("/preparation/jobs/{job_id}")
def get_job(book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=jobs.job_to_dict(jobs.get_job(db, book_id, job_id)))


@router.post("/preparation/jobs/{job_id}/pause")
def pause(book_id: int = Path(ge=1), job_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=jobs.job_to_dict(jobs.request_pause(db, book_id, job_id)))


@router.post("/preparation/jobs/{job_id}/resume")
def resume(payload: RoleJobResume, book_id: int = Path(ge=1), job_id: int = Path(ge=1),
           db: Session = Depends(get_db)):
    return Response.success(data=jobs.job_to_dict(jobs.resume_job(db, book_id, job_id, payload)))


@router.get("/roles/{role_id}/profile")
def profile(book_id: int = Path(ge=1), role_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return Response.success(data=jobs.latest_profile(db, book_id, role_id))


@router.get("/profiles/{profile_id}/evidence")
def evidence(book_id: int = Path(ge=1), profile_id: int = Path(ge=1),
             offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=50),
             observation_id: str | None = Query(None, min_length=1, max_length=100),
             db: Session = Depends(get_db)):
    return Response.success(data=jobs.profile_evidence(db, book_id, profile_id, offset, limit, observation_id))
