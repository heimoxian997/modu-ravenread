"""Synthetic storyline maintenance with version, scope and transaction checks."""
from app.services.note_service import NoteError
from test_reading_memory import memory_book, save, position, tool
from test_chapter_graph import chapter_entry, branch_line
from test_storyline_manual import delete


def root(book):
    return f"/api/books/{book}/storylines"


def detail(client, book, note_id):
    return client.get(f"{root(book)}/{note_id}/edit").json()["data"]["data"]


def trash(client, book):
    return client.get(root(book) + "/deleted").json()["data"]["data"]


def restore(client, book, line, version):
    return client.post(f"{root(book)}/{line}/restore", json={"expected_version": version}).json()["data"]


def pair(client, book, single=False):
    a = save(client, book, chapter_entry())
    b = save(client, book, chapter_entry(title="另一条合成事件"))
    source_payload = branch_line(a)
    if single:
        source_payload.update(nodes=source_payload["nodes"][:1], edges=[])
    source = save(client, book, source_payload)
    target_payload = branch_line(b)
    target_payload.update(title="合成目标线", nodes=target_payload["nodes"][:1], edges=[])
    return source, save(client, book, target_payload), a, b


def move(client, book, source, target, node="a", **extra):
    body = {"expected_version": source["version"], "target_note_id": target["note_id"],
            "target_expected_version": target["version"], **extra}
    return client.post(f"{root(book)}/{source['note_id']}/nodes/{node}/move", json=body).json()["data"]


def test_restore_preserves_graph_and_can_be_deleted_again(client, memory_book):
    source, _, _, _ = pair(client, memory_book)
    before = detail(client, memory_book, source["note_id"])
    deleted = delete(client, memory_book, source["note_id"])["data"]
    assert any(r["note_id"] == source["note_id"] for r in trash(client, memory_book)["items"])
    result = restore(client, memory_book, source["note_id"], deleted["version"])
    assert result["ok"] and result["data"]["version"] == 3
    after = detail(client, memory_book, source["note_id"])
    assert after["nodes"] == before["nodes"] and after["edges"] == before["edges"]
    assert all(r["note_id"] != source["note_id"] for r in trash(client, memory_book)["items"])
    assert restore(client, memory_book, source["note_id"], 2)["error"] == "version_conflict"
    assert delete(client, memory_book, source["note_id"], 3)["ok"]
    assert restore(client, memory_book, source["note_id"], 4)["ok"]


def test_restore_respects_reading_scope_and_hides_historical_tombstones(client, memory_book):
    source, _, _, _ = pair(client, memory_book)
    position(memory_book, 3)
    delete(client, memory_book, source["note_id"])
    position(memory_book, 1)
    assert trash(client, memory_book)["total"] == 0
    assert restore(client, memory_book, source["note_id"], 2)["error"] == "not_found"
    position(memory_book, 4)
    assert restore(client, memory_book, source["note_id"], 2)["ok"]
    assert tool(client, memory_book, "note_query", {"note_id": source["note_id"], "version": 2})["data"]["items"] == []
    position(memory_book, 3)
    assert not client.get(f"{root(memory_book)}/{source['note_id']}").json()["data"]["ok"]
    assert all(r["note_id"] != source["note_id"] for r in tool(client, memory_book, "list_storylines", {})["data"]["items"])


def test_restored_stale_evidence_is_flagged_without_rewriting_refs(client, memory_book):
    source, _, event, _ = pair(client, memory_book)
    delete(client, memory_book, source["note_id"])
    revised = chapter_entry()
    revised.update(note_id=event["note_id"], expected_version=1, claim="核对后的合成结论")
    save(client, memory_book, revised)
    assert restore(client, memory_book, source["note_id"], 2)["ok"]
    data = client.get(f"{root(memory_book)}/{source['note_id']}").json()["data"]["data"]
    assert data["storyline"]["needs_revision"]
    assert all(n["evidence"][0]["version"] == 1 for n in data["nodes"])


def test_move_preserves_refs_and_unrelated_branches(client, memory_book):
    source, target, event, _ = pair(client, memory_book)
    result = move(client, memory_book, source, target, connect_after="start")
    assert result["ok"] and not result["data"]["source_deleted"]
    src = detail(client, memory_book, source["note_id"])
    dst = detail(client, memory_book, target["note_id"])
    assert src["version"] == dst["version"] == 2
    assert {n["node_id"] for n in src["nodes"]} == {"start", "b", "merge"}
    assert {(e["source"], e["target"]) for e in src["edges"]} == {("start", "b"), ("b", "merge")}
    moved = next(n for n in dst["nodes"] if n["node_id"] == result["data"]["node_id"])
    assert moved["evidence"] == [{"note_id": event["note_id"], "version": 1}]
    assert dst["edges"][0]["target"] == moved["node_id"]
    assert tool(client, memory_book, "note_query", {"note_id": event["note_id"], "view": "entry"})["data"]["items"]


def test_moving_last_node_deletes_source_reversibly(client, memory_book):
    source, target, _, _ = pair(client, memory_book, single=True)
    result = move(client, memory_book, source, target, node="start")
    assert result["ok"] and result["data"]["source_deleted"]
    assert len(detail(client, memory_book, target["note_id"])["nodes"]) == 2
    assert trash(client, memory_book)["items"][0]["note_id"] == source["note_id"]
    assert restore(client, memory_book, source["note_id"], 2)["ok"]


def test_move_rejects_conflicts_duplicates_and_invalid_targets(client, memory_book):
    source, target, _, _ = pair(client, memory_book)
    assert move(client, memory_book, source, source)["error"] == "invalid_arguments"
    assert move(client, memory_book, source, target, target_expected_version=99)["error"] == "version_conflict"
    assert move(client, memory_book, source, target, expected_version=99)["error"] == "version_conflict"
    assert move(client, memory_book, source, target, connect_after="missing")["error"] == "invalid_arguments"
    assert move(client, memory_book, source, target)["ok"]
    latest_source = {**source, "version": 2}
    latest_target = {**target, "version": 2}
    assert move(client, memory_book, latest_source, latest_target, node="b")["error"] == "duplicate_node"
    assert detail(client, memory_book, source["note_id"])["version"] == 2


def test_move_rolls_back_target_if_source_change_fails(client, memory_book, monkeypatch):
    source, target, _, _ = pair(client, memory_book)
    from app.services import reading_note_service as notes
    original = notes.upsert

    def reject_source(db, book_id, note, **kwargs):
        if note.note_id == source["note_id"]:
            raise NoteError("version_conflict", "合成并发冲突")
        return original(db, book_id, note, **kwargs)

    monkeypatch.setattr(notes, "upsert", reject_source)
    assert move(client, memory_book, source, target)["error"] == "version_conflict"
    assert detail(client, memory_book, source["note_id"])["version"] == 1
    assert detail(client, memory_book, target["note_id"])["version"] == 1
    assert len(detail(client, memory_book, target["note_id"])["nodes"]) == 1


def test_move_rejects_stale_evidence_and_future_target(client, memory_book):
    source, target, event, _ = pair(client, memory_book)
    revised = chapter_entry()
    revised.update(note_id=event["note_id"], expected_version=1, claim="另一条合成结论")
    save(client, memory_book, revised)
    assert move(client, memory_book, source, target)["error"] == "stale_evidence"
    position(memory_book, 3)
    payload = branch_line(event)
    payload["title"] = "后期整理的合成目标线"
    # Use the current event version for a later-created target.
    payload["nodes"][0]["evidence"][0]["version"] = 2
    future = save(client, memory_book, payload)
    position(memory_book, 1)
    assert move(client, memory_book, source, future)["error"] == "not_found"


def test_adding_node_preserves_existing_branches_and_links(client, memory_book):
    source, _, _, event = pair(client, memory_book)
    payload = {"kind": "storyline_patch", "note_id": source["note_id"], "expected_version": 1,
        "change_reason": "合成手动添加节点", "nodes": [{"node_id": "added", "chapter_number": 1,
        "conclusion": "合成补充节点", "evidence": [{"note_id": event["note_id"], "version": 1}]}],
        "edges": [{"source": "merge", "target": "added", "relation": "follows"}]}
    assert tool(client, memory_book, "note_upsert", payload)["ok"]
    after = detail(client, memory_book, source["note_id"])
    assert len(after["nodes"]) == 5 and len(after["edges"]) == 5
