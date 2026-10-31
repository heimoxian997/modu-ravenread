"""Synthetic read-only summary fallback. Author: heimoxian."""
from app.database import SessionLocal
from app.models.companion_read import CompanionRead
from app.models.chapter_summary import ChapterSummary
from app.services import companion_read_service as follow
from test_reading_memory import memory_book


def test_overview_fallback_preserves_summary_and_counts(client, memory_book):
    base = f"/api/books/{memory_book}/summaries"
    with SessionLocal() as db:
        text = follow.chapter_text(db, memory_book, 0)
        digest, _ = follow.find(db, memory_book, 0, text)
        row = CompanionRead(book_id=memory_book, chapter_idx=0, source_hash=digest, status="completed",
            total=len(text), covered=len(text), segments=[{"start": 0, "end": len(text), "summary": "合成概览"}],
            organization={"status": "failed"})
        db.add(row); db.commit(); job = row.id
    data = client.get(base + '/0').json()["data"]
    assert data["summary"] is None and data["follow_overview"]["complete"]
    assert data["status"] == "pending"
    page = client.get(base).json()["data"]
    assert page["items"][0]["summary_preview"] == "合成概览"
    from app.services.chapter_extract_service import get_overview
    with SessionLocal() as db:
        overview = get_overview(db, memory_book)
        assert overview["done"] == 0 and overview["available_done"] == 1
        assert overview["follow_done"] == 1 and overview["next_available_number"] == 3
    with SessionLocal() as db:
        assert db.query(ChapterSummary).count() == 0
        db.add(ChapterSummary(book_id=memory_book, chapter_idx=0, title="合成章", summary="人工摘要", edited=True))
        db.commit()
    data = client.get(base + '/0').json()["data"]
    assert data["summary"]["summary"] == "人工摘要" and data["summary"]["edited"]
    assert data["follow_overview"]["parts"] == ["合成概览"]
    with SessionLocal() as db:
        assert get_overview(db, memory_book)["available_done"] == 1
    with SessionLocal() as db:
        row = db.get(CompanionRead, job)
        row.segments = [{"start": 0, "end": 20, "summary": "部分概览"}]
        db.commit()
    assert not client.get(base + '/0').json()["data"]["follow_overview"]["complete"]
    with SessionLocal() as db:
        overview = get_overview(db, memory_book)
        assert overview["follow_done"] == 0 and overview["follow_partial"] == 1
    with SessionLocal() as db:
        row = db.get(CompanionRead, job); row.source_hash = "outdated"; db.commit()
    assert client.get(base + '/0').json()["data"]["follow_overview"] is None
