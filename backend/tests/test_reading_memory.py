"""Synthetic evidence only: chapter gates, historical revisions and model contracts."""
import json
import asyncio
import pytest
from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.reading_note import ReadingNoteRevision
from app.services import reader_progress_service


@pytest.fixture()
def memory_book(client):
    with SessionLocal() as db:
        book=Book(title="合成书",filename="sample.txt",encoding="UTF-8",char_count=0,chapter_count=4,main_chapter_count=3,
            author="",synopsis="",protagonists=[],front_matter="")
        db.add(book);db.flush()
        parts=[];start=0
        bodies=[("main","甲某把纸伞交给乙某。"+"甲某在院中等候。"*180),
                ("extra","番外独有线索不会进入伴读。"),
                ("main","乙某归还纸伞，甲某道谢。"),
                ("main","丙某揭开密信，甲某得知真相。")]
        for idx,(kind,body) in enumerate(bodies):
            raw=f"第{idx+1}章 合成\n{body}\n"
            db.add(Chapter(book_id=book.id,idx=idx,title=f"第{idx+1}章 合成",kind=kind,char_start=start,char_end=start+len(raw),word_count=len(body)))
            start+=len(raw);parts.append(raw)
        db.commit()
        path=UPLOADS_DIR/str(book.id);path.mkdir(parents=True)
        (path/"source.txt").write_text("".join(parts),encoding="utf-8")
        reader_progress_service.put(db,book.id,0,0)
        return book.id


def position(book, chapter):
    with SessionLocal() as db:
        reader_progress_service.put(db,book,chapter-1,0)


def tool(client,book,name,args):
    response=client.post(f"/api/books/{book}/agent/model-tool",json={"tool":name,"args":args})
    assert response.status_code==200,response.text
    return response.json()["data"]


def entry(title="交伞",chapter=1,quote="甲某把纸伞交给乙某。"):
    return {"kind":"entry","title":title,"claim":"甲某把纸伞交给乙某", "basis":"explicit",
        "payload":{"type":"event","action":"交付纸伞","participants":["甲某","乙某"]},
        "evidence":[{"chapter_number":chapter,"quote":quote}]}


def save(client,book,value):
    result=tool(client,book,"note_upsert",value)
    assert result["ok"],result
    return result["data"]


def storyline(ref,title="纸伞线"):
    return {"kind":"storyline","title":title,"category":"relationship","entities":["甲某","乙某"],
        "overview":"交伞后的关系变化","stage":"交付纸伞","nodes":[{"chapter_number":1,
        "conclusion":"甲某交伞","evidence":[{"note_id":ref["note_id"],"version":ref["version"]}]}]}


def test_chapter_number_and_cursor_roundtrip(client,memory_book):
    book=memory_book
    assert not tool(client,book,"read_chapter",{"chapter_number":0})["ok"]
    assert not tool(client,book,"read_chapter",{"chapter_idx":0})["ok"]
    first=tool(client,book,"read_chapter",{"chapter_number":1})["data"]
    assert first["has_more"]
    parts=first["segments"][:]
    cursor=first["cursor"]
    while cursor:
        data=tool(client,book,"read_chapter",{"chapter_number":1,"cursor":cursor})["data"]
        parts+=data["segments"];cursor=data["cursor"]
    with SessionLocal() as db:
        from app.services.text_window_service import get_chapter_content
        expected=get_chapter_content(db,book,0)["text"]
    assert "".join(p["text"] for p in parts).rstrip("\n")==expected
    assert all(p["chapter_number"]==1 for p in parts)


def test_extra_future_search_and_passage_gate(client,memory_book):
    book=memory_book
    hit=tool(client,book,"search_text",{"query":"纸伞","mode":"exact"})["data"]["items"][0]
    assert hit["chapter_number"]==1
    assert "char_start" not in hit
    assert tool(client,book,"read_passage",{"passage_ref":hit["passage_ref"]})["ok"]
    position(book,4)
    toc=tool(client,book,"get_toc",{})["data"]
    assert [r["chapter_number"] for r in toc["items"]]==[1,3,4]
    found=tool(client,book,"search_text",{"query":"番外独有","mode":"exact"})
    assert found["data"]["items"]==[]
    assert not tool(client,book,"read_chapter",{"chapter_number":2})["ok"]
    data=tool(client,book,"read_chapters",{"chapter_from":1,"chapter_to":4})["data"]
    assert "番外独有" not in json.dumps(data,ensure_ascii=False)
    later=tool(client,book,"search_text",{"query":"密信","mode":"exact"})["data"]["items"][0]
    position(book,1)
    assert not tool(client,book,"read_passage",{"passage_ref":later["passage_ref"]})["ok"]
    assert tool(client,book,"search_text",{"query":"密信","mode":"exact"})["data"]["items"]==[]


def test_note_requires_target_and_valid_evidence(client,memory_book):
    book=memory_book
    assert not tool(client,book,"note_query",{})["ok"]
    assert not tool(client,book,"note_upsert",entry(chapter=3,quote="乙某归还纸伞"))["ok"]
    assert not tool(client,book,"note_upsert",entry(quote="不存在的引文"))["ok"]
    assert tool(client,book,"note_upsert",entry(quote="甲某在院中等候。"))["error"]=="quote_ambiguous"
    position(book,4)
    assert not tool(client,book,"note_upsert",entry(chapter=2,quote="番外独有线索"))["ok"]
    invalid=entry();invalid["basis"]="interpretation"
    assert not tool(client,book,"note_upsert",invalid)["ok"]


def test_revisions_historical_titles_conflicts_and_fixed_evidence(client,memory_book):
    book=memory_book
    early=save(client,book,entry())
    line=save(client,book,storyline(early))
    position(book,4)
    update=storyline(early,"密信揭晓后的纸伞线")
    update.update(note_id=line["note_id"],expected_version=1)
    revised=save(client,book,update)
    assert revised["version"]==2
    assert tool(client,book,"note_upsert",update)["error"]=="version_conflict"
    late=save(client,book,entry("后期解释早期交伞"))
    assert late["known_by_chapter"]==4
    position(book,1)
    listing=tool(client,book,"list_storylines",{})["data"]
    assert listing["items"][0]["title"]=="纸伞线"
    assert listing["items"][0]["version"]==1
    assert "密信" not in json.dumps(listing,ensure_ascii=False)
    assert not tool(client,book,"note_query",{"note_id":late["note_id"]})["data"]["items"]
    detail=tool(client,book,"note_query",{"note_id":line["note_id"]})["data"]["items"]
    assert detail[0]["evidence"][0]["version"]==1
    assert tool(client,book,"note_upsert",update)["error"]=="not_found"


def test_query_targeting_budget_pagination_and_snapshot(client,memory_book):
    book=memory_book
    for i in range(8):save(client,book,entry(f"纸伞记录{i}"))
    first=tool(client,book,"note_query",{"entities":["甲某"]})["data"]
    assert len(first["items"])<=5
    assert len(json.dumps(first["items"],ensure_ascii=False))<6100
    assert first["has_more"]
    save(client,book,entry("分页后新记录"))
    second=tool(client,book,"note_query",{"entities":["甲某"],"cursor":first["cursor"]})["data"]
    assert len(first["items"])+len(second["items"])==8
    assert not tool(client,book,"note_query",{"entities":["乙某"],"cursor":first["cursor"]})["ok"]
    position(book,3)
    assert not tool(client,book,"note_query",{"entities":["甲某"],"cursor":first["cursor"]})["ok"]


def test_cross_book_reference_cannot_be_used(client,memory_book):
    book=memory_book
    ref=save(client,book,entry())
    with SessionLocal() as db:
        other=Book(title="另一合成书",filename="other.txt",encoding="UTF-8",char_count=0,chapter_count=1,main_chapter_count=1,
            author="",synopsis="",protagonists=[],front_matter="")
        db.add(other);db.flush()
        db.add(Chapter(book_id=other.id,idx=0,title="合成章",kind="main",char_start=0,char_end=10,word_count=10))
        db.commit();other_id=other.id
        path=UPLOADS_DIR/str(other_id);path.mkdir();(path/"source.txt").write_text("甲某与乙某走进院门。",encoding="utf-8")
        reader_progress_service.put(db,other_id,0,0)
    assert tool(client,other_id,"note_upsert",storyline(ref))["error"]=="evidence_unavailable"


def test_mcp_contracts_expose_typed_notes_and_no_offsets(mcp_server):
    tools={t.name:t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"list_storylines","note_query","note_upsert","read_chapters","read_passage"}<=set(tools)
    assert "read_span" not in tools
    for name in ("read_chapter","read_chapters","get_chapter_summary","get_character_timeline"):
        schema=json.dumps(tools[name].inputSchema)
        assert "chapter_idx" not in schema and '"offset"' not in schema
    assert "expected_version" in json.dumps(tools["note_upsert"].inputSchema)
    assert "chapter_number" in json.dumps(tools["note_upsert"].inputSchema)


def test_fixed_revision_lookup_does_not_follow_later_correction(client,memory_book):
    old=save(client,memory_book,entry())
    revised=entry("修正交伞描述")
    revised.update(note_id=old["note_id"],expected_version=1)
    new=save(client,memory_book,revised)
    assert new["version"]==2
    exact=tool(client,memory_book,"note_query",{"note_id":old["note_id"],"version":1})["data"]["items"]
    assert exact[0]["title"]=="交伞"
    assert exact[0]["version"]==1


def test_names_and_counts_do_not_reveal_future_revisions(client,memory_book):
    ref=save(client,memory_book,entry())
    position(memory_book,4)
    line=save(client,memory_book,storyline(ref,"密信与纸伞"))
    position(memory_book,1)
    result=tool(client,memory_book,"list_storylines",{})["data"]
    assert result["matched_count"]==0 and not result["has_more"]
    assert result["cursor"] is None
    assert tool(client,memory_book,"note_query",{"note_id":line["note_id"],"version":1})["data"]["items"]==[]


def test_note_query_aliases_are_gated_and_ambiguous_names_not_merged(client,memory_book):
    from app.models.character import Character
    from app.models.character_alias_observation import CharacterAliasObservation
    with SessionLocal() as db:
        actor=Character(book_id=memory_book,canonical_name="甲某",first_seen_chapter=0,last_seen_chapter=0,mention_count=1)
        db.add(actor);db.flush()
        db.add(CharacterAliasObservation(book_id=memory_book,character_id=actor.id,alias="旧伞客",chapter_idx=2,count=1,source="mention"))
        db.commit()
    save(client,memory_book,entry())
    early=tool(client,memory_book,"note_query",{"entities":["旧伞客"]})["data"]
    assert early["items"]==[]
    position(memory_book,3)
    later=tool(client,memory_book,"note_query",{"entities":["旧伞客"]})["data"]
    assert len(later["items"])==1


def test_reviewed_import_is_explicit_idempotent_and_keeps_original(client,memory_book):
    import hashlib
    from app.models.note import Note
    with SessionLocal() as db:
        old=Note(note_id="legacy_synthetic",book_id=memory_book,type="event",subject_key="交伞事件",
            chapter_idx=0,char_start=10,char_end=20,quote="甲某把纸伞交给乙某。",
            payload_json={"action":"交伞","participants":["甲某","乙某"]},confidence=0.6,status="tentative",spoiler=0,created_by="test")
        db.add(old);db.commit()
        raw=json.dumps({"subject_key":old.subject_key,"quote":old.quote,"payload":old.payload_json},ensure_ascii=False,sort_keys=True)
        body={"legacy_id":old.id,"source_hash":hashlib.sha256(raw.encode()).hexdigest(),"note":entry()}
    url=f"/api/books/{memory_book}/reading-notes/import-reviewed"
    first=client.post(url,json=body).json()["data"]
    assert first["ok"]
    again=client.post(url,json=body).json()["data"]
    assert again["already_imported"]
    assert first["data"]["note_id"]==again["data"]["note_id"]
    body["source_hash"]="0"*64
    assert client.post(url,json=body).json()["data"]["error"]=="source_changed"
    with SessionLocal() as db:
        assert db.query(Note).filter(Note.note_id=="legacy_synthetic").one().subject_key=="交伞事件"


def test_model_output_converts_all_chapter_metadata():
    from app.services.model_tool_service import public
    data=public({"chapter_idx":124,"gate_chapter":0,"as_of_chapter":124,
        "items":[{"first_seen_chapter":0,"last_seen_chapter":124,"chapters":[0,124],"char_start":20}],
        "override_from_chapter":4,"char_offset":99})
    assert data=={"chapter_number":125,"gate_chapter":1,"as_of_chapter":125,
        "items":[{"first_seen_chapter":1,"last_seen_chapter":125,"chapters":[1,125]}],"override_from_chapter":5}
