"""Versioned model-facing notes; old notes remain available only through legacy API."""
from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session
from app.database import get_db
from app.common.response import Response
from app.schemas.reading_note import NoteWrite, NoteQuery, StorylineList, ReviewedImport
from app.services import reading_note_service, storyline_proposal_service
from app.services.note_service import NoteError

router = APIRouter(prefix="/books", tags=["阅读记忆 v2"])


@router.get("/{book_id}/reading-notes/index")
def index_status(book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    from app.services import note_embedding_service
    # Reuse book/context validation; this endpoint reports counts, never note text.
    try:
        reading_note_service.gate(db,book_id)
        return Response.success(data=note_embedding_service.status(db,book_id))
    except NoteError as exc:
        return Response.success(data={"ok":False,"error":exc.code,"data":exc.data})


def _run(fn, db, book_id, value, **kwargs):
    try:
        data = fn(db, book_id, value, **kwargs)
    except NoteError as exc:
        data = {"ok":False,"error":exc.code,"data":exc.data}
    return Response.success(data=data)


@router.post("/{book_id}/reading-notes/upsert")
def upsert(payload: NoteWrite, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(storyline_proposal_service.submit, db, book_id, payload)


@router.get("/{book_id}/storyline-proposals")
def proposals(book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    try:
        return Response.success(data=storyline_proposal_service.listing(db, book_id))
    except NoteError as exc:
        return Response.success(data={"ok": False, "error": exc.code, "data": exc.data})


@router.post("/{book_id}/storyline-proposals/{proposal_id}/accept")
def accept_proposal(proposal_id: int, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(storyline_proposal_service.decide, db, book_id, proposal_id, accept=True)


@router.post("/{book_id}/storyline-proposals/{proposal_id}/reject")
def reject_proposal(proposal_id: int, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(storyline_proposal_service.decide, db, book_id, proposal_id, accept=False)


@router.post("/{book_id}/reading-notes/query")
def query(payload: NoteQuery, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(reading_note_service.query, db, book_id, payload)


@router.post("/{book_id}/storylines/query")
def storylines(payload: StorylineList, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(reading_note_service.query, db, book_id, payload, listing=True)


@router.get("/{book_id}/storylines/{note_id}")
def storyline_detail(note_id: str, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(reading_note_service.storyline_detail,db,book_id,note_id)


@router.get("/{book_id}/storylines")
def visible_storylines(book_id: int = Path(ge=1), query: str | None = Query(None,min_length=1,max_length=200),
                       cursor: str | None = Query(None,max_length=12000), limit: int = Query(10,ge=1,le=20), db: Session = Depends(get_db)):
    from app.services import storyline_view_service
    return _run(storyline_view_service.listing,db,book_id,query,cursor=cursor,limit=limit)


@router.get("/{book_id}/storylines/{note_id}/nodes/{node_id}/evidence")
def storyline_evidence(note_id: str,node_id: str,book_id: int = Path(ge=1),db: Session = Depends(get_db)):
    from app.services import storyline_view_service
    return _run(storyline_view_service.evidence,db,book_id,note_id,node_id=node_id)


@router.post("/{book_id}/reading-notes/import-reviewed")
def import_reviewed(payload: ReviewedImport, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(reading_note_service.import_reviewed, db, book_id, payload)
