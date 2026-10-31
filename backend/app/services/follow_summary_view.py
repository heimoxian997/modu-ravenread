"""Read-only follow overview for the summary page. Author: heimoxian."""
from app.models.companion_read import CompanionRead
from app.services import companion_read_service as follow, reader_progress_service, reading_note_service as notes


def get(db, book_id, chapter_idx, *, respect_progress=True, include_events=True):
    progress = reader_progress_service.get(db, book_id)
    if respect_progress and (not progress.get("available") or chapter_idx > progress["chapter_idx"]):
        return None
    if not db.query(CompanionRead.id).filter_by(book_id=book_id, chapter_idx=chapter_idx).first():
        return None
    text = follow.chapter_text(db, book_id, chapter_idx)
    _, row = follow.find(db, book_id, chapter_idx, text)
    if row is None:
        return None
    parts, covered = [], 0
    for segment in sorted(row.segments, key=lambda s: s["start"]):
        summary = segment.get("summary", "")
        if segment["start"] != covered or not summary.strip() or follow.looks_structured(summary):
            break
        parts.append(summary)
        covered = segment["end"]
    if not parts:
        return None
    events = [notes._public(n) for n in (notes._latest(db, book_id, chapter_idx + 1).all() if include_events else [])
        if n.kind == "entry" and n.content.get("event_chapters") == [chapter_idx + 1]
        and n.content.get("status", "active") == "active" and n.content.get("payload", {}).get("type") == "event"
        and notes._entry_scope_valid(n.content)]
    return {"source": "follow", "source_id": row.id, "complete": covered == len(text), "covered": covered, "total": len(text),
            "parts": parts, "events": events}
