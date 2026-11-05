"""Synthetic provenance and historical versus retrospective note queries."""
import asyncio
import json
import pytest
from app.database import SessionLocal
from app.schemas.reading_note import NoteQuery
from app.services import reading_note_service
from app.models.reading_note import ReadingNoteRevision as Revision
from test_reading_memory import memory_book, tool, save, position
from test_chapter_graph import chapter_entry, branch_line


def test_entry_query_exposes_basis_and_perspective(client,memory_book):
    entry = chapter_entry()
    entry.update(basis="interpretation",perspective="读者判断",claim="甲某交伞可能出于礼节")
    saved = save(client,memory_book,entry)
    with SessionLocal() as db:
        data = reading_note_service.query(db,memory_book,NoteQuery(note_id=saved["note_id"],view="entry"))["data"]
    assert data["items"][0]["basis"]=="interpretation"
    assert data["items"][0]["perspective"]=="读者判断"
    wire = tool(client,memory_book,"note_query",{"note_id":saved["note_id"],"view":"entry"})
    assert wire["ok"],wire
    for view in ("events","graph"):
        result = tool(client,memory_book,"note_query",{"chapter_from":1,"view":view,"compact":True})["data"]
        item = (result.get("items") or result["graph"]["nodes"])[0]
        assert item["basis"]=="interpretation" and item["perspective"]=="读者判断"


def test_directory_exposes_node_basis(client,memory_book):
    ref = save(client,memory_book,chapter_entry())
    line = save(client,memory_book,branch_line(ref))
    with SessionLocal() as db:
        data = reading_note_service.query(db,memory_book,NoteQuery(note_id=line["note_id"],view="directory"),safe_override=1)["data"]
    assert data["items"][0]["basis"]=="unclassified"
    assert data["items"][0]["evidence_basis"]=="explicit"


def test_node_does_not_promote_interpretive_evidence_to_explicit_fact(client,memory_book):
    value = chapter_entry();value.update(basis="interpretation",perspective="读者判断")
    ref = save(client,memory_book,value)
    line = branch_line(ref)
    for node in line["nodes"]:node["basis"] = "explicit"
    saved = save(client,memory_book,line)
    result = tool(client,memory_book,"note_query",{"note_id":saved["note_id"],"compact":True})["data"]
    assert all(node["basis"]=="interpretation" and node["evidence_perspectives"]==["读者判断"] for node in result["graph"]["nodes"])
    line["nodes"][0].update(basis="interpretation",perspective="读者推断")
    line["title"] = "合成推断线"
    own = save(client,memory_book,line)
    selected = tool(client,memory_book,"note_query",{"note_id":own["note_id"],"node_id":"start","compact":True})["data"]["graph"]["nodes"][0]
    assert selected["perspective"]=="读者推断"


def test_node_mixed_evidence_and_required_perspective(client,memory_book):
    explicit = save(client,memory_book,chapter_entry())
    value = chapter_entry(title="合成解释");value.update(basis="interpretation",perspective="读者判断")
    inferred = save(client,memory_book,value)
    line = branch_line(explicit)
    for node in line["nodes"]:
        node["basis"] = "explicit"
        node["evidence"] = [node["evidence"][0],{"note_id":inferred["note_id"],"version":1}]
    saved = save(client,memory_book,line)
    node = tool(client,memory_book,"note_query",{"note_id":saved["note_id"],"view":"directory"})["data"]["items"][0]
    assert node["basis"]==node["evidence_basis"]=="mixed"
    line["nodes"][0]["basis"] = "interpretation"
    assert tool(client,memory_book,"note_upsert",line)["error"]=="invalid_arguments"


def backfilled(client,book):
    position(book,4)
    value = chapter_entry(title="后期独有标题")
    value.update(claim="后期独有解释，不能倒写为早章已知",basis="interpretation",perspective="后期读者判断")
    return save(client,book,value)


def test_retrospective_separates_event_scope_from_current_knowledge(client,memory_book):
    saved = backfilled(client,memory_book)
    filters = {"note_id":saved["note_id"],"view":"entry","as_of_chapter":1}
    strict = tool(client,memory_book,"note_query",filters)["data"]
    assert strict["time_mode"]=="strict" and not strict["items"]
    retro = tool(client,memory_book,"note_query",{**filters,"time_mode":"retrospective"})["data"]
    assert retro["time_scope"]=={"event_as_of_chapter":1,"knowledge_as_of_chapter":4}
    assert retro["items"][0]["recorded_at_chapter"]==4 and retro["items"][0]["written_after_as_of"]
    assert retro["items"][0]["basis"]=="interpretation"
    position(memory_book,1)
    assert not tool(client,memory_book,"note_query",{**filters,"time_mode":"retrospective"})["data"]["items"]
    with SessionLocal() as db:assert db.query(Revision).filter_by(note_id=saved["note_id"]).count()==1


def test_event_after_target_is_not_returned_even_in_retrospective_mode(client,memory_book):
    position(memory_book,4)
    saved = save(client,memory_book,chapter_entry(3,"归还事件","乙某归还纸伞，甲某道谢。"))
    result = tool(client,memory_book,"note_query",{"note_id":saved["note_id"],"as_of_chapter":1,"time_mode":"retrospective"})
    assert result["ok"] and not result["data"]["items"]


def test_strict_excerpt_hides_late_metadata_and_has_no_writable_reference(client,memory_book):
    saved = backfilled(client,memory_book)
    position(memory_book,1)
    filters = {"view":"excerpts","as_of_chapter":1,"note_id":saved["note_id"]}
    result = tool(client,memory_book,"note_query",filters)["data"]
    assert result["knowledge_boundary_policy"]=="source_only"
    item = result["items"][0]
    assert item["text"]=="甲某把纸伞交给乙某。" and item["recorded_later"]
    assert item["read_only"] and item["basis_scope"]=="quotation"
    assert "note_id" not in item and "version" not in item
    raw = json.dumps(result,ensure_ascii=False)
    assert all(hidden not in raw for hidden in ("后期独有标题","后期独有解释","后期读者判断"))
    for query in ("后期独有标题","后期独有解释"):
        assert not tool(client,memory_book,"note_query",{**filters,"query":query})["data"]["items"]
    ref = {"note_id":item["excerpt_id"],"version":1}
    assert tool(client,memory_book,"note_upsert",branch_line(ref))["error"]=="evidence_unavailable"


def test_excerpt_filters_use_quote_only_and_do_not_include_future_evidence(client,memory_book):
    position(memory_book,4)
    value = chapter_entry()
    value.pop("chapter_number")
    value["payload"]["participants"] = ["丙某"]
    value["evidence"].append({"chapter_number":4,"quote":"丙某揭开密信，甲某得知真相。"})
    saved = save(client,memory_book,value)
    filters = {"view":"excerpts","as_of_chapter":1,"note_id":saved["note_id"]}
    data = tool(client,memory_book,"note_query",filters)["data"]
    assert [item["chapter_number"] for item in data["items"]]==[1]
    assert "密信" not in json.dumps(data,ensure_ascii=False)
    assert not tool(client,memory_book,"note_query",{**filters,"entities":["丙某"]})["data"]["items"]


def test_strict_revision_is_unchanged_while_retrospective_uses_new_version(client,memory_book):
    saved = save(client,memory_book,chapter_entry())
    position(memory_book,4)
    value = chapter_entry(title="后期标题");value.update(note_id=saved["note_id"],expected_version=1,
        claim="后期解释",basis="interpretation",perspective="读者判断")
    save(client,memory_book,value)
    filters = {"note_id":saved["note_id"],"as_of_chapter":1,"view":"entry"}
    old = tool(client,memory_book,"note_query",filters)["data"]["items"][0]
    new = tool(client,memory_book,"note_query",{**filters,"time_mode":"retrospective"})["data"]["items"][0]
    assert old["version"]==1 and old["basis"]=="explicit"
    assert new["version"]==2 and new["basis"]=="interpretation"


def test_excerpt_pagination_freezes_later_insertions_and_deduplicates_quotes(client,memory_book):
    backfilled(client,memory_book)
    save(client,memory_book,chapter_entry(title="另一条同引文记录"))
    save(client,memory_book,chapter_entry(3,"归还事件","乙某归还纸伞，甲某道谢。"))
    filters = {"view":"excerpts","as_of_chapter":3,"limit":1}
    first = tool(client,memory_book,"note_query",filters)["data"]
    assert first["matched_count"]==2 and first["has_more"]
    extra = chapter_entry(title="分页后新摘录");extra["evidence"][0]["quote"] = "甲某把纸伞交给乙某。甲某在院中等候。"
    save(client,memory_book,extra)
    second = tool(client,memory_book,"note_query",{**filters,"cursor":first["cursor"]})["data"]
    assert second["matched_count"]==2 and not second["has_more"]
    assert second["items"][0]["chapter_number"]==3
    assert len(json.dumps(second,ensure_ascii=False))<=6000


def test_retrospective_directory_does_not_reuse_current_knowledge_after_rewind(client,memory_book):
    saved = backfilled(client,memory_book)
    line = save(client,memory_book,branch_line(saved))
    filters = {"note_id":line["note_id"],"view":"directory","as_of_chapter":1,"time_mode":"retrospective","limit":1}
    data = tool(client,memory_book,"note_query",filters)["data"]
    assert data["time_scope"]["knowledge_as_of_chapter"]==4 and data["has_more"]
    position(memory_book,3)
    result = tool(client,memory_book,"note_query",{**filters,"cursor":data["cursor"]})
    assert result["error"]=="invalid_cursor" and result["data"]["reason"]=="reading_scope_shrank"


def test_mcp_provenance_and_time_modes_use_typed_contracts(client,memory_book,monkeypatch,mcp_server):
    saved = backfilled(client,memory_book)
    monkeypatch.setattr(mcp_server,"_tool",lambda book,name,args:tool(client,book,name,args))
    for view,mode in (("entry","retrospective"),("excerpts","strict")):
        result = asyncio.run(mcp_server.mcp.call_tool("note_query",{"book_id":memory_book,"filters":{
            "view":view,"time_mode":mode,"as_of_chapter":1,"note_id":saved["note_id"]}}))
        structured = result[1] if isinstance(result,tuple) else result.structuredContent
        assert structured["ok"] and structured["data"]["items"]
        assert "perspective" in structured["data"]["items"][0]
