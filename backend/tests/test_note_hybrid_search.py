"""Synthetic-only semantic retrieval, incremental indexing and spoiler isolation."""
import json
import threading
import time

import pytest
from app.database import SessionLocal
from app.models.reading_note import ReadingNoteRevision as Revision
from app.models.book import Book
from app.models.note_embedding import NoteSearchDocument as Document, NoteEmbedding as Embedding
from app.services import note_embedding_service as index, embedding_service
from test_reading_memory import memory_book, tool, save, position
from test_chapter_graph import chapter_entry, branch_line

_real_start, _real_stop = index.start, index.stop


@pytest.fixture()
def fake_embedding(monkeypatch):
    calls = []
    def encode(texts, **kwargs):
        calls.extend(texts)
        return [embedding_service.EmbeddedDoc(
            dense=[1.0,0.0] if any(word in text for word in ("信任","隔阂","提防")) else [0.0,1.0], sparse={}) for text in texts]
    monkeypatch.setattr(embedding_service,"encode_documents",encode)
    monkeypatch.setattr(index,"available",lambda:True)
    return calls


def trust_entry():
    value = chapter_entry(title="态度转变")
    value["claim"] = "甲某与乙某失去信任"
    return value


def build_all():
    with SessionLocal() as db:
        index.backfill(db)
        while index.index_batch(db):
            pass


def cache_query(text):
    with SessionLocal() as db:
        index._encode(db,index.model_key(),[text])


def test_semantic_paraphrase_without_shared_keywords(client,memory_book,fake_embedding):
    note=save(client,memory_book,trust_entry())
    question="隔阂起点"
    before=tool(client,memory_book,"note_query",{"query":question,"view":"events"})["data"]
    assert before["items"]==[] and before["retrieval"]["semantic"]=="building"
    build_all();cache_query(question)
    found=tool(client,memory_book,"note_query",{"query":question,"view":"events"})["data"]
    assert found["items"][0]["note_id"]==note["note_id"]
    assert found["retrieval"]["semantic"]=="ready"
    previous=len(fake_embedding)
    tool(client,memory_book,"note_query",{"query":question,"view":"events"})
    assert len(fake_embedding)==previous  # Cache the question, don't re-embed documents.


def test_incremental_dedup_and_edge_only_edits_reuse_vectors(client,memory_book,fake_embedding):
    ref=save(client,memory_book,trust_entry());line=branch_line(ref)
    line["nodes"][0]["node_id"]="overview"  # Node IDs cannot collide with summary segments.
    line["edges"]=[]
    saved=save(client,memory_book,line);build_all()
    count=len(fake_embedding)
    saved_again=save(client,memory_book,trust_entry())
    assert saved_again["version"]==1
    line.update(note_id=saved["note_id"],expected_version=1,change_reason="补连线")
    line["edges"]=[{"source":"overview","target":"a","relation":"follows"}]
    save(client,memory_book,line);build_all()
    assert len(fake_embedding)==count
    with SessionLocal() as db:
        assert db.query(Document).filter(Document.segment=="node:overview").count()==2
    correction=trust_entry();correction.update(note_id=ref["note_id"],expected_version=1,
        claim="甲某开始提防乙某",change_reason="修订表述")
    save(client,memory_book,correction);build_all()
    assert len(fake_embedding)==count+1


def test_historical_vectors_future_versions_other_books_and_chapter_filter(client,memory_book,fake_embedding):
    old=save(client,memory_book,chapter_entry(title="旧时交付"))
    position(memory_book,4)
    later=trust_entry();later.update(note_id=old["note_id"],expected_version=1,change_reason="后章才理解")
    save(client,memory_book,later)
    future=chapter_entry(4,"后期密信","丙某揭开密信，甲某得知真相。")
    future["claim"]="甲某因此失去信任"
    save(client,memory_book,future)
    with SessionLocal() as db:
        other=Book(title="另一部合成书",filename="synthetic.txt",encoding="UTF-8",char_count=0,
            chapter_count=0,main_chapter_count=0,author="",synopsis="",protagonists=[],front_matter="")
        db.add(other);db.flush()
        original=db.query(Revision).filter_by(note_id=old["note_id"],version=2).one()
        foreign=Revision(book_id=other.id,note_id="note_foreign_synthetic",version=1,kind="entry",
            knowledge_idx=0,content={**original.content,"title":"另一部书的信任秘密"})
        db.add(foreign);db.commit()
    build_all();cache_query("隔阂起点")
    position(memory_book,1)
    early=tool(client,memory_book,"note_query",{"query":"隔阂起点","view":"events"})["data"]
    assert early["items"]==[]
    assert "另一部书的信任秘密" not in json.dumps(early,ensure_ascii=False)
    position(memory_book,4)
    now=tool(client,memory_book,"note_query",{"query":"隔阂起点","view":"events","chapter_from":4})["data"]
    assert len(now["items"])==1 and now["items"][0]["title"]=="后期密信"
    # A high-similarity document outside the caller's allowlist cannot be ranked.
    with SessionLocal() as db:
        safe_row=db.query(Revision).filter_by(note_id=old["note_id"],version=1).one()
        allowed={(safe_row.id,"event"):index.segments(safe_row)["event"]}
        scores,_,_=index.semantic_scores(db,"隔阂起点",allowed)
        assert scores=={}


def test_invalidated_nodes_cannot_reappear_via_semantic_search(client,memory_book,fake_embedding):
    ref=save(client,memory_book,chapter_entry())
    line=branch_line(ref);line["title"]="往来记录";line["overview"]="信任逐渐瓦解"
    line["nodes"][0]["conclusion"]="甲某失去信任"
    saved=save(client,memory_book,line);build_all();cache_query("隔阂起点")
    assert tool(client,memory_book,"note_query",{"query":"隔阂起点","view":"graph"})["data"]["items"]
    revised=chapter_entry();revised.update(note_id=ref["note_id"],expected_version=1,
        claim="甲某交付物品，动机未明",change_reason="删除过度解释")
    save(client,memory_book,revised);build_all()
    found=tool(client,memory_book,"note_query",{"query":"隔阂起点","view":"graph"})["data"]
    assert not found["items"]
    assert "失去信任" not in json.dumps(found,ensure_ascii=False)
    assert found["storyline_candidates"][0]["note_id"]==saved["note_id"]


def test_people_fallback_derives_participants_from_event_references(client,memory_book):
    ref=save(client,memory_book,chapter_entry())
    line=branch_line(ref);line["entities"]=["甲某"]
    saved=save(client,memory_book,line)
    listing=tool(client,memory_book,"list_storylines",{"entities":["乙某"]})["data"]
    assert listing["items"][0]["note_id"]==saved["note_id"]
    assert "乙某" in listing["items"][0]["entities"]
    found=tool(client,memory_book,"note_query",{"query":"乙某为何改变看法","view":"graph"})["data"]
    assert any(item["note_id"]==saved["note_id"] for item in found["items"])
    assert found["retrieval"]["semantic"]=="unavailable"


def test_model_change_and_retry_after_embedding_failure(client,memory_book,fake_embedding,monkeypatch):
    save(client,memory_book,trust_entry())
    def fail(*args,**kwargs):raise RuntimeError("synthetic failure")
    encoder=embedding_service.encode_documents
    monkeypatch.setattr(embedding_service,"encode_documents",fail)
    with SessionLocal() as db:
        assert index.index_batch(db)==1
        failed=db.query(Embedding).one()
        assert failed.vector is None and failed.retry_at>time.time()
        assert index.index_batch(db)==0  # Respect retry backoff, no tight failure loop.
        failed.retry_at=0;db.commit()
        monkeypatch.setattr(embedding_service,"encode_documents",encoder)
        assert index.index_batch(db)==1
        assert db.query(Embedding).one().vector
    count=len(fake_embedding)
    monkeypatch.setattr(index,"model_key",lambda:"replacement-model")
    build_all()
    assert len(fake_embedding)==count+1
    with SessionLocal() as db:
        assert index.status(db,memory_book)["ready"]==1


def test_cursor_freezes_index_availability_and_ranking(client,memory_book,fake_embedding):
    for i in range(4):
        value=chapter_entry(title=f"交付记录{i}");value["claim"]=f"甲某交付纸伞，第{i}种记录"
        save(client,memory_book,value)
    first=tool(client,memory_book,"note_query",{"query":"甲某","view":"events","limit":1})["data"]
    assert first["has_more"]
    build_all();cache_query("甲某")
    seen=[first["items"][0]["note_id"]];cursor=first["cursor"]
    while cursor:
        page=tool(client,memory_book,"note_query",{"query":"甲某","view":"events","limit":1,"cursor":cursor})["data"]
        seen.extend(item["note_id"] for item in page["items"])
        assert page["retrieval"]["semantic"]!="ready"
        cursor=page["cursor"]
    assert len(seen)==len(set(seen))==4


@pytest.mark.disk_db
def test_background_backfill_query_timeout_and_restart(client,memory_book,fake_embedding,monkeypatch):
    note=save(client,memory_book,trust_entry())
    with SessionLocal() as db:
        db.query(Document).delete();db.commit()  # Simulate notes written before the upgrade.
    indexed=threading.Event()
    encode=embedding_service.encode_documents
    def signal(texts,**kwargs):
        result=encode(texts,**kwargs);indexed.set();return result
    monkeypatch.setattr(embedding_service,"encode_documents",signal)
    _real_start()
    try:
        assert indexed.wait(3)
        deadline=time.monotonic()+3
        while True:
            with SessionLocal() as db:
                if index.status(db,memory_book)["ready"]:
                    break
            assert time.monotonic()<deadline
            threading.Event().wait(0.01)
        # The writer and HTTP caller use independent sessions.
        found=tool(client,memory_book,"note_query",{"query":"甲某为什么不相信乙某","view":"events"})["data"]
        assert found["items"]
        assert found["retrieval"]["semantic"]=="ready"
    finally:
        _real_stop()
    assert not index._thread.is_alive()
    count=len(fake_embedding)
    _real_start()
    try:
        with SessionLocal() as db:
            assert index.status(db,memory_book)["ready"]>=1
        assert len(fake_embedding)==count
    finally:
        _real_stop()


def test_query_wait_is_bounded_and_note_write_does_not_encode(client,memory_book,fake_embedding,monkeypatch):
    save(client,memory_book,trust_entry())
    assert fake_embedding==[]
    build_all()
    monkeypatch.setattr(index,"QUERY_WAIT_SECONDS",0.01)
    before=time.monotonic()
    found=tool(client,memory_book,"note_query",{"query":"甲某有何心事","view":"events"})["data"]
    assert time.monotonic()-before<1
    assert found["items"] and found["retrieval"]["semantic"]=="warming"
    # Discard tickets left by this deliberately stopped worker before another test.
    with index._tickets_lock:
        index._tickets.clear()
    while not index._queries.empty():index._queries.get_nowait()


def test_hybrid_pages_include_diagnostics_in_character_budget(client,memory_book,fake_embedding):
    for i in range(12):
        value=trust_entry();value["title"]=f"第{i}项"+"合成事件"*16
        value["claim"]="甲某与乙某失去信任，"+"记录需要核实，"*20
        save(client,memory_book,value)
    build_all();cache_query("甲某为什么不相信乙某")
    args={"query":"甲某为什么不相信乙某","view":"graph","limit":10}
    seen=[]
    while True:
        page=tool(client,memory_book,"note_query",args)["data"]
        assert len(json.dumps(page,ensure_ascii=False))<=6000
        assert page["items"]
        seen.extend(item["note_id"] for item in page["items"])
        if not page["has_more"]:break
        args["cursor"]=page["cursor"]
    assert len(seen)==len(set(seen))==12
