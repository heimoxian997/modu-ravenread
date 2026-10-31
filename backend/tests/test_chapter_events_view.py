"""Chapter event browsing uses synthetic evidence only. Author: heimoxian."""
from test_reading_memory import memory_book, entry, storyline, save, position


def test_events_include_independent_and_linked_without_follow_job(client, memory_book):
    first = save(client, memory_book, entry())
    save(client, memory_book, entry("独立合成事件"))
    line = save(client, memory_book, storyline(first))
    data = client.get(f"/api/books/{memory_book}/chapter-events/1").json()["data"]["data"]
    assert data["total"] == 2
    linked = next(e for e in data["items"] if e["note_id"] == first["note_id"])
    assert linked["storylines"][0]["note_id"] == line["note_id"]
    assert linked["evidence"][0]["chapter_number"] == 1
    assert sum(not e["storylines"] for e in data["items"]) == 1
    assert not client.get(f"/api/books/{memory_book}/chapter-events/3").json()["data"]["ok"]
    position(memory_book, 3)
    assert not client.get(f"/api/books/{memory_book}/chapter-events/2").json()["data"]["ok"]
    assert client.get(f"/api/books/{memory_book}/chapter-events/3").json()["data"]["data"]["total"] == 0


def test_event_pages_have_no_silent_truncation(client, memory_book):
    for i in range(23): save(client, memory_book, entry(f"合成事件{i}"))
    url = f"/api/books/{memory_book}/chapter-events/1"
    first = client.get(url).json()["data"]["data"]
    second = client.get(url, params={"offset": first["next_offset"]}).json()["data"]["data"]
    assert first["total"] == 23 and len(first["items"]) == 20
    assert len(second["items"]) == 3 and second["next_offset"] is None
    assert len({e["note_id"] for e in first["items"] + second["items"]}) == 23
