"""Manual authoring and deletion use synthetic chapter evidence only."""
from test_reading_memory import memory_book, save, position, tool
from test_chapter_graph import chapter_entry, branch_line


def delete(client, book, line, version=1):
    return client.request("DELETE", f"/api/books/{book}/storylines/{line}",
                          json={"expected_version": version}).json()["data"]


def test_manual_event_and_line_use_existing_evidence_checks(client, memory_book):
    url = f"/api/books/{memory_book}/reading-notes/upsert"
    event = client.post(url, json=chapter_entry()).json()["data"]
    assert event["ok"]
    line = client.post(url, json=branch_line(event["data"])).json()["data"]
    assert line["ok"]
    chapter = client.get(f"/api/books/{memory_book}/chapter-events/1").json()["data"]["data"]
    assert chapter["items"][0]["storylines"][0]["note_id"] == line["data"]["note_id"]
    invalid = chapter_entry(quote="合成文本中没有这段原文")
    assert client.post(url, json=invalid).json()["data"]["error"] == "quote_not_found"
    invalid = branch_line(event["data"])
    invalid["nodes"][0]["evidence"][0]["version"] = 99
    assert not client.post(url, json=invalid).json()["data"]["ok"]


def test_deletion_hides_all_views_and_keeps_events(client, memory_book):
    event = save(client, memory_book, chapter_entry())
    payload = branch_line(event)
    line = save(client, memory_book, payload)
    position(memory_book, 3)
    assert delete(client, memory_book, line["note_id"])["ok"]
    # Rewinding must not bring a manually deleted line back.
    position(memory_book, 1)
    root = f"/api/books/{memory_book}"
    assert client.get(root + "/storylines").json()["data"]["data"]["items"] == []
    assert client.get(root + f"/storylines/{line['note_id']}").json()["data"]["error"] == "not_found"
    assert not client.get(root + f"/storylines/{line['note_id']}/edit").json()["data"]["ok"]
    assert tool(client, memory_book, "list_storylines", {})["data"]["items"] == []
    assert tool(client, memory_book, "note_query", {"note_id": line["note_id"], "version": 1})["error"] == "not_found"
    assert tool(client, memory_book, "note_query", {"note_id": event["note_id"], "view": "entry"})["data"]["items"]
    chapter = client.get(root + "/chapter-events/1").json()["data"]["data"]
    assert len(chapter["items"]) == 1 and chapter["items"][0]["storylines"] == []
    payload.update(note_id=line["note_id"], expected_version=1)
    assert tool(client, memory_book, "note_upsert", payload)["error"] == "not_found"
    recreated = save(client, memory_book, branch_line(event))
    assert recreated["note_id"] != line["note_id"]


def test_delete_checks_latest_version_and_reading_scope(client, memory_book):
    event = save(client, memory_book, chapter_entry())
    payload = branch_line(event)
    line = save(client, memory_book, payload)
    payload.update(note_id=line["note_id"], expected_version=1, title="合成剧情线修订")
    position(memory_book, 3)
    save(client, memory_book, payload)
    assert delete(client, memory_book, line["note_id"], 1)["error"] == "version_conflict"
    position(memory_book, 1)
    assert delete(client, memory_book, line["note_id"], 2)["error"] == "not_found"
    position(memory_book, 3)
    assert delete(client, memory_book, line["note_id"], 2)["ok"]
    assert not delete(client, memory_book, event["note_id"])["ok"]


def test_delete_cannot_reappear_in_old_cursor(client, memory_book):
    event = save(client, memory_book, chapter_entry())
    first = save(client, memory_book, branch_line(event))
    payload = branch_line(event)
    payload["title"] = "另一条合成剧情线"
    second = save(client, memory_book, payload)
    root = f"/api/books/{memory_book}"
    data = client.get(root + "/storylines?limit=1").json()["data"]["data"]
    unseen = second if data["items"][0]["note_id"] == first["note_id"] else first
    assert delete(client, memory_book, unseen["note_id"])["ok"]
    page = client.get(root + "/storylines", params={"limit": 1, "cursor": data["cursor"]}).json()["data"]["data"]
    assert all(item["note_id"] != unseen["note_id"] for item in page["items"])
