"""Synthetic structured-response and legacy-cache recovery checks. Author: heimoxian."""
import json
import pytest
from app.database import SessionLocal
from app.models.companion_read import CompanionRead
from app.services import companion_read_service as service
from test_companion_read import setup


@pytest.mark.parametrize("wrap", [lambda s:s, lambda s:'```json\n'+s+'\n```', lambda s:'```\n'+s+'\n```'])
def test_fenced_complete_json_retains_events(wrap):
    event = {"title": "交信", "action": "林岚交信", "participants": ["林岚"], "quote": "林岚交给周砚一封未拆的信。"}
    summary, events = service.parse_result(wrap(json.dumps({"summary": "合成概括", "events": [event]}, ensure_ascii=False)))
    assert summary == "合成概括" and events == [event]


@pytest.mark.parametrize("content", ['```json\n{"summary":', '{"summary":"概括"}', '{"summary":"概括","events":"bad"}', '普通文字'])
def test_invalid_output_is_not_a_success(content):
    with pytest.raises(service.FollowFormatError): service.parse_result(content)


def test_truncation_does_not_advance_coverage(monkeypatch):
    book = setup(monkeypatch)
    monkeypatch.setattr(service.llm_service, "chat_completion", lambda *a, **k: {"content": '```json\n{"summary":'})
    with SessionLocal() as db: state, work = service.prepare(db, book, 0)
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.status == "failed" and row.covered == 0 and row.segments == []


def test_legacy_fenced_cache_repairs_without_model(monkeypatch):
    book = setup(monkeypatch)
    with SessionLocal() as db:
        state, work = service.prepare(db, book, 0)
        row = db.get(CompanionRead, state["id"])
        row.status = "completed"; row.covered = row.total
        row.segments = [{"start": 0, "end": row.total, "summary": '```json\n{"summary":"合成概括","events":[]}\n```', "events": []}]
        db.commit()
        assert service.snapshot(row)["status"] == "failed"
        state, work = service.prepare(db, book, 0, retry=True)
    monkeypatch.setattr(service.llm_service, "chat_completion", lambda *a, **k: pytest.fail("cache repair must not call model"))
    service.run(state["id"], *work)
    with SessionLocal() as db:
        row = db.get(CompanionRead, state["id"])
        assert row.status == "completed" and row.segments[0]["summary"] == "合成概括"
