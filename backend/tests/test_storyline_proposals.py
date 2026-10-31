"""Synthetic review and adjacent-follow contracts. Author: heimoxian."""
import json
import pytest
from pydantic import TypeAdapter
from app.database import SessionLocal
from app.models.companion_read import CompanionRead
from app.models.reading_note import ReadingNoteRevision
from app.models.storyline_proposal import StorylineProposal
from app.schemas.reading_note import NoteWrite
from app.services import reading_note_service as notes, storyline_proposal_service as proposals
from app.services import companion_organization_service as organization, companion_read_service as follow
from app.services.note_service import NoteError
from test_reading_memory import memory_book, entry, storyline, position, save, tool


def test_review_reject_deduplicate_and_accept(client, memory_book):
    ref = save(client, memory_book, entry())
    draft = storyline(ref)
    with SessionLocal() as db:
        result = proposals.submit(db, memory_book, TypeAdapter(NoteWrite).validate_python(draft))["data"]
    with SessionLocal() as db:
        assert db.query(ReadingNoteRevision).filter_by(kind="storyline").count() == 0
        proposals.decide(db, memory_book, result["proposal_id"], False)
        assert proposals.submit(db, memory_book, TypeAdapter(NoteWrite).validate_python(draft))["data"]["status"] == "rejected"
        draft["title"] = "纸伞交接"
        second = proposals.submit(db, memory_book, TypeAdapter(NoteWrite).validate_python(draft))["data"]
        proposals.decide(db, memory_book, second["proposal_id"], True)
        proposals.decide(db, memory_book, second["proposal_id"], True)
        assert db.query(ReadingNoteRevision).filter_by(kind="storyline").count() == 1


def test_conflict_preserves_pending_and_same_chapter(client, memory_book):
    ref = save(client, memory_book, entry())
    line = save(client, memory_book, storyline(ref))
    patch = {"kind": "storyline_patch", "note_id": line["note_id"], "expected_version": 1,
             "change_reason": "合成修订", "title": "纸伞线索"}
    with SessionLocal() as db:
        model = TypeAdapter(NoteWrite).validate_python(patch)
        proposed = proposals.submit(db, memory_book, model)["data"]["proposal_id"]
        notes.upsert(db, memory_book, model)
        with pytest.raises(NoteError): proposals.decide(db, memory_book, proposed, True)
        assert db.get(StorylineProposal, proposed).status == "pending"
    position(memory_book, 3)
    bad = storyline(ref)
    bad["nodes"][0]["chapter_number"] = 3
    assert tool(client, memory_book, "note_upsert", bad)["error"] == "evidence_chapter_mismatch"


def test_thirty_events_resume_without_silent_truncation(client, memory_book, monkeypatch):
    for i in range(30): save(client, memory_book, entry(title=f"合成事件{i}"))
    text = None
    with SessionLocal() as db:
        text = follow.chapter_text(db, memory_book, 0)
        source_hash, _ = follow.find(db, memory_book, 0, text)
        row = CompanionRead(book_id=memory_book, chapter_idx=0, source_hash=source_hash,
            status="running", attempt_id="synthetic", total=len(text), covered=len(text), segments=[], organization={})
        db.add(row); db.commit(); job_id = row.id
    seen = []
    def model(provider, messages, **kwargs):
        context = json.loads(messages[-1]["content"])
        seen.extend(context["current_event_ids"])
        return {"content": '{"groups":[]}'}
    monkeypatch.setattr(organization.llm_service, "chat_completion", model)
    organization.organize(job_id, "synthetic", {})
    with SessionLocal() as db:
        row = db.get(CompanionRead, job_id)
        assert row.organization["total_events"] == 30
        assert row.organization["processed_events"] == 16
        assert row.organization["status"] == "partial"
        row.status = "running"; db.commit()
    organization.organize(job_id, "synthetic", {})
    with SessionLocal() as db:
        assert db.get(CompanionRead, job_id).organization["processed_events"] == 30
    assert len(seen) == len(set(seen)) == 30


def test_adjacent_context_skips_extras_but_not_unprepared_chapters(client, memory_book):
    ref = save(client, memory_book, entry())
    save(client, memory_book, storyline(ref))
    position(memory_book, 4)
    with SessionLocal() as db:
        latest = {n.note_id: n for n in notes._latest(db, memory_book, 4).all()}
        row = CompanionRead(book_id=memory_book, chapter_idx=2)
        previous, entries, lines = organization.adjacent_context(db, row, latest)
        assert previous == {1} and entries and lines
        row.chapter_idx = 3
        previous, entries, lines = organization.adjacent_context(db, row, latest)
        assert previous == {1, 3} and entries and lines


def test_recent_three_chapters_bound_events_and_lines(memory_book):
    from types import SimpleNamespace
    from app.models.chapter import Chapter
    with SessionLocal() as db:
        for idx in (4, 5):
            db.add(Chapter(book_id=memory_book, idx=idx, title="合成窗口章节", kind="main",
                           char_start=0, char_end=0, word_count=0))
        db.commit()
        latest = {}
        for chapter in (1, 2, 3, 4, 5, 6):
            latest[f"event{chapter}"] = SimpleNamespace(kind="entry", content={"event_chapters": [chapter]})
            latest[f"line{chapter}"] = SimpleNamespace(kind="storyline", content={"nodes": [{"chapter_number": chapter}]})
        row = CompanionRead(book_id=memory_book, chapter_idx=5)
        previous, events, lines = organization.adjacent_context(db, row, latest)
        assert previous == {3, 4, 5}
        assert {n.content["event_chapters"][0] for n in events} == {3, 4, 5}
        assert {n.content["nodes"][0]["chapter_number"] for n in lines} == {3, 4, 5}
        # Missing recent results do not expand the chapter window into older evidence.
        previous, events, lines = organization.adjacent_context(db, row, {"old": latest["event1"]})
        assert previous == {3, 4, 5} and not events and not lines


@pytest.mark.parametrize("target", ["unrelated_line", "unknown_event"])
def test_follow_rejects_unseen_references(client, memory_book, monkeypatch, target):
    ref = save(client, memory_book, entry())
    with SessionLocal() as db:
        row = CompanionRead(book_id=memory_book, chapter_idx=0, organization={})
        response = {"groups": [{"title": "合成线索", "overview": "合成概述",
            "line_id": "unrelated_line" if target == "unrelated_line" else None,
            "event_ids": [ref["note_id"]] if target == "unrelated_line" else ["unknown_event"]}]}
        monkeypatch.setattr(follow, "cancelled", lambda *a: False)
        monkeypatch.setattr(organization.llm_service, "chat_completion", lambda *a, **k: {"content": json.dumps(response)})
        with pytest.raises(ValueError, match=target.replace("unrelated_line", "nonadjacent_line")):
            organization.process_batch(db, row, [{"note_id": ref["note_id"], "version": 1}], {}, "synthetic")


def test_frontend_edit_direct_save_and_progress_gate(client, memory_book):
    ref = save(client, memory_book, entry())
    line = save(client, memory_book, storyline(ref))
    url = f"/api/books/{memory_book}/storylines/{line['note_id']}/edit"
    current = client.get(url).json()["data"]
    assert current["ok"] and current["data"]["version"] == 1
    patch = {"kind": "storyline_patch", "note_id": line["note_id"], "expected_version": 1,
             "change_reason": "用户手动编辑", "title": "合成修订标题"}
    position(memory_book, 3)
    result = client.post(f"/api/books/{memory_book}/reading-notes/upsert", json=patch).json()["data"]
    assert result["ok"] and result["data"]["version"] == 2
    conflict = client.post(f"/api/books/{memory_book}/reading-notes/upsert", json=patch).json()["data"]
    assert conflict["error"] == "version_conflict"
    position(memory_book, 1)
    assert not client.get(url).json()["data"]["ok"]


def test_paragraph_split_has_no_gap_and_long_paragraph_is_bounded():
    text = "合成段落" * 800 + "\n" + "第二段" * 2400
    position = 0
    parts = []
    while position < len(text):
        end = follow.segment_end(text, position)
        assert position < end <= position + follow.SEGMENT_SIZE
        parts.append(text[position:end]); position = end
    assert parts[0].endswith("\n")
    assert "".join(parts) == text
