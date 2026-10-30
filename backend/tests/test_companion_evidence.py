"""Synthetic follow-reading evidence publication. Author: heimoxian."""
import json
from app.database import SessionLocal
from app.models.reading_note import ReadingNoteRevision
from app.models.companion_read import CompanionRead
from app.services import companion_read_service as service
from test_reading_memory import memory_book


def test_automatic_storyline_without_chat(memory_book, monkeypatch):
    monkeypatch.setattr(service.llm_provider_service, "get_active_provider_config", lambda db: {"model": "synthetic"})
    calls = []
    def model(provider, messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {"content": json.dumps({"summary": "交伞后等候。", "events": [
                {"title": "交伞", "action": "甲某交伞", "participants": ["甲某", "乙某"], "quote": "甲某把纸伞交给乙某。"},
                {"title": "等候", "action": "甲某在院中等候", "participants": ["甲某"], "quote": "甲某把纸伞交给乙某。甲某在院中等候。"}]}, ensure_ascii=False)}
        context = json.loads(messages[-1]["content"])
        nodes = [{"node_id": f"event_{i}", "chapter_number": 1, "conclusion": e["content"]["claim"],
                  "evidence": [{"note_id": e["note_id"], "version": e["version"]}]} for i, e in enumerate(context["events"])]
        return {"content": json.dumps({"changes": [{"kind": "storyline", "title": "交伞与等候", "category": "plot",
            "entities": ["甲某", "乙某"], "overview": "交伞后留在院中", "stage": "等候", "nodes": nodes, "edges": []}], "dispositions": [{"note_id": e["note_id"], "status": "proposed", "reason": "synthetic continuation"} for e in context["events"]]}, ensure_ascii=False)}
    monkeypatch.setattr(service.llm_service, "chat_completion", model)
    with SessionLocal() as db:
        state, work = service.prepare(db, memory_book, 0)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.organization["status"] == "completed"
        assert len(row.organization["lines"]) == 1
        assert db.query(ReadingNoteRevision).filter_by(book_id=memory_book, kind="storyline").count() == 0
        assert len(calls) == 2


def test_follow_events_require_actual_quote_and_reuse_note_service(memory_book, monkeypatch):
    monkeypatch.setattr(service.llm_provider_service, "get_active_provider_config", lambda db: {"model": "synthetic"})
    content = {"summary": "甲某交出了纸伞，仍有待讨论的疑问。", "events": [
        {"title": "交付纸伞", "action": "甲某把纸伞交给乙某", "participants": ["甲某", "乙某"], "quote": "甲某把纸伞交给乙某。"},
        {"title": "无依据的事", "action": "甲某拿走宝箱", "participants": ["甲某"], "quote": "这段引文不存在于合成正文"},
    ]}
    monkeypatch.setattr(service.llm_service, "chat_completion", lambda *a, **k: {"content": json.dumps(content, ensure_ascii=False)})
    with SessionLocal() as db:
        state, work = service.prepare(db, memory_book, 0)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        notes = db.query(ReadingNoteRevision).filter_by(book_id=memory_book).all()
        assert len(notes) == 1 and notes[0].kind == "entry"
        row = db.get(CompanionRead, state["id"])
        assert row.status == "completed"
        assert row.segments[0]["notes"][0]["note_id"] == notes[0].note_id
        assert notes[0].content["evidence"][0]["quote"] == "甲某把纸伞交给乙某。"


def test_organization_failure_retries_without_reading_body(memory_book, monkeypatch):
    monkeypatch.setattr(service.llm_provider_service, "get_active_provider_config", lambda db: {"model": "synthetic"})
    calls = []
    def model(provider, messages, **kwargs):
        calls.append(messages)
        if len(calls) == 1:
            return {"content": json.dumps({"summary": "交伞与等候", "events": [
                {"title": "交伞", "action": "甲某交伞", "participants": ["甲某", "乙某"], "quote": "甲某把纸伞交给乙某。"},
                {"title": "等候", "action": "甲某等候", "participants": ["甲某"], "quote": "甲某把纸伞交给乙某。甲某在院中等候。"}]}, ensure_ascii=False)}
        context = json.loads(messages[-1]["content"])
        return {"content": "invalid" if len(calls) == 2 else json.dumps({"changes": [], "dispositions": [
            {"note_id": e["note_id"], "status": "standalone", "reason": "synthetic independent event"} for e in context["events"]]})}
    monkeypatch.setattr(service.llm_service, "chat_completion", model)
    with SessionLocal() as db:
        state, work = service.prepare(db, memory_book, 0)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.organization["status"] == "failed" and row.covered == row.total
        before = list(row.segments)
        state, work = service.prepare(db, memory_book, 0, retry=True)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.organization["status"] == "completed" and row.segments == before
        assert len(calls) == 3
