from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query
from sqlalchemy.orm import Session
from typing import Literal
from app.common.response import Response
from app.database import get_db
from app.schemas.correction import CorrectionDecision
from app.services import correction_service as service
from app.services.note_service import NoteError

router=APIRouter(prefix="/books",tags=["纠错审核"])


def run(fn,*args,**kwargs):
    try:
        return Response.success(data={"ok":True,"data":fn(*args,**kwargs)})
    except NoteError as exc:
        return Response.success(data={"ok":False,"error":exc.code,"data":exc.data})


@router.get("/{book_id}/corrections")
def list_reports(book_id:int=Path(ge=1),status:Literal["open","pending","applied","rejected"]|None=Query(None),
                 page:int=Query(1,ge=1),limit:int=Query(20,ge=1,le=50),db:Session=Depends(get_db)):
    return run(service.list_reports,db,book_id,status,page,limit)


@router.post("/{book_id}/corrections/{issue_id}/approve")
def approve(issue_id:str,payload:CorrectionDecision,tasks:BackgroundTasks,book_id:int=Path(ge=1),db:Session=Depends(get_db)):
    try:
        data=service.decide(db,book_id,issue_id,payload)
    except NoteError as exc:
        return Response.success(data={"ok":False,"error":exc.code,"data":exc.data})
    if payload.rebuild and data["rebuild_layers"]:
        tasks.add_task(service.rebuild_issue,book_id,issue_id)
    return Response.success(data={"ok":True,"data":data})


@router.post("/{book_id}/corrections/{issue_id}/reject")
def reject(issue_id:str,book_id:int=Path(ge=1),db:Session=Depends(get_db)):
    return run(service.decide,db,book_id,issue_id,reject=True)


@router.post("/{book_id}/corrections/{issue_id}/rebuild")
def rebuild(issue_id:str,tasks:BackgroundTasks,book_id:int=Path(ge=1),db:Session=Depends(get_db)):
    try:
        row=service._issue(db,book_id,issue_id)
        if row.fix_status!="applied":
            raise NoteError("not_applied","请先审核并应用修改")
    except NoteError as exc:
        return Response.success(data={"ok":False,"error":exc.code,"data":exc.data})
    tasks.add_task(service.rebuild_issue,book_id,issue_id)
    return Response.success(data={"ok":True,"data":{"issue_id":issue_id,"status":"queued"}})
