from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.orm import Session
from app.common.response import Response
from app.database import get_db
from app.schemas.bookmark import BookmarkCreate, BookmarkPatch
from app.services import bookmark_service

router=APIRouter(prefix="/books/{book_id}/bookmarks",tags=["阅读书签"])


@router.get("")
def list_bookmarks(book_id:int=Path(ge=1),page:int=Query(1,ge=1),limit:int=Query(20,ge=1,le=100),db:Session=Depends(get_db)):
    return Response.success(data=bookmark_service.listing(db,book_id,page,limit))


@router.post("")
def create_bookmark(payload:BookmarkCreate,book_id:int=Path(ge=1),db:Session=Depends(get_db)):
    return Response.success(data=bookmark_service.create(db,book_id,payload))


@router.patch("/{bookmark_id}")
def update_bookmark(payload:BookmarkPatch,book_id:int=Path(ge=1),bookmark_id:int=Path(ge=1),db:Session=Depends(get_db)):
    return Response.success(data=bookmark_service.update(db,book_id,bookmark_id,payload))


@router.delete("/{bookmark_id}")
def delete_bookmark(book_id:int=Path(ge=1),bookmark_id:int=Path(ge=1),db:Session=Depends(get_db)):
    return Response.success(data=bookmark_service.remove(db,book_id,bookmark_id))
