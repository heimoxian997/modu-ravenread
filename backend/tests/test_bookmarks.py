"""Synthetic bookmarks: canonical offsets, isolation, persistence and duplicate writes."""
from concurrent.futures import ThreadPoolExecutor
import pytest
from app.database import SessionLocal
from app.models.book import Book
from app.models.bookmark import Bookmark
from test_reading_memory import memory_book


def first_offset(client,book,chapter=0):
    return client.get(f"/api/books/{book}/chapters/{chapter}").json()["data"]["paragraphs"][0]["offset"]


def add(client,book,chapter=0,offset=None,**extra):
    if offset is None:offset=first_offset(client,book,chapter)
    return client.post(f"/api/books/{book}/bookmarks",json={"chapter_idx":chapter,"char_offset":offset,**extra})


def test_bookmark_canonicalizes_paragraph_and_preserves_reading_progress(client,memory_book):
    base=f"/api/books/{memory_book}"
    progress=client.get(base+"/progress").json()["data"]
    offset=first_offset(client,memory_book)
    created=add(client,memory_book,offset=offset+4,label="合成线索").json()["data"]
    assert created["created"] and created["item"]["char_offset"]==offset
    assert created["item"]["excerpt"].startswith("甲某把纸伞交给乙某。")
    again=add(client,memory_book,offset=offset+8,label="不能覆盖已有备注").json()["data"]
    assert not again["created"] and again["item"]["id"]==created["item"]["id"]
    assert again["item"]["label"]=="合成线索"
    assert client.get(base+"/bookmarks").json()["data"]["total"]==1
    assert client.get(base+"/progress").json()["data"]==progress


def test_edit_delete_scoping_and_validation(client,memory_book):
    mark=add(client,memory_book).json()["data"]["item"]
    path=f"/api/books/{memory_book}/bookmarks/{mark['id']}"
    with SessionLocal() as db:
        other=Book(title="另一部合成书",filename="other.txt",encoding="UTF-8",char_count=0,
            chapter_count=0,main_chapter_count=0,author="",synopsis="",protagonists=[],front_matter="")
        db.add(other);db.commit();other_id=other.id
    wrong=f"/api/books/{other_id}/bookmarks/{mark['id']}"
    assert client.patch(wrong,json={"label":"错误书籍"}).json()["code"]==4040
    assert client.delete(wrong).json()["code"]==4040
    assert client.patch(path,json={"label":"甲某交伞处"}).json()["data"]["label"]=="甲某交伞处"
    assert client.patch(path,json={"label":"甲"*81}).status_code==422
    assert client.patch(path,json={"label":"备注","char_offset":0}).status_code==422
    assert client.delete(path).json()["data"]["deleted"]
    assert client.get(f"/api/books/{memory_book}/bookmarks").json()["data"]["total"]==0
    assert client.delete(path).json()["code"]==4040


def test_rejects_wrong_positions_and_supports_extras(client,memory_book):
    assert add(client,memory_book,offset=10**9).json()["code"]==4000
    assert add(client,memory_book,chapter=99,offset=0).json()["code"]==4040
    assert add(client,memory_book,chapter=-1,offset=0).status_code==422
    extra=add(client,memory_book,chapter=1).json()["data"]["item"]
    assert extra["chapter_kind"]=="extra"
    assert client.get("/api/books/999999/bookmarks").json()["code"]==4040


def test_paginated_list_and_book_deletion_cascade(client,memory_book):
    ids=[add(client,memory_book,chapter=i).json()["data"]["item"]["id"] for i in range(4)]
    url=f"/api/books/{memory_book}/bookmarks"
    first=client.get(url,params={"page":1,"limit":2}).json()["data"]
    second=client.get(url,params={"page":2,"limit":2}).json()["data"]
    assert first["total"]==4 and first["has_more"] and not second["has_more"]
    assert {m["id"] for m in first["items"]+second["items"]}==set(ids)
    with SessionLocal() as db:
        db.delete(db.get(Book,memory_book));db.commit()
        assert db.query(Bookmark).count()==0


@pytest.mark.disk_db
def test_concurrent_duplicate_creation_persists_once(client,memory_book):
    offset=first_offset(client,memory_book)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:add(client,memory_book,offset=offset).json(),range(2)))
    assert all(r["code"]==200 for r in results)
    assert len({r["data"]["item"]["id"] for r in results})==1
    with SessionLocal() as db:
        assert db.query(Bookmark).count()==1
        assert db.query(Bookmark).one().char_offset==offset
