"""Versioned model-facing notes; old notes remain available only through legacy API."""
from fastapi import APIRouter, Depends, Path
from sqlalchemy.orm import Session
from app.database import get_db
from app.common.response import Response
from app.schemas.reading_note import NoteWrite, NoteQuery, StorylineList, ReviewedImport
from app.services import reading_note_service
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
    return _run(reading_note_service.upsert, db, book_id, payload)


@router.post("/{book_id}/reading-notes/query")
def query(payload: NoteQuery, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(reading_note_service.query, db, book_id, payload)


@router.post("/{book_id}/storylines/query")
def storylines(payload: StorylineList, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(reading_note_service.query, db, book_id, payload, listing=True)


@router.post("/{book_id}/reading-notes/import-reviewed")
def import_reviewed(payload: ReviewedImport, book_id: int = Path(ge=1), db: Session = Depends(get_db)):
    return _run(reading_note_service.import_reviewed, db, book_id, payload)
