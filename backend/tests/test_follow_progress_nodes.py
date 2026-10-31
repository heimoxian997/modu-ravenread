"""Synthetic follow-only node grouping. Author: heimoxian."""
import json
from app.database import SessionLocal
from app.models.companion_read import CompanionRead
from app.schemas.reading_note import StorylinePatch
from app.services import companion_organization_service as org, companion_read_service as follow
from app.services import reading_note_service as notes
from test_reading_memory import memory_book, entry, save


def test_batches_merge_own_node_but_preserve_manual_edits(client, memory_book, monkeypatch):
    refs = [save(client, memory_book, entry(title=f"合成线索{i}")) for i in range(4)]
    monkeypatch.setattr(follow, "cancelled", lambda *a: False)
    target = None
    def model(provider, messages, **kwargs):
        data = json.loads(messages[-1]["content"])
        return {"content": json.dumps({"groups": [{"title": "纸伞交接", "overview": "核对纸伞交接",
            "line_id": target, "event_ids": data["current_event_ids"]}]})}
    monkeypatch.setattr(org.llm_service, "chat_completion", model)
    def batch(items): return [{"note_id": r["note_id"], "version": r["version"]} for r in items]
    with SessionLocal() as db:
        row = CompanionRead(book_id=memory_book, chapter_idx=0, organization={})
        changes, _ = org.process_batch(db, row, batch(refs[:2]), {}, "test")
        saved = notes.upsert(db, memory_book, changes[0])["data"]
        target = saved["note_id"]
        row.organization = {"lines": [saved]}
        changes, _ = org.process_batch(db, row, batch(refs[2:3]), {}, "test")
        assert len(changes) == 1 and len(changes[0].nodes) == 1
        assert len(changes[0].nodes[0].evidence) == 3
        assert not changes[0].edges
        saved = notes.upsert(db, memory_book, changes[0])["data"]
        row.organization = {"lines": [saved]}
        # Editing a visible title creates a new version; automatic grouping cannot overwrite it.
        notes.upsert(db, memory_book, StorylinePatch(kind="storyline_patch", note_id=target,
            expected_version=saved["version"], change_reason="合成人工修订", title="人工纸伞主题"))
        # Include the line through a prior-chapter node to exercise the existing-node guard.
        latest = {n.note_id: n for n in notes._latest(db, memory_book, 1).all()}
        monkeypatch.setattr(org, "adjacent_context", lambda *a: (set(), [], [latest[target]]))
        changes, dispositions = org.process_batch(db, row, batch(refs[3:]), {}, "test")
        assert changes == [] and dispositions[0]["status"] == "standalone"
