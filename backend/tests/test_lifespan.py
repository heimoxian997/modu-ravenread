"""Startup failures preserve their original error and close the database session."""

import asyncio

import pytest

from app import main
from app.services import correction_service, role_preparation_job_service, note_embedding_service


@pytest.mark.parametrize("service,method", [
    (main.draft_service, "cleanup_expired_drafts"),
    (main.text_index_job_service, "mark_interrupted_jobs"),
    (main.summary_job_service, "mark_interrupted_jobs"),
    (correction_service, "mark_interrupted"),
])
def test_startup_cleanup_failure_does_not_enter_shutdown_or_mask_error(monkeypatch, service, method):
    events = []
    failure = RuntimeError("synthetic startup failure")

    class Session:
        def close(self):
            events.append("closed")

    monkeypatch.setattr(main, "SessionLocal", Session)
    for module, name in [
        (main.draft_service, "cleanup_expired_drafts"),
        (main.text_index_job_service, "mark_interrupted_jobs"),
        (main.summary_job_service, "mark_interrupted_jobs"),
        (correction_service, "mark_interrupted"),
    ]:
        monkeypatch.setattr(module, name, lambda db: None)

    def fail(db):
        raise failure

    monkeypatch.setattr(service, method, fail)
    monkeypatch.setattr(role_preparation_job_service, "shutdown", lambda: events.append("shutdown"))
    monkeypatch.setattr(note_embedding_service, "start", lambda: events.append("start"))
    monkeypatch.setattr(note_embedding_service, "stop", lambda: events.append("stop"))

    async def start():
        async with main.lifespan(main.app):
            events.append("started")

    with pytest.raises(RuntimeError) as caught:
        asyncio.run(start())
    assert caught.value is failure
    assert events == ["closed"]
