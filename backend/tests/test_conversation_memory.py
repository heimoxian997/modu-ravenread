"""Synthetic long conversation and isolation checks. Author: heimoxian."""
import json
import pytest
from app.database import SessionLocal
from app.models.agent_message import AgentMessage
from app.models.conversation_memory import ConversationDigest
from app.services import conversation_memory_service as memory, agent_context_service, agent_service
from test_agent_step1 import _book, _fake_model as _fake


def seed(db, turns=16, scope="reading", safe=0, book=None):
    book = book or _book(db)
    session = agent_context_service.get_or_create_session(db, book)
    for n in range(turns):
        for role, text in [("user", f"第{n}次讨论：我猜林岚藏了信，但暂未确认。"),
                           ("assistant", "这是猜测，需要原文核对；周砚的行踪仍待讨论。")]:
            agent_context_service.save_message(db, session.id, role, text, safe, 0, safe,
                                               {"scope": scope, "status": "completed"})
    return book, session.id


def compressor(monkeypatch, fail=False):
    calls = []
    def fake(db, provider, messages, timeout=30):
        calls.append(messages)
        rows = json.loads(messages[-1]["content"])
        content = [{"kind": "hypothesis", "content": "用户猜测林岚藏信，尚未确认；仍关注周砚行踪。", "sources": [rows[0]["id"]]}]
        return {"content": "invalid" if fail else json.dumps(content, ensure_ascii=False), "model": "synthetic"}
    monkeypatch.setattr(memory.agent_runtime, "call_model", fake)
    return calls


def test_segment_cache_keeps_originals_and_recent_pairs(monkeypatch):
    calls = compressor(monkeypatch)
    with SessionLocal() as db:
        _, session = seed(db)
        first = memory.assemble(db, session, 0, "reading", "林岚的信")
        second = memory.assemble(db, session, 0, "reading", "林岚的信")
        assert first["compression"] == "compressed" and len(calls) == 1
        assert len(second["history"]) == 12
        assert db.query(AgentMessage).filter_by(session_id=session).count() == 32
        assert db.query(ConversationDigest).count() == 1
        assert first["memory"] and second["memory"]


def test_failed_compression_does_not_advance_watermark(monkeypatch):
    calls = compressor(monkeypatch, fail=True)
    with SessionLocal() as db:
        _, session = seed(db)
        result = memory.assemble(db, session, 0, "reading", "信")
        assert result["compression"] == "failed" and len(result["history"]) == 12
        assert db.query(ConversationDigest).count() == 0
        assert db.query(AgentMessage).count() == 32


def test_correction_and_forgetting_suppress_derived_claim(monkeypatch):
    compressor(monkeypatch)
    with SessionLocal() as db:
        _, session = seed(db)
        first = memory.assemble(db, session, 0, "reading", "信")
        sources = first["memory"][0]["sources"]
        edited = memory.edit(db, session, 0, "reading", "correction", "我撤回藏信猜测", sources)
        corrected = memory.assemble(db, session, 0, "reading", "信")
        assert all(m["origin"] != "compressed" for m in corrected["memory"])
        memory.edit(db, session, 0, "reading", "correction", "忘记这条猜测", sources, edited.id, True)
        assert memory.assemble(db, session, 0, "reading", "信")["memory"] == []


def test_late_revision_not_visible_after_progress_rollback():
    with SessionLocal() as db:
        _, session = seed(db, turns=1)
        old = memory.edit(db, session, 0, "reading", "hypothesis", "我怀疑林岚藏信", [])
        memory.edit(db, session, 1, "reading", "correction", "后章撤回了猜测", [], old.id)
        assert [m.content for m in memory.memories(db, session, 0, "reading")] == [old.content]
        assert memory.memories(db, session, 1, "reading")[0].content == "后章撤回了猜测"


def test_book_scope_and_source_isolation(monkeypatch):
    compressor(monkeypatch)
    with SessionLocal() as db:
        book, normal = seed(db, turns=1)
        seed(db, turns=1, scope="spoilers", safe=1, book=book)
        _, other = seed(db, turns=1)
        reading = memory.messages(db, normal, 1, "reading")
        spoiler = memory.messages(db, normal, 1, "spoilers")
        assert len(reading) == len(spoiler) == 2
        assert not memory.messages(db, normal, 0, "spoilers")
        with pytest.raises(Exception):
            memory.edit(db, other, 1, "reading", "hypothesis", "不可跨书引用", [reading[0].id])
        with pytest.raises(Exception):
            memory.edit(db, normal, 1, "reading", "hypothesis", "不可跨模式引用", [spoiler[0].id])


def test_chat_request_replay_is_idempotent(monkeypatch):
    calls = _fake(monkeypatch, ['{"action":"answer","answer":"合成回复","citations":[]}'])
    with SessionLocal() as db:
        book = _book(db)
        first = agent_service.chat(db, book, "你好", 0, 0, request_key="synthetic-1")
        again = agent_service.chat(db, book, "你好", 0, 0, request_key="synthetic-1")
        assert first == again and len(calls) == 1
        assert db.query(AgentMessage).count() == 2


def test_spoiler_read_does_not_expand_mcp_or_reading_history(monkeypatch):
    calls = _fake(monkeypatch, [json.dumps({"action": "tool", "calls": [{"tool": "read_chapter", "args": {"chapter_number": 2}}]}),
                              '{"action":"answer","answer":"全书讨论","citations":[]}'])
    with SessionLocal() as db:
        book = _book(db)
        result = agent_service.chat(db, book, "允许剧透，查第二章", 0, 0, spoilers=True)
        assert result["tool_trace"][0]["ok"]
        session = result["session_id"]
        assert agent_context_service.list_messages(db, session, safe=1) == []
        assert len(agent_context_service.list_messages(db, session, safe=1, scope="spoilers")) == 2


def test_spoiler_cannot_write_notes(monkeypatch):
    _fake(monkeypatch, [json.dumps({"action": "tool", "calls": [{"tool": "note_upsert", "args": {}}]}),
                       '{"action":"answer","answer":"只读讨论","citations":[]}'])
    with SessionLocal() as db:
        result = agent_service.chat(db, _book(db), "讨论结局", 0, 0, spoilers=True)
        assert not result["tool_trace"][0]["ok"]


@pytest.mark.parametrize("turns", [40, 100])
def test_long_session_budget_and_provenance(monkeypatch, turns):
    compressor(monkeypatch)
    with SessionLocal() as db:
        _, session = seed(db, turns=turns)
        result = memory.assemble(db, session, 0, "reading", "周砚的行踪")
        assert result["diagnostics"]["memory_chars"] <= memory.MEMORY_CHARS
        assert result["diagnostics"]["recent_chars"] <= memory.RECENT_CHARS
        assert db.query(AgentMessage).count() == turns * 2
        assert len(result["history"]) % 2 == 0
