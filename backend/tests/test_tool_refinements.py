"""Synthetic-only reproduction of MCP pagination, diagnostics and local revisions."""
import asyncio
import json
from app.database import SessionLocal
from app.schemas.reading_note import NoteQuery
from app.models.reading_note import ReadingNoteRevision as Revision
from test_reading_memory import memory_book, tool, save, position
from test_chapter_graph import chapter_entry, branch_line


def directory_line(client, book):
    ref = save(client,book,chapter_entry())
    line = branch_line(ref)
    line["nodes"] = [{**line["nodes"][0],"node_id":f"synthetic_{i}","conclusion":f"合成进展{i}"} for i in range(14)]
    line["edges"] = []
    return save(client,book,line)


def test_directory_cursor_roundtrip_through_typed_wire(client,memory_book):
    line = directory_line(client,memory_book)
    filters = {"note_id":line["note_id"],"view":"directory","limit":10}
    wire = lambda args:NoteQuery.model_validate(args).model_dump(exclude_none=True)
    response = tool(client,memory_book,"note_query",wire(filters))
    assert response["ok"],response
    first = response["data"]
    assert first["has_more"] and len(first["items"])==10
    second = tool(client,memory_book,"note_query",wire({**filters,"cursor":first["cursor"]}))
    assert second["ok"],second
    assert len(second["data"]["items"])==4 and not second["data"]["has_more"]
    assert len({n["node_id"] for n in first["items"]+second["data"]["items"]})==14


def test_directory_cursor_survives_forward_progress_but_rejects_changed_filters(client,memory_book):
    line = directory_line(client,memory_book)
    filters = {"note_id":line["note_id"],"view":"directory","limit":10}
    first = tool(client,memory_book,"note_query",filters)["data"]
    position(memory_book,3)
    second = tool(client,memory_book,"note_query",{**filters,"cursor":first["cursor"]})
    assert second["ok"],second
    assert second["data"]["safe_chapter"]==1
    wrong = tool(client,memory_book,"note_query",{**filters,"limit":9,"cursor":first["cursor"]})
    assert wrong["error"]=="invalid_cursor"
    assert "limit" in wrong["data"]["changed_fields"]


def test_directory_mcp_cursor_roundtrip(client,memory_book,monkeypatch,mcp_server):
    line = directory_line(client,memory_book)
    monkeypatch.setattr(mcp_server,"_tool",lambda book,name,args:tool(client,book,name,args))
    filters = {"note_id":line["note_id"],"view":"directory","limit":10}
    def invoke(args):
        result = asyncio.run(mcp_server.mcp.call_tool("note_query",{"book_id":memory_book,"filters":args}))
        structured = result[1] if isinstance(result,tuple) else result.structuredContent
        return structured
    first = invoke(filters)
    second = invoke({**filters,"cursor":first["data"]["cursor"]})
    assert second["ok"] and len(second["data"]["items"])==4


def test_directory_rewind_and_mutation_do_not_break_snapshot_safety(client,memory_book):
    line = directory_line(client,memory_book)
    filters = {"note_id":line["note_id"],"view":"directory","limit":10}
    first = tool(client,memory_book,"note_query",filters)["data"]
    patch = {"kind":"storyline_patch","note_id":line["note_id"],"expected_version":1,
        "change_reason":"合成标题修订","title":"修订后的合成线"}
    save(client,memory_book,patch)
    second = tool(client,memory_book,"note_query",{**filters,"cursor":first["cursor"]})["data"]
    assert second["storyline"]["version"]==1 and len(second["items"])==4
    position(memory_book,3)
    later = tool(client,memory_book,"note_query",filters)["data"]
    position(memory_book,1)
    error = tool(client,memory_book,"note_query",{**filters,"cursor":later["cursor"]})
    assert error["error"]=="invalid_cursor" and error["data"]["reason"]=="reading_scope_shrank"


def test_quote_diagnostics_identify_each_failure_without_saving(client,memory_book):
    value = chapter_entry()
    value["evidence"] += [{"chapter_number":1,"quote":"甲某把纸伞交给丙某。"},
        {"chapter_number":1,"quote":"甲某在院中等候。"}]
    result = tool(client,memory_book,"note_upsert",value)
    assert result["error"]=="quote_not_found"
    errors = result["data"]["errors"]
    assert [e["evidence_index"] for e in errors]==[1,2]
    assert errors[0]["quote"]==value["evidence"][1]["quote"]
    assert errors[0]["closest_passage"]["candidate_quote"]=="甲某把纸伞交给乙某。"
    assert errors[0]["closest_passage"]["review_only"]
    assert errors[1]["error"]=="quote_ambiguous"
    with SessionLocal() as db:assert db.query(Revision).count()==0


def test_quote_suggestions_do_not_search_future_chapters(client,memory_book):
    value = chapter_entry(quote="丙某揭开密信，甲某得知真相。")
    result = tool(client,memory_book,"note_upsert",value)
    assert result["error"]=="quote_not_found"
    assert "丙某揭开密信" not in json.dumps(result["data"].get("errors",[])[0]["closest_passage"],ensure_ascii=False)
    value["evidence"][0]["chapter_number"] = 4
    value["chapter_number"] = 4
    assert tool(client,memory_book,"note_upsert",value)["error"]=="chapter_out_of_range"


def test_name_suggestions_are_observed_and_progress_gated(client,memory_book):
    from app.models.character import Character
    with SessionLocal() as db:
        for name,chapter in (("甲某",0),("丙某",3)):
            db.add(Character(book_id=memory_book,canonical_name=name,first_seen_chapter=chapter,last_seen_chapter=chapter,mention_count=1))
        db.commit()
    value = chapter_entry()
    value["payload"]["participants"] = ["甲谋","乙某"]
    result = tool(client,memory_book,"note_upsert",value)
    assert result["error"]=="entity_not_observed"
    assert result["data"]["candidates"][0]["name"]=="甲某"
    assert all(candidate["chapter_number"]==1 for candidate in result["data"]["candidates"])
    assert "丙某" not in json.dumps(result["data"]["candidates"],ensure_ascii=False)


def test_partial_evidence_confirmation_preserves_metadata_and_other_stale_nodes(client,memory_book):
    ref = save(client,memory_book,chapter_entry())
    original = branch_line(ref)
    line = save(client,memory_book,original)
    with SessionLocal() as db:original_edges = db.query(Revision).filter_by(note_id=line["note_id"],version=1).one().content["edges"]
    corrected = chapter_entry(title="修订后的合成记录")
    corrected.update(note_id=ref["note_id"],expected_version=1,change_reason="只修订标题")
    new = save(client,memory_book,corrected)
    review = tool(client,memory_book,"note_query",{"note_id":line["note_id"],"node_id":"start","view":"review"})["data"]
    assert review["review_only"] and review["previous_conclusion"]==original["nodes"][0]["conclusion"]
    assert review["evidence"][0]["current_version"]==2
    confirmation = {"note_id":line["note_id"],"expected_version":1,"change_reason":"核对标题修订，原结论仍成立",
        "nodes":[{"node_id":"start","evidence":[{"note_id":new["note_id"],"version":2}],"conclusion_unchanged":True}]}
    partial = tool(client,memory_book,"refresh_storyline_evidence",confirmation)
    assert partial["ok"] and partial["data"]["stale_node_count"]==3
    confirmation["expected_version"] = 2
    repeated = tool(client,memory_book,"refresh_storyline_evidence",confirmation)
    assert repeated["data"]["version"]==2 and repeated["data"]["stale_node_count"]==3
    confirmation["nodes"] = [{**confirmation["nodes"][0],"node_id":node} for node in ("a","b","merge")]
    complete = tool(client,memory_book,"refresh_storyline_evidence",confirmation)
    assert complete["ok"] and not complete["data"]["needs_revision"]
    assert complete["data"]["overview"]==original["overview"] and complete["data"]["stage"]==original["stage"]
    with SessionLocal() as db:
        current = db.query(Revision).filter_by(note_id=line["note_id"],version=3).one()
        assert current.content["edges"]==original_edges
        assert [node["conclusion"] for node in current.content["nodes"]]==[node["conclusion"] for node in original["nodes"]]


def test_evidence_confirmation_rejects_replacements_conflicts_and_unconfirmed_changes(client,memory_book):
    ref = save(client,memory_book,chapter_entry())
    line = save(client,memory_book,branch_line(ref))
    other = save(client,memory_book,chapter_entry(title="另一条合成记录"))
    payload = {"note_id":line["note_id"],"expected_version":1,"change_reason":"合成版本核对",
        "nodes":[{"node_id":"start","evidence":[{"note_id":other["note_id"],"version":1}],"conclusion_unchanged":True}]}
    assert tool(client,memory_book,"refresh_storyline_evidence",payload)["error"]=="invalid_arguments"
    payload["nodes"][0]["evidence"] = [{"note_id":ref["note_id"],"version":1}]
    payload["nodes"][0]["conclusion_unchanged"] = False
    assert tool(client,memory_book,"refresh_storyline_evidence",payload)["error"]=="invalid_arguments"
    payload["nodes"][0]["conclusion_unchanged"] = True
    correction = chapter_entry(title="新合成版本");correction.update(note_id=ref["note_id"],expected_version=1)
    save(client,memory_book,correction)
    assert tool(client,memory_book,"refresh_storyline_evidence",payload)["error"]=="stale_evidence"
    payload["expected_version"] = 99
    assert tool(client,memory_book,"refresh_storyline_evidence",payload)["error"]=="version_conflict"


def test_duplicate_search_supports_read_range_and_excludes_future(client,memory_book):
    save(client,memory_book,chapter_entry())
    position(memory_book,3)
    save(client,memory_book,chapter_entry(3,"归还事件","乙某归还纸伞，甲某道谢。"))
    across = tool(client,memory_book,"find_note_duplicates",{"entities":["乙某"]})["data"]
    assert {item["chapter_number"] for item in across["items"]}=={1,3}
    limited = tool(client,memory_book,"find_note_duplicates",{"entities":["乙某"],"chapter_from":3,"chapter_to":3})["data"]
    assert [item["chapter_number"] for item in limited["items"]]==[3]
    assert tool(client,memory_book,"find_note_duplicates",{"entities":["乙某"],"chapter_to":4})["error"]=="chapter_out_of_range"
    position(memory_book,1)
    assert [item["chapter_number"] for item in tool(client,memory_book,"find_note_duplicates",{"entities":["乙某"]})["data"]["items"]]==[1]


def test_compact_graph_contains_one_copy_and_paginates(client,memory_book):
    line = directory_line(client,memory_book)
    filters = {"note_id":line["note_id"],"view":"graph","limit":2}
    normal = tool(client,memory_book,"note_query",filters)["data"]
    assert normal["items"]==normal["graph"]["nodes"]
    seen = []
    while True:
        data = tool(client,memory_book,"note_query",{**filters,"compact":True})["data"]
        assert "items" not in data and len(json.dumps(data,ensure_ascii=False))<=6000
        seen += [node["node_id"] for node in data["graph"]["nodes"]]
        if not data["cursor"]:break
        filters["cursor"] = data["cursor"]
    assert len(seen)==len(set(seen))==14


def test_vague_person_question_returns_navigation_and_focused_nodes_do_not_expand_arbitrary_neighbors(client,memory_book):
    ref = save(client,memory_book,chapter_entry())
    save(client,memory_book,branch_line(ref))
    question = {"query":"甲某怎么知道","view":"graph"}
    vague = tool(client,memory_book,"note_query",question)["data"]
    assert not vague["items"] and vague["navigation_candidates"] and vague["matched_count"]==0
    broad = tool(client,memory_book,"note_query",{**question,"relevance":"broad"})["data"]
    assert broad["items"]
    focused = tool(client,memory_book,"note_query",{"query":"甲某交付时间","view":"graph"})["data"]
    assert {node["node_id"] for node in focused["graph"]["nodes"]}=={"a"}


def test_semantic_threshold_is_raw_similarity_not_fusion_score(client,memory_book,monkeypatch):
    ref = save(client,memory_book,chapter_entry())
    from app.services import note_embedding_service
    with SessionLocal() as db:row = db.query(Revision).filter_by(note_id=ref["note_id"]).one();revision_id = row.id
    monkeypatch.setattr(note_embedding_service,"semantic_scores",lambda *a,**kw:({(revision_id,"event"):0.55},
        {"semantic":"ready","query_ready":True,"model_key":"synthetic"},1))
    assert not tool(client,memory_book,"note_query",{"query":"合成隔阂","view":"events"})["data"]["items"]
    relaxed = tool(client,memory_book,"note_query",{"query":"合成隔阂","view":"events","min_similarity":0.5})["data"]
    assert relaxed["items"][0]["semantic_similarity"]==0.55


def test_node_similarity_does_not_inherit_overview_similarity(client,memory_book,monkeypatch):
    ref = save(client,memory_book,chapter_entry())
    line = save(client,memory_book,branch_line(ref))
    from app.services import note_embedding_service
    with SessionLocal() as db:revision_id = db.query(Revision).filter_by(note_id=line["note_id"]).one().id
    scores = {(revision_id,"overview"):0.99,(revision_id,"node:a"):0.7,(revision_id,"node:b"):0.65}
    monkeypatch.setattr(note_embedding_service,"semantic_scores",lambda *a,**kw:(scores,
        {"semantic":"ready","query_ready":True,"model_key":"synthetic"},1))
    data = tool(client,memory_book,"note_query",{"query":"合成航向","view":"graph","compact":True})["data"]
    assert {node["node_id"]:node["semantic_similarity"] for node in data["graph"]["nodes"]}=={"a":0.7,"b":0.65}


def test_mcp_context_and_schema_expose_actual_contracts(client,memory_book,monkeypatch,mcp_server):
    monkeypatch.setattr(mcp_server,"_context",lambda book:{"ok":True,"data":client.get(f"/api/books/{book}/progress").json()["data"]})
    position(memory_book,2)
    context = mcp_server.get_reading_context(memory_book)["data"]
    assert context["is_extra"] and context["chapter_kind"]=="extra"
    assert context["main_text_end_chapter"]==4 and context["safe_main_chapter"]==1
    tools = {item.name:item for item in asyncio.run(mcp_server.mcp.list_tools())}
    limit = tools["search_text"].inputSchema["properties"]["limit"]
    assert any(option.get("maximum")==8 for option in limit["anyOf"])
    schema = json.dumps(tools["note_query"].inputSchema,ensure_ascii=False)
    assert "最多10" in schema and "最多40" in schema and '"default": null' in schema
    assert "refresh_storyline_evidence" in tools


def test_reading_context_metadata_without_optional_sdk(client,memory_book):
    from app.common.model_contract import public
    position(memory_book,2)
    context = public(client.get(f"/api/books/{memory_book}/progress").json()["data"])
    assert context["is_extra"] and context["main_text_end_chapter"]==4 and context["safe_main_chapter"]==1
    position(memory_book,3)
    context = public(client.get(f"/api/books/{memory_book}/progress").json()["data"])
    assert not context["is_extra"] and context["safe_main_chapter"]==3


def test_mcp_evidence_confirmation_validates_nested_acknowledgment(monkeypatch,mcp_server):
    calls = []
    monkeypatch.setattr(mcp_server,"_tool",lambda book,name,args:calls.append((name,args)) or {"ok":True})
    update = {"note_id":"line_synthetic","expected_version":1,"change_reason":"已核对合成证据",
        "nodes":[{"node_id":"start","evidence":[{"note_id":"note_synthetic","version":2}],"conclusion_unchanged":True}]}
    asyncio.run(mcp_server.mcp.call_tool("refresh_storyline_evidence",{"update":update,"book_id":1}))
    assert calls[0][0]=="refresh_storyline_evidence" and calls[0][1]["nodes"][0]["conclusion_unchanged"] is True
    update["nodes"][0]["conclusion_unchanged"] = False
    rejected = asyncio.run(mcp_server.mcp.call_tool("refresh_storyline_evidence",{"update":update,"book_id":1}))
    assert rejected.isError and len(calls)==1
