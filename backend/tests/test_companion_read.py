"""Synthetic follow-reading lifecycle tests. Author: heimoxian."""
from app.database import SessionLocal
from app.models.companion_read import CompanionRead
from app.services import companion_read_service as service
from test_agent_capability import _book


def setup(monkeypatch, text="林岚交给周砚一封未拆的信。"):
    with SessionLocal() as db:
        book = _book(db)
    monkeypatch.setattr(service, "chapter_text", lambda *args: text)
    monkeypatch.setattr(service.llm_provider_service, "get_active_provider_config",
                        lambda db: {"model": "synthetic-model"})
    return book


def test_deduplicate_and_resume_partial(monkeypatch):
    book = setup(monkeypatch, "合成正文" * 10)
    monkeypatch.setattr(service, "SEGMENT_SIZE", 10)
    monkeypatch.setattr(service, "MAX_SEGMENTS", 1)
    calls = []
    def model(provider, messages, **kwargs):
        calls.append(messages[-1]["content"])
        return {"content": '{"summary":"合成概括","events":[]}'}
    monkeypatch.setattr(service.llm_service, "chat_completion", model)
    with SessionLocal() as db:
        state, work = service.prepare(db, book, 0)
        assert service.prepare(db, book, 0)[1] is None
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.status == "partial" and row.covered == 10
        state, work = service.prepare(db, book, 0, retry=True)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.covered == 20 and len(row.segments) == 2
    assert len(calls) == 2


def test_cancel_during_model_does_not_publish(monkeypatch):
    book = setup(monkeypatch)
    with SessionLocal() as db:
        state, work = service.prepare(db, book, 0)
    def model(*args, **kwargs):
        with SessionLocal() as db:
            row = db.get(CompanionRead, state["id"])
            row.status = "cancelled"
            db.commit()
        assert kwargs["cancel_requested"]()
        return {"content": "不得发布的晚到结果"}
    monkeypatch.setattr(service.llm_service, "chat_completion", model)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.status == "cancelled" and row.covered == 0 and row.segments == []


def test_recovery_and_model_failure_preserve_safe_state(monkeypatch):
    book = setup(monkeypatch)
    with SessionLocal() as db:
        state, work = service.prepare(db, book, 0)
        service.recover(db)
        assert db.get(CompanionRead, state["id"]).status == "failed"
        state, work = service.prepare(db, book, 0, retry=True)
    monkeypatch.setattr(service.llm_service, "chat_completion", lambda *a, **k: {"error": "private provider detail"})
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.status == "failed" and row.covered == 0
        assert "private" not in row.error


def test_context_is_source_bound_and_bounded(monkeypatch):
    book = setup(monkeypatch)
    monkeypatch.setattr(service.llm_service, "chat_completion",
                        lambda *a, **k: {"content": '{"summary":"合成概括","events":[]}'})
    with SessionLocal() as db:
        state, work = service.prepare(db, book, 0)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        assert 0 < len(service.context(db, book, 0)) < 2600
        monkeypatch.setattr(service, "chapter_text", lambda *a: "正文已修订")
        assert service.context(db, book, 0) == ""


def test_api_duplicate_observer_does_not_own_task(monkeypatch):
    from fastapi import BackgroundTasks
    from app.api.companion_read import start, Start
    from app.services import reader_progress_service
    book = setup(monkeypatch)
    monkeypatch.setattr(reader_progress_service, "get", lambda *a: {"available": True, "chapter_idx": 0})
    with SessionLocal() as db:
        first_tasks, second_tasks = BackgroundTasks(), BackgroundTasks()
        first = start(book, Start(chapter_idx=0), first_tasks, db)
        second = start(book, Start(chapter_idx=0), second_tasks, db)
        assert first.data["started_here"] is True
        assert second.data["started_here"] is False
        assert len(first_tasks.tasks) == 1
        assert len(second_tasks.tasks) == 0


def test_api_rejects_future_chapter_before_model_selection(monkeypatch):
    import pytest
    from fastapi import BackgroundTasks
    from app.api.companion_read import start, Start
    from app.common.exceptions import BusinessException
    from app.services import reader_progress_service
    book = setup(monkeypatch)
    monkeypatch.setattr(reader_progress_service, "get", lambda *a: {"available": True, "chapter_idx": 0})
    monkeypatch.setattr(service, "prepare", lambda *a: pytest.fail("must not prepare future chapter"))
    with SessionLocal() as db, pytest.raises(BusinessException):
        start(book, Start(chapter_idx=1), BackgroundTasks(), db)


def test_stale_worker_and_cancel_cannot_affect_retry(monkeypatch):
    from app.api.companion_read import cancel
    book = setup(monkeypatch)
    calls = []
    monkeypatch.setattr(service.llm_service, "chat_completion",
                        lambda *a, **k: calls.append(1) or {"content": '{"summary":"合成概括","events":[]}'})
    with SessionLocal() as db:
        old, old_work = service.prepare(db, book, 0)
        cancel(book, old["id"], old["attempt_id"], db)
        new, new_work = service.prepare(db, book, 0, retry=True)
        assert new["attempt_id"] != old["attempt_id"]
        cancel(book, old["id"], old["attempt_id"], db)
    service.run(old["id"], *old_work)
    assert calls == []
    service.run(new["id"], *new_work)
    assert calls == [1]
    with SessionLocal() as db:
        assert db.get(CompanionRead, new["id"]).status == "completed"


def test_chat_preempts_only_this_book_and_gate_releases(monkeypatch):
    import pytest
    from app.common.exceptions import BusinessException
    book = setup(monkeypatch)
    with SessionLocal() as db:
        other = _book(db)
        first, _ = service.prepare(db, book, 0)
        second, _ = service.prepare(db, other, 0)
        try:
            service.begin_chat(db, book)
            db.expire_all()
            assert db.get(CompanionRead, first["id"]).status == "cancelled"
            assert db.get(CompanionRead, second["id"]).status == "queued"
            with pytest.raises(BusinessException):
                service.prepare(db, book, 0, retry=True)
        finally:
            service.end_chat(book)
        resumed, work = service.prepare(db, book, 0, retry=True)
        assert resumed["status"] == "queued" and work


def test_chat_during_response_discards_late_result(monkeypatch):
    book = setup(monkeypatch)
    with SessionLocal() as db:
        state, work = service.prepare(db, book, 0)
    def model(*args, **kwargs):
        with SessionLocal() as db:
            service.begin_chat(db, book)
        assert kwargs["cancel_requested"]()
        return {"content": "旧回复不得发布"}
    monkeypatch.setattr(service.llm_service, "chat_completion", model)
    try:
        service.run(state["id"], *work)
    finally:
        service.end_chat(book)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.status == "cancelled" and row.covered == 0


def test_schema_upgrade_is_additive_and_repeatable(monkeypatch):
    from sqlalchemy import create_engine
    from app import database
    engine = create_engine("sqlite:///:memory:")
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("CREATE TABLE companion_reads (id INTEGER PRIMARY KEY, status TEXT)")
            conn.exec_driver_sql("INSERT INTO companion_reads VALUES (1, 'completed')")
        monkeypatch.setattr(database, "engine", engine)
        service.ensure_schema()
        service.ensure_schema()
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT status, attempt_id FROM companion_reads").one() == ("completed", "")
    finally:
        engine.dispose()
