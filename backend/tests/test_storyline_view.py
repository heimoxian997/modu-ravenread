"""Read-only human graph projection, including history and invalidated evidence."""
import json
from app.database import SessionLocal
from app.models.reader_progress import ReaderProgress
from test_reading_memory import memory_book, save, position
from test_chapter_graph import chapter_entry, branch_line


def detail(client, book, line):
    response=client.get(f"/api/books/{book}/storylines/{line}")
    assert response.status_code==200
    return response.json()["data"]


def test_complete_graph_contains_all_branches_without_model_page_budget(client,memory_book,monkeypatch):
    ref=save(client,memory_book,chapter_entry())
    line=branch_line(ref)
    line["nodes"]=[{**line["nodes"][0],"node_id":f"node{i}","conclusion":f"甲某核对合成线索{i}，"+"补充说明"*20} for i in range(30)]
    line["edges"]=[{"source":"node0","target":f"node{i}","relation":"follows"} for i in range(1,30)]
    saved=save(client,memory_book,line)
    def forbidden(*args,**kwargs):raise AssertionError("graph viewing must not probe vector stores")
    monkeypatch.setattr("app.services.agent_capability_service.detect",forbidden)
    result=detail(client,memory_book,saved["note_id"])
    assert result["ok"]
    assert len(result["data"]["nodes"])==30 and len(result["data"]["edges"])==29
    assert result["data"]["safe_chapter"]==1
    assert len(json.dumps(result["data"],ensure_ascii=False))>6000


def test_rewind_selects_old_graph_and_hides_future_titles(client,memory_book):
    early=save(client,memory_book,chapter_entry())
    line=branch_line(early);saved=save(client,memory_book,line)
    position(memory_book,3)
    later=save(client,memory_book,chapter_entry(3,"归还事件","乙某归还纸伞，甲某道谢。"))
    line.update(note_id=saved["note_id"],expected_version=1,title="后章揭示的剧情线名",change_reason="补充后章")
    line["nodes"].append({"node_id":"later","chapter_number":3,"conclusion":"后章独有结论",
        "evidence":[{"note_id":later["note_id"],"version":1}]})
    save(client,memory_book,line)
    assert detail(client,memory_book,saved["note_id"])["data"]["storyline"]["version"]==2
    position(memory_book,1)
    result=detail(client,memory_book,saved["note_id"])["data"]
    assert result["storyline"]["version"]==1 and len(result["nodes"])==4
    assert "后章" not in json.dumps(result,ensure_ascii=False)
    assert detail(client,memory_book,later["note_id"])["error"]=="not_found"


def test_invalidated_conclusions_and_edge_reasons_are_hidden(client,memory_book):
    ref=save(client,memory_book,chapter_entry());line=branch_line(ref)
    line["edges"][0].update(relation="causes",reason="待失效的合成解释")
    saved=save(client,memory_book,line)
    revised=chapter_entry();revised.update(note_id=ref["note_id"],expected_version=1,
        claim="交付行为已核对，其他解释待定",change_reason="删掉过度推断")
    save(client,memory_book,revised)
    result=detail(client,memory_book,saved["note_id"])["data"]
    assert result["storyline"]["stale_node_count"]==4
    assert all(node["needs_revision"] for node in result["nodes"])
    assert result["edges"]==[] and result["hidden_edge_count"]==4
    raw=json.dumps(result,ensure_ascii=False)
    assert "待失效的合成解释" not in raw and "甲某核实交付时间" not in raw


def test_graph_requires_reading_context(client,memory_book):
    ref=save(client,memory_book,chapter_entry());line=save(client,memory_book,branch_line(ref))
    with SessionLocal() as db:
        db.query(ReaderProgress).filter(ReaderProgress.book_id==memory_book).delete()
        db.commit()
    assert detail(client,memory_book,line["note_id"])["error"]=="no_reading_context"
