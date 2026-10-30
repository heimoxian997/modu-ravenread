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
    result = tool(client, memory_book, "note_upsert", draft)["data"]
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
        seen.extend(e["note_id"] for e in context["events"])
        return {"content": json.dumps({"changes": [], "dispositions": [{"note_id": e["note_id"],
            "status": "standalone", "reason": "合成独立事件"} for e in context["events"]]})}
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
        assert previous == 1 and entries and lines
        row.chapter_idx = 3
        previous, entries, lines = organization.adjacent_context(db, row, latest)
        assert previous == 3 and not entries and not lines


@pytest.mark.parametrize("change", ["rename", "remote", "replace"])
def test_follow_cannot_rewrite_or_reach_remote_lines(client, memory_book, monkeypatch, change):
    ref = save(client, memory_book, entry())
    line = save(client, memory_book, storyline(ref))
    position(memory_book, 3)
    current = save(client, memory_book, entry("归还", 3, "乙某归还纸伞，甲某道谢。"))
    with SessionLocal() as db:
        row = CompanionRead(book_id=memory_book, chapter_idx=2, organization={})
        old = notes._latest(db, memory_book, 3).filter(ReadingNoteRevision.note_id == line["note_id"]).one()
        patch = {"kind": "storyline_patch", "note_id": line["note_id"], "expected_version": 1,
            "change_reason": "合成检验", "nodes": [{"node_id": "new_event", "chapter_number": 3,
            "conclusion": "归还纸伞", "evidence": [{"note_id": current["note_id"], "version": 1}]}]}
        if change == "rename": patch["title"] = "改写标题"
        if change == "remote": patch["note_id"] = "unrelated_line"
        if change == "replace": patch["nodes"][0]["node_id"] = old.content["nodes"][0]["node_id"]
        response = {"changes": [patch], "dispositions": [{"note_id": current["note_id"], "status": "proposed", "reason": "归还"}]}
        monkeypatch.setattr(follow, "cancelled", lambda *a: False)
        monkeypatch.setattr(organization.llm_service, "chat_completion", lambda *a, **k: {"content": json.dumps(response)})
        with pytest.raises(ValueError):
            organization.process_batch(db, row, [{"note_id": current["note_id"], "version": 1}], {}, "synthetic")


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
