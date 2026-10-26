"""Bookmarks are personal navigation data, separate from agent evidence notes."""
from sqlalchemy.exc import IntegrityError
from app.common.exceptions import BusinessException
from app.models.bookmark import Bookmark
from app.models.chapter import Chapter
from app.services import reader_progress_service, text_window_service


def _item(row, chapter):
    return {"id":row.id,"book_id":row.book_id,"chapter_idx":row.chapter_idx,"char_offset":row.char_offset,
        "chapter_title":chapter.title,"chapter_kind":chapter.kind,"excerpt":row.excerpt,"label":row.label,
        "create_time":row.create_time.isoformat()}


def _row(db, book_id, bookmark_id):
    reader_progress_service._book_or_404(db,book_id)
    row=db.query(Bookmark).filter_by(book_id=book_id,id=bookmark_id).first()
    if row is None:raise BusinessException(message="书签不存在或已被删除",code=4040)
    chapter=reader_progress_service._chapter_or_404(db,book_id,row.chapter_idx)
    return row,chapter


def listing(db, book_id, page=1, limit=20):
    reader_progress_service._book_or_404(db,book_id)
    query=db.query(Bookmark).filter(Bookmark.book_id==book_id)
    total=query.count()
    rows=query.order_by(Bookmark.create_time.desc(),Bookmark.id.desc()).offset((page-1)*limit).limit(limit).all()
    chapters={c.idx:c for c in db.query(Chapter).filter(Chapter.book_id==book_id,Chapter.idx.in_([r.chapter_idx for r in rows]))}
    return {"items":[_item(row,chapters[row.chapter_idx]) for row in rows if row.chapter_idx in chapters],
        "total":total,"page":page,"limit":limit,"has_more":page*limit<total}


def create(db,book_id,payload):
    reader_progress_service._book_or_404(db,book_id)
    chapter=reader_progress_service._chapter_or_404(db,book_id,payload.chapter_idx)
    if not chapter.char_start<=payload.char_offset<chapter.char_end:
        raise BusinessException(message="书签位置不在指定章节内",code=4000)
    content=text_window_service.get_chapter_content(db,book_id,payload.chapter_idx)
    paragraphs=content["paragraphs"]
    if not paragraphs:raise BusinessException(message="本章没有可标记的正文",code=4000)
    paragraph=next((p for p in reversed(paragraphs) if p["offset"]<=payload.char_offset),paragraphs[0])
    position={"book_id":book_id,"chapter_idx":payload.chapter_idx,"char_offset":paragraph["offset"]}
    previous=db.query(Bookmark).filter_by(**position).first()
    if previous:return {"item":_item(previous,chapter),"created":False}
    row=Bookmark(**position,excerpt=paragraph["text"].strip()[:120],label=payload.label)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        previous=db.query(Bookmark).filter_by(**position).first()
        if previous:return {"item":_item(previous,chapter),"created":False}
        raise
    db.refresh(row)
    return {"item":_item(row,chapter),"created":True}


def update(db,book_id,bookmark_id,payload):
    row,chapter=_row(db,book_id,bookmark_id)
    row.label=payload.label
    db.commit();db.refresh(row)
    return _item(row,chapter)


def remove(db,book_id,bookmark_id):
    row,_=_row(db,book_id,bookmark_id)
    db.delete(row);db.commit()
    return {"deleted":True,"id":bookmark_id}
