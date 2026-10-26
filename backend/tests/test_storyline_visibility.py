"""Synthetic chapter-scoped writes and non-spoiling retrospective human views."""
import copy
import json
from app.database import SessionLocal
from app.models.reading_note import ReadingNoteRevision as Revision
from test_reading_memory import memory_book, save, position, tool
from test_chapter_graph import chapter_entry, branch_line


def get_data(client,path):
    response=client.get(path)
    assert response.status_code==200,response.text
    result=response.json()["data"]
    assert result["ok"],result
    return result["data"]


def late_line(client,book):
    position(book,4)
    early=chapter_entry(title="后章才写的身份解释")
    early["claim"]="甲某其实是旧城的城主，交伞是为了掩饰身份"
    first=save(client,book,early)
    later=save(client,book,chapter_entry(3,"归还事件","乙某归还纸伞，甲某道谢。"))
    line=branch_line(first)
    line.update(title="甲某城主身份被揭穿",overview="后章才有的剧情概述",stage="全书终局",open_questions=["谁会在结局离开"])
    line["nodes"]=[{"node_id":"future_identity_name","chapter_number":1,"branch":"后章分支剧透",
        "conclusion":"后章才有的剧情判断","evidence":[{"note_id":first["note_id"],"version":1}]},
        {"node_id":"later","chapter_number":3,"conclusion":"后章独有节点","evidence":[{"note_id":later["note_id"],"version":1}]}]
    line["edges"]=[{"source":"future_identity_name","target":"later","relation":"causes","reason":"后章因果剧透"}]
    return save(client,book,line),first


def test_atomic_entry_rejects_later_or_earlier_chapter_evidence(client,memory_book):
    position(memory_book,3)
    bad=chapter_entry()
    bad["evidence"].append({"chapter_number":3,"quote":"乙某归还纸伞，甲某道谢。"})
    assert tool(client,memory_book,"note_upsert",bad)["error"]=="invalid_arguments"
    response=client.post(f"/api/books/{memory_book}/reading-notes/upsert",json=bad)
    assert response.status_code==422
    earlier=chapter_entry(3,"归还事件","甲某把纸伞交给乙某。")
    earlier["evidence"][0]["chapter_number"]=1
    assert tool(client,memory_book,"note_upsert",earlier)["error"]=="invalid_arguments"
    with SessionLocal() as db:
        assert db.query(Revision).count()==0


def test_existing_bad_scope_is_quarantined_and_cannot_be_newly_referenced(client,memory_book):
    ref=save(client,memory_book,chapter_entry());saved=save(client,memory_book,branch_line(ref))
    position(memory_book,3)
    with SessionLocal() as db:
        entry=db.query(Revision).filter_by(note_id=ref["note_id"]).one()
        data=copy.deepcopy(entry.content)
        data["evidence"].append({"chapter_number":3,"quote":"乙某归还纸伞，甲某道谢。"})
        entry.content=data;db.commit()
    new_line=branch_line(ref);new_line["title"]="引用旧问题记录的新线"
    assert tool(client,memory_book,"note_upsert",new_line)["error"]=="evidence_chapter_mismatch"
    line=get_data(client,f"/api/books/{memory_book}/storylines/{saved['note_id']}")
    assert all(node["needs_revision"] for node in line["nodes"])
    assert not line["edges"]
    note=tool(client,memory_book,"note_query",{"note_id":ref["note_id"],"view":"entry"})["data"]["items"][0]
    assert note["needs_revision"] and note["scope_error"]
    assert not tool(client,memory_book,"note_query",{"entities":["甲某"],"view":"events"})["data"]["items"]


def test_late_created_line_keeps_early_excerpts_but_no_late_metadata(client,memory_book):
    line,_=late_line(client,memory_book)
    position(memory_book,1)
    listing=get_data(client,f"/api/books/{memory_book}/storylines")
    assert len(listing["items"])==1
    assert listing["items"][0]["title"]=="第1章起的线索"
    assert listing["items"][0]["node_count"]==1
    data=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}")
    assert data["limited_view"] and len(data["nodes"])==1
    assert data["nodes"][0]["chapter_number"]==1 and not data["edges"]
    node_id=data["nodes"][0]["node_id"]
    assert node_id.startswith("p_")
    notes=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}/nodes/{node_id}/evidence")
    assert notes["items"][0]["evidence"]==[{"chapter_number":1,"quote":"甲某把纸伞交给乙某。"}]
    raw=json.dumps([listing,data,notes],ensure_ascii=False)
    for hidden in ("城主","后章分支剧透","后章因果剧透","全书终局","future_identity_name","后章独有节点"):
        assert hidden not in raw
    # Hidden titles cannot be discovered through a search-result existence oracle.
    assert get_data(client,f"/api/books/{memory_book}/storylines?query=城主")["items"]==[]
    assert get_data(client,f"/api/books/{memory_book}/storylines?query=纸伞")["items"]
    assert tool(client,memory_book,"list_storylines",{})["data"]["items"]==[]  # MCP remains conservative.


def test_progressively_reveals_nodes_and_only_then_restores_late_title(client,memory_book):
    line,_=late_line(client,memory_book)
    position(memory_book,1)
    early=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}")
    position(memory_book,3)
    middle=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}")
    assert len(middle["nodes"])==2 and middle["limited_view"]
    assert middle["storyline"]["title"]==early["storyline"]["title"]
    assert not middle["edges"]  # Late causal judgments are still hidden.
    position(memory_book,4)
    full=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}")
    assert not full["limited_view"]
    assert full["storyline"]["title"]==line["title"] and len(full["edges"])==1
    position(memory_book,1)
    restored=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}")
    assert restored==early


def test_old_mixed_quote_is_filtered_even_in_node_evidence_endpoint(client,memory_book):
    line,ref=late_line(client,memory_book)
    with SessionLocal() as db:
        entry=db.query(Revision).filter_by(note_id=ref["note_id"]).one()
        data=copy.deepcopy(entry.content)
        data["evidence"].append({"chapter_number":3,"quote":"乙某归还纸伞，甲某道谢。"})
        entry.content=data;db.commit()
    position(memory_book,1)
    graph=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}")
    evidence=get_data(client,f"/api/books/{memory_book}/storylines/{line['note_id']}/nodes/{graph['nodes'][0]['node_id']}/evidence")
    assert all(q["chapter_number"]==1 for item in evidence["items"] for q in item["evidence"])
    assert "归还" not in json.dumps(evidence,ensure_ascii=False)
    response=client.get(f"/api/books/{memory_book}/storylines/{line['note_id']}/nodes/later/evidence").json()["data"]
    assert response["error"]=="not_found"


def test_wholly_future_line_stays_hidden_and_cursor_checks_progress(client,memory_book):
    position(memory_book,4)
    ref=save(client,memory_book,chapter_entry(3,"归还事件","乙某归还纸伞，甲某道谢。"))
    line=branch_line(ref)
    for n in line["nodes"]:n["chapter_number"]=3
    saved=save(client,memory_book,line)
    line2=copy.deepcopy(line);line2["title"]="另一条合成线";save(client,memory_book,line2)
    position(memory_book,3)
    page=get_data(client,f"/api/books/{memory_book}/storylines?limit=1")
    assert page["has_more"]
    position(memory_book,1)
    assert not get_data(client,f"/api/books/{memory_book}/storylines")["items"]
    result=client.get(f"/api/books/{memory_book}/storylines/{saved['note_id']}").json()["data"]
    assert result["error"]=="not_found"
    response=client.get(f"/api/books/{memory_book}/storylines",params={"limit":1,"cursor":page["cursor"]}).json()["data"]
    assert response["error"]=="invalid_cursor"
