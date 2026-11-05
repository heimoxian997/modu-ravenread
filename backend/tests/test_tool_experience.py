"""Synthetic source only: bounded reads, transparent search and versioned navigation."""
import asyncio
from collections import OrderedDict
import json
import pytest
from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.chapter import Chapter
from app.services import text_window_service, agent_capability_service, text_index_service, tool_reference_service, tool_token_service
from test_reading_memory import memory_book, tool, save, position
from test_chapter_graph import chapter_entry, branch_line


@pytest.fixture()
def long_book(memory_book):
    parts = []
    with SessionLocal() as db:
        start = 0
        for row in db.query(Chapter).filter_by(book_id=memory_book).order_by(Chapter.idx):
            body = "\n".join(f"合成段落{i}："+"甲某在院中检查纸伞。"*10 for i in range(80)) if row.idx==0 else "乙某整理合成记录。"*230
            if row.idx==0:body = "甲某把纸伞交给乙某。\n"+body
            raw = row.title+"\n"+body+"\n"
            row.char_start,row.char_end = start,start+len(raw)
            start += len(raw)
            parts.append(raw)
        db.commit()
    (UPLOADS_DIR/str(memory_book)/"source.txt").write_text("".join(parts),encoding="utf-8")
    text_window_service.clear_cache()
    return memory_book


def test_read_progress_paragraph_boundaries_and_lossless_continuation(client,long_book):
    args = {"chapter_number":1,"max_chars":700}
    chunks = []
    consumed = 0
    index = 0
    while True:
        page = tool(client,long_book,"read_chapter",args)["data"]
        assert page["returned_chars"]<=700
        assert page["consumed_chars"]==consumed+page["returned_chars"]
        assert page["remaining_chars"]==page["total_chars"]-page["consumed_chars"]
        segment = page["segments"][0]
        assert segment["segment_index"]==index
        assert segment["start_offset"]==consumed
        assert segment["end_offset"]==page["consumed_chars"]
        if page["has_more"]:assert segment["text"].endswith("\n")
        chunks.append(segment["text"])
        consumed = page["consumed_chars"]
        index += 1
        if not page["has_more"]:break
        args["cursor"] = page["cursor"]
    with SessionLocal() as db:expected = text_window_service.get_chapter_content(db,long_book,0)["text"]
    assert "".join(chunks).rstrip("\n")==expected
    assert tool(client,long_book,"read_chapter",{"chapter_number":1})["data"]["returned_chars"]>1200


def test_batch_budget_and_cursor_options_are_bound(client,long_book):
    position(long_book,4)
    args = {"chapter_from":1,"chapter_to":4,"max_chars":12000}
    data = tool(client,long_book,"read_chapters",args)["data"]
    assert 6000<data["returned_chars"]<=12000 and data["has_more"]
    assert all(segment["chapter_number"]!=2 for segment in data["segments"])
    changed = tool(client,long_book,"read_chapters",{**args,"max_chars":6000,"cursor":data["cursor"]})
    assert changed["error"]=="invalid_cursor"
    assert tool(client,long_book,"read_chapters",{**args,"max_chars":12001})["error"]=="invalid_arguments"
    cursor = data["cursor"]
    position(long_book,1)
    assert tool(client,long_book,"read_chapters",{**args,"cursor":cursor})["error"]=="chapter_out_of_range"


def test_passage_hit_short_ref_and_chapter_continuation(client,long_book):
    hit = tool(client,long_book,"search_text",{"query":"合成段落20","mode":"exact"})["data"]["items"][0]
    assert len(hit["passage_ref"])<=24
    assert hit["snippet"][hit["hit_offset"]:][:hit["hit_length"]]=="合成段落20"
    args = {"passage_ref":hit["passage_ref"],"radius":100,"max_chars":1000}
    data = tool(client,long_book,"read_passage",args)["data"]
    segment = data["segments"][0]
    assert segment["text"][segment["hit_offset"]:][:segment["hit_length"]]=="合成段落20"
    assert not data["has_more"] and data["chapter_cursor"]
    tail = tool(client,long_book,"read_chapter",{"chapter_number":1,"cursor":data["chapter_cursor"]})["data"]
    assert tail["segments"][0]["start_offset"]==segment["end_offset"]
    args.update(to_chapter_end=True,max_chars=6000)
    page = tool(client,long_book,"read_passage",args)["data"]
    text = "".join(s["text"] for s in page["segments"])
    while page["cursor"]:
        page = tool(client,long_book,"read_passage",{**args,"cursor":page["cursor"]})["data"]
        text += "".join(s["text"] for s in page["segments"])
    with SessionLocal() as db:body = text_window_service.get_chapter_content(db,long_book,0)["text"]
    assert body.endswith(text.rstrip("\n"))


def test_short_ref_expiry_cross_book_and_source_change(client,memory_book,monkeypatch):
    monkeypatch.setattr(tool_reference_service,"_references",OrderedDict())
    hit = tool(client,memory_book,"search_text",{"query":"纸伞","mode":"exact"})["data"]["items"][0]
    ref = hit["passage_ref"]
    expiry,payload = tool_reference_service._references[ref]
    tool_reference_service._references[ref] = (expiry,{**payload,"book":memory_book+1})
    assert tool(client,memory_book,"read_passage",{"passage_ref":ref})["error"]=="invalid_cursor"
    legacy_ref = tool_token_service.encode(payload)
    assert tool(client,memory_book,"read_passage",{"passage_ref":legacy_ref})["ok"]
    tool_reference_service._references[ref] = (expiry,payload)
    path = UPLOADS_DIR/str(memory_book)/"source.txt"
    path.write_text(path.read_text(encoding="utf-8")+"\n",encoding="utf-8")
    assert tool(client,memory_book,"read_passage",{"passage_ref":ref})["error"]=="invalid_cursor"
    tool_reference_service._references[ref] = (0,payload)
    failed = tool(client,memory_book,"read_passage",{"passage_ref":ref})
    assert failed["error"]=="invalid_cursor" and "重新搜索" in failed["data"]["message"]


def test_search_reports_fallback_and_validates_mode(client,memory_book):
    result = tool(client,memory_book,"search_text",{"query":"不存在的合成词","mode":"hybrid"})["data"]
    assert result["mode"]=="exact" and not result["items"]
    assert result["retrieval"]["requested_mode"]=="hybrid"
    assert result["retrieval"]["fallback_reason"]=="text_index_unavailable"
    assert "语义" in result["next_action"]
    assert tool(client,memory_book,"search_text",{"query":"纸伞","mode":"wrong"})["error"]=="invalid_arguments"
    assert tool(client,memory_book,"search_text",{"query":"纸伞","mode":"exact"})["data"]["items"][0]["matched_by"]=="exact"


def test_auto_exact_then_semantic_and_hybrid_failure(client,memory_book,monkeypatch):
    caps = agent_capability_service.AgentCapabilities(chapters=True,text_index=True)
    monkeypatch.setattr(agent_capability_service,"detect",lambda *a:caps)
    calls = []
    with SessionLocal() as db:body = text_window_service.get_chapter_content(db,memory_book,0)
    start = body["char_start"]
    def search(*a,**kwargs):
        calls.append(kwargs)
        return [{"chapter_idx":0,"char_start":start,"char_end":start+10,"text":body["text"][:10],
            "parent_char_start":start,"parent_char_end":start+100,"parent_text":body["text"][:100]}]
    monkeypatch.setattr(text_index_service,"search_text_index",search)
    exact = tool(client,memory_book,"search_text",{"query":"纸伞","mode":"auto"})["data"]
    assert exact["mode"]=="exact" and not calls
    semantic = tool(client,memory_book,"search_text",{"query":"借物经过","mode":"auto"})["data"]
    assert semantic["retrieval"]["steps"]==["exact","hybrid"]
    assert semantic["items"][0]["matched_by"]=="semantic" and len(calls)==1
    assert tool(client,memory_book,"read_passage",{"passage_ref":semantic["items"][0]["passage_ref"]})["ok"]
    def fail(*a,**kw):raise RuntimeError("synthetic unavailable")
    monkeypatch.setattr(text_index_service,"search_text_index",fail)
    failed = tool(client,memory_book,"search_text",{"query":"纸伞","mode":"hybrid"})["data"]
    assert failed["mode"]=="exact" and failed["retrieval"]["fallback_reason"]


def test_auto_alias_expansion_is_progress_gated(client,memory_book,monkeypatch):
    from app.models.character import Character
    from app.models.character_alias_observation import CharacterAliasObservation
    with SessionLocal() as db:
        actor = Character(book_id=memory_book,canonical_name="甲某",first_seen_chapter=0,last_seen_chapter=0,mention_count=1)
        db.add(actor);db.flush()
        db.add(CharacterAliasObservation(book_id=memory_book,character_id=actor.id,alias="持伞客",chapter_idx=2,count=1,source="mention"))
        db.commit()
    assert tool(client,memory_book,"search_text",{"query":"持伞客","mode":"auto"})["data"]["items"]==[]
    position(memory_book,3)
    later = tool(client,memory_book,"search_text",{"query":"持伞客","mode":"auto"})["data"]
    assert later["items"][0]["matched_by"]=="alias"
    assert later["retrieval"]["expanded_query"]=="甲某"
    from app.services import correction_service
    monkeypatch.setattr(correction_service,"blocked_layers",lambda *a:{"character"})
    dirty = tool(client,memory_book,"search_text",{"query":"持伞客","mode":"auto"})["data"]
    assert not dirty["items"] and dirty["retrieval"]["alias_status"]=="index_updating"


def test_directory_and_versioned_backlinks_hide_deleted_lines(client,memory_book):
    ref = save(client,memory_book,chapter_entry())
    line = branch_line(ref)
    line["nodes"] = [{**line["nodes"][0],"node_id":f"node{i}","conclusion":f"合成进展{i}"} for i in range(14)]
    line["edges"] = []
    saved = save(client,memory_book,line)
    directory = tool(client,memory_book,"note_query",{"note_id":saved["note_id"],"view":"directory"})["data"]
    assert len(directory["items"])==14 and not directory["has_more"]
    assert directory["matched_counts"]["nodes"]==14
    args = {"note_id":ref["note_id"],"view":"references","limit":10}
    links = tool(client,memory_book,"note_query",args)["data"]
    assert links["matched_count"]==14 and links["has_more"]
    position(memory_book,4)
    updated = chapter_entry();updated.update(note_id=ref["note_id"],expected_version=1,change_reason="合成修订",claim="交付纸伞，其他动机不明")
    save(client,memory_book,updated)
    old_refs = tool(client,memory_book,"note_query",{**args,"version":1})["data"]
    assert all(i["needs_revision"] for i in old_refs["items"])
    assert not tool(client,memory_book,"note_query",{**args,"version":2})["data"]["items"]
    deleted = tool(client,memory_book,"delete_storyline",{"note_id":saved["note_id"],"expected_version":1})
    assert deleted["ok"]
    assert not tool(client,memory_book,"note_query",args)["data"]["items"]


def test_mcp_facing_delete_restore_conflicts_and_listing_cursor(client,memory_book):
    ref = save(client,memory_book,chapter_entry())
    lines = []
    for title in ("合成甲线","合成乙线"):
        line = branch_line(ref);line["title"] = title
        saved = save(client,memory_book,line)
        action = {"note_id":saved["note_id"],"expected_version":1}
        assert tool(client,memory_book,"delete_storyline",{**action,"expected_version":99})["error"]=="version_conflict"
        deleted = tool(client,memory_book,"delete_storyline",action)["data"]
        lines.append(deleted)
    page = tool(client,memory_book,"list_deleted_storylines",{"limit":1})["data"]
    assert page["has_more"] and page["items"][0]["version"]==2
    restored = tool(client,memory_book,"restore_storyline",{"note_id":lines[0]["note_id"],"expected_version":2})
    assert restored["ok"] and restored["data"]["version"]==3
    assert tool(client,memory_book,"list_deleted_storylines",{"limit":1,"cursor":page["cursor"]})["error"]=="invalid_cursor"
    assert tool(client,memory_book,"note_query",{"note_id":ref["note_id"],"view":"entry"})["data"]["items"]


def test_duplicate_candidates_any_entities_motive_and_structured_errors(client,memory_book):
    first = save(client,memory_book,chapter_entry())
    query = {"chapter_number":1,"entities":["甲某"],"query":"交付纸伞","quote":"甲某把纸伞交给乙某。"}
    candidates = tool(client,memory_book,"find_note_duplicates",query)["data"]
    assert candidates["items"][0]["note_id"]==first["note_id"]
    assert "same_evidence" in candidates["items"][0]["candidate_reasons"]
    position(memory_book,4)
    save(client,memory_book,chapter_entry(4,"拆信事件","丙某揭开密信，甲某得知真相。"))
    entities = {"entities":["乙某","丙某"],"view":"events"}
    assert not tool(client,memory_book,"note_query",entities)["data"]["items"]
    assert tool(client,memory_book,"note_query",{**entities,"entity_match":"any"})["data"]["items"]
    motive = chapter_entry(title="动机记录")
    motive.update(payload={"type":"fact","subject":"甲某","predicate":"动机","value":"甲某交伞的动机尚不明确"},basis="interpretation",perspective="读者判断")
    assert tool(client,memory_book,"note_upsert",motive)["ok"]
    bad = tool(client,memory_book,"note_upsert",{**chapter_entry(),"node_id":"wrong"})
    assert bad["error"]=="invalid_arguments"
    error = bad["data"]["errors"][0]
    assert error["field"].endswith("node_id") and "entry" in error["hint"]


def test_weak_matches_are_navigation_and_knowledge_scope_stays_conservative(client,memory_book):
    save(client,memory_book,chapter_entry())
    data = tool(client,memory_book,"note_query",{"query":"甲某为何离开合成小镇","view":"events"})["data"]
    assert not data["items"] and data["navigation_candidates"][0]["reason"]=="entity_only"
    position(memory_book,4)
    later = save(client,memory_book,chapter_entry(title="回顾交伞"))
    assert later["known_by_chapter"]==later["recorded_at_chapter"]==4
    assert later["chapter_number"]==1
    position(memory_book,1)
    assert not tool(client,memory_book,"note_query",{"note_id":later["note_id"]})["data"]["items"]


def test_search_ignores_saved_questions_and_single_incidental_bigram(client,memory_book):
    value = chapter_entry(title="院中记录")
    value.update(question="为何寻找旧地图",claim="甲某整理院中杂物")
    value["payload"]["action"] = "整理杂物"
    save(client,memory_book,value)
    for query in ("寻找旧地图","整理地图"):
        assert not tool(client,memory_book,"note_query",{"query":query,"view":"events"})["data"]["items"]
    data = tool(client,memory_book,"note_query",{"query":"整理杂物","view":"events"})["data"]
    assert "整理杂物" in data["items"][0]["matched_terms"]


def test_mcp_schema_and_dispatch_for_new_operations(monkeypatch,mcp_server):
    calls = []
    monkeypatch.setattr(mcp_server,"_tool",lambda book,name,args:calls.append((name,args)) or {"ok":True})
    specs = {spec.name:spec for spec in asyncio.run(mcp_server.mcp.list_tools())}
    assert {"find_note_duplicates","delete_storyline","restore_storyline","list_deleted_storylines"}<=set(specs)
    assert "radius" in specs["read_passage"].inputSchema["properties"]
    assert "max_chars" in specs["read_chapters"].inputSchema["properties"]
    assert "directory" in json.dumps(specs["note_query"].inputSchema)
    asyncio.run(mcp_server.mcp.call_tool("find_note_duplicates",{"filters":{"chapter_number":1,"entities":["甲某"]},"book_id":1}))
    assert calls[0][0]=="find_note_duplicates" and calls[0][1]["chapter_number"]==1
    invalid = asyncio.run(mcp_server.mcp.call_tool("note_upsert",{"note":{**chapter_entry(),"node_id":"wrong"},"book_id":1}))
    assert invalid.isError
    assert "entry" in invalid.structuredContent["data"]["errors"][0]["hint"]
    assert len(calls)==1
