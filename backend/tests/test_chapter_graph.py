"""Synthetic chapter graphs: branching, bounded retrieval and evidence correction."""
import json
import pytest
from test_reading_memory import memory_book, tool, save, entry, position


def chapter_entry(chapter=1,title="交伞事件",quote="甲某把纸伞交给乙某。"):
    data=entry(title,chapter,quote)
    data["chapter_number"]=chapter
    return data


def branch_line(ref):
    evidence=[{"note_id":ref["note_id"],"version":ref["version"]}]
    return {"kind":"storyline","title":"纸伞去向","category":"plot","entities":["甲某","乙某"],
        "overview":"交伞后分两路核实，再汇合","stage":"核实中",
        "nodes":[{"node_id":name,"chapter_number":1,"conclusion":text,"branch":branch,"evidence":evidence}
            for name,text,branch in [("start","甲某交伞","起点"),("a","甲某核实交付时间","甲路"),
                ("b","乙某核实纸伞来历","乙路"),("merge","两路核实汇合","汇合")]],
        "edges":[{"source":a,"target":b,"relation":"follows"} for a,b in [("start","a"),("start","b"),("a","merge"),("b","merge")]]}


def test_chapter_entry_short_claim_and_separate_knowledge_boundary(client,memory_book):
    position(memory_book,4)
    data=chapter_entry();data["claim"]="甲"*181
    assert tool(client,memory_book,"note_upsert",data)["error"]=="invalid_arguments"
    note=save(client,memory_book,chapter_entry())
    assert note["chapter_number"]==1
    assert note["known_by_chapter"]==4
    position(memory_book,1)
    assert tool(client,memory_book,"note_query",{"note_id":note["note_id"]})["data"]["items"]==[]


def test_branching_merging_and_neighborhood_query(client,memory_book):
    ref=save(client,memory_book,chapter_entry())
    line=save(client,memory_book,branch_line(ref))
    data=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"view":"graph","limit":10})["data"]
    assert {n["node_id"] for n in data["graph"]["nodes"]}=={"start","a","b","merge"}
    assert len(data["graph"]["edges"])==4
    visible={n["graph_id"] for n in data["graph"]["nodes"]}
    assert all(e["source"] in visible and e["target"] in visible for e in data["graph"]["edges"])
    local=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"node_id":"a","view":"graph","limit":10})["data"]
    assert {n["node_id"] for n in local["graph"]["nodes"]}=={"start","a","merge"}


def test_invalid_endpoints_and_unexplained_causation_rejected(client,memory_book):
    ref=save(client,memory_book,chapter_entry())
    line=branch_line(ref);line["edges"][0]["target"]="missing"
    assert not tool(client,memory_book,"note_upsert",line)["ok"]
    line=branch_line(ref);line["edges"][0]["relation"]="causes"
    assert not tool(client,memory_book,"note_upsert",line)["ok"]
    line["edges"][0].update(basis="interpretation",reason="叙述视角的解释")
    assert tool(client,memory_book,"note_upsert",line)["ok"]


def test_correction_marks_dependencies_and_hides_outdated_conclusions(client,memory_book):
    ref=save(client,memory_book,chapter_entry())
    line=save(client,memory_book,branch_line(ref))
    position(memory_book,4)
    correction=chapter_entry(title="交伞记录修订")
    correction.update(note_id=ref["note_id"],expected_version=1,claim="甲某交伞，动机暂不确定",change_reason="原解释超出证据")
    result=tool(client,memory_book,"note_upsert",correction)
    assert result["ok"]
    assert result["affected_storylines"][0]["note_id"]==line["note_id"]
    data=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"view":"graph","limit":10})["data"]
    assert all(n["needs_revision"] for n in data["graph"]["nodes"])
    assert "甲某核实交付时间" not in json.dumps(data,ensure_ascii=False)
    assert not data["graph"]["edges"]
    stale=branch_line(ref);stale.update(note_id=line["note_id"],expected_version=1)
    assert tool(client,memory_book,"note_upsert",stale)["error"]=="stale_evidence"
    updated_ref=result["data"]
    revised=branch_line(updated_ref);revised.update(note_id=line["note_id"],expected_version=1,change_reason="核对新证据后重写节点")
    save(client,memory_book,revised)
    refreshed=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"view":"graph","limit":10})["data"]
    assert len(refreshed["graph"]["edges"])==4
    position(memory_book,1)
    early=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"view":"graph","limit":10})["data"]
    assert all(not n.get("needs_revision") for n in early["graph"]["nodes"])
    assert all(n["version"]==1 for n in early["graph"]["nodes"])


def test_disputed_notes_are_not_normal_search_evidence(client,memory_book):
    note=save(client,memory_book,chapter_entry())
    disputed=chapter_entry();disputed.update(note_id=note["note_id"],expected_version=1,status="disputed",change_reason="证据含义待核实")
    save(client,memory_book,disputed)
    assert not tool(client,memory_book,"note_query",{"entities":["甲某"],"view":"events"})["data"]["items"]
    explicit=tool(client,memory_book,"note_query",{"note_id":note["note_id"]})["data"]["items"][0]
    assert explicit["status"]=="disputed"


def test_graph_pages_are_bounded_and_cannot_be_reused_after_rewind(client,memory_book):
    ref=save(client,memory_book,chapter_entry())
    line=save(client,memory_book,branch_line(ref))
    args={"note_id":line["note_id"],"view":"graph","limit":2}
    page=tool(client,memory_book,"note_query",args)["data"]
    assert len(json.dumps(page,ensure_ascii=False))<=6000
    assert page["has_more"]
    following=tool(client,memory_book,"note_query",{**args,"cursor":page["cursor"]})["data"]
    assert len(page["items"])+len(following["items"])==4


def test_topic_expansion_and_safe_storyline_candidates(client,memory_book):
    data=chapter_entry(title="身份隐瞒");data["claim"]="甲某暂时隐瞒身份"
    ref=save(client,memory_book,data)
    save(client,memory_book,branch_line(ref))
    found=tool(client,memory_book,"note_query",{"query":"甲某是谁，真名是什么","view":"events"})["data"]
    assert found["items"]
    missed=tool(client,memory_book,"note_query",{"query":"完全不相关XYZ"})["data"]
    assert missed["storyline_candidates"][0]["title"]=="纸伞去向"
    position(memory_book,4)
    late=branch_line(ref);late["title"]="后期另一支线";save(client,memory_book,late)
    position(memory_book,1)
    missed=tool(client,memory_book,"note_query",{"query":"完全不相关XYZ"})["data"]
    assert "后期另一支线" not in json.dumps(missed,ensure_ascii=False)


def test_targeted_patch_preserves_other_branches_and_removes_attached_edges(client,memory_book):
    ref=save(client,memory_book,chapter_entry())
    initial=branch_line(ref)
    line=save(client,memory_book,initial)
    node={**initial["nodes"][1],"conclusion":"甲某再次核实交付时间"}
    patch={"kind":"storyline_patch","note_id":line["note_id"],"expected_version":1,
        "change_reason":"补充一个节点","nodes":[node]}
    updated=save(client,memory_book,patch)
    assert updated["version"]==2
    data=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"view":"graph","limit":10})["data"]
    assert len(data["graph"]["nodes"])==4 and len(data["graph"]["edges"])==4
    assert next(n for n in data["graph"]["nodes"] if n["node_id"]=="b")["conclusion"]==initial["nodes"][2]["conclusion"]
    assert tool(client,memory_book,"note_upsert",patch)["error"]=="version_conflict"
    remove={"kind":"storyline_patch","note_id":line["note_id"],"expected_version":2,
        "change_reason":"移除一条未证实支线","remove_node_ids":["b"]}
    save(client,memory_book,remove)
    edges=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"view":"edges","limit":10})["data"]
    assert len(edges["items"])==2
    assert all("b" not in (e["source"],e["target"]) for e in edges["items"])
    assert "storyline" in edges


def test_partial_correction_keeps_unreviewed_nodes_quarantined(client,memory_book):
    old=save(client,memory_book,chapter_entry())
    initial=branch_line(old);line=save(client,memory_book,initial)
    correction=chapter_entry();correction.update(note_id=old["note_id"],expected_version=1,
        claim="交付发生，其他解释待定",change_reason="删掉未证实解释")
    corrected=save(client,memory_book,correction)
    node={**initial["nodes"][0],"conclusion":"甲某交付纸伞，其他支线待核对",
        "evidence":[{"note_id":corrected["note_id"],"version":2}]}
    patch={"kind":"storyline_patch","note_id":line["note_id"],"expected_version":1,
        "change_reason":"先核对起点","nodes":[node]}
    save(client,memory_book,patch)
    data=tool(client,memory_book,"note_query",{"note_id":line["note_id"],"view":"graph","limit":10})["data"]
    assert data["storyline"]["stale_node_count"]==3
    assert next(n for n in data["graph"]["nodes"] if n["node_id"]=="a")["needs_revision"]
    assert not next(n for n in data["graph"]["nodes"] if n["node_id"]=="start").get("needs_revision")
    assert data["graph"]["edges"]==[]


def test_patch_cannot_remove_every_node_or_update_deleted_node(client,memory_book):
    ref=save(client,memory_book,chapter_entry());base=branch_line(ref);line=save(client,memory_book,base)
    patch={"kind":"storyline_patch","note_id":line["note_id"],"expected_version":1,
        "change_reason":"测试非法修改","remove_node_ids":[n["node_id"] for n in base["nodes"]]}
    response=client.post(f"/api/books/{memory_book}/reading-notes/upsert",json=patch)
    assert response.status_code==200 and response.json()["data"]["error"]=="invalid_arguments"
    patch.update(remove_node_ids=["start"],nodes=[base["nodes"][0]])
    assert tool(client,memory_book,"note_upsert",patch)["error"]=="invalid_arguments"


def test_large_stale_graph_masks_every_node_and_keeps_pages_bounded(client,memory_book):
    ref=save(client,memory_book,chapter_entry())
    line=branch_line(ref)
    line["nodes"]=[{"node_id":f"node-{i}","chapter_number":1,"conclusion":f"甲某核实第{i}项合成线索",
        "evidence":[{"note_id":ref["note_id"],"version":1}]} for i in range(30)]
    line["edges"]=[{"source":"node-0","target":f"node-{i}","relation":"follows"} for i in range(1,30)]
    saved=save(client,memory_book,line)
    correction=chapter_entry();correction.update(note_id=ref["note_id"],expected_version=1,claim="交伞已核实",change_reason="核对解释")
    save(client,memory_book,correction)
    cursor=None;seen=set()
    while True:
        args={"note_id":saved["note_id"],"view":"graph","limit":10}
        if cursor:args["cursor"]=cursor
        data=tool(client,memory_book,"note_query",args)["data"]
        assert len(json.dumps(data,ensure_ascii=False))<=6000
        assert data["items"]
        assert all(n["needs_revision"] for n in data["graph"]["nodes"])
        assert data["storyline"]["stale_node_count"]==30
        seen.update(n["node_id"] for n in data["items"])
        if not data["has_more"]:break
        cursor=data["cursor"]
    assert len(seen)==30


def test_revelation_can_point_back_but_narrative_order_cannot_cycle(client,memory_book):
    early=save(client,memory_book,chapter_entry())
    position(memory_book,3)
    later=save(client,memory_book,chapter_entry(3,"还伞事件","乙某归还纸伞，甲某道谢。"))
    line=branch_line(early);line["nodes"]=line["nodes"][:2]
    line["nodes"][1].update(chapter_number=3,evidence=[{"note_id":later["note_id"],"version":1}])
    line["edges"]=[{"source":"a","target":"start","relation":"reveals","reason":"后章说明前章行为"}]
    assert tool(client,memory_book,"note_upsert",line)["ok"]
    line["edges"][0]={"source":"a","target":"start","relation":"follows"}
    assert tool(client,memory_book,"note_upsert",line)["error"]=="invalid_arguments"
    line=branch_line(early);line["edges"]=[{"source":"a","target":"b","relation":"follows"},{"source":"b","target":"a","relation":"follows"}]
    assert tool(client,memory_book,"note_upsert",line)["error"]=="invalid_arguments"
