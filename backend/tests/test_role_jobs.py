"""Persistent role preparation, using only synthetic novels and fake model calls."""

import json
import os
import threading
import time
import asyncio
import httpx

import pytest
from sqlalchemy.exc import IntegrityError

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.reader_progress import ReaderProgress
from app.models.role_preparation import (
    RoleCharacter, RoleExtractionCache, RolePreparationJob, RoleProfileVersion,
)
from app.schemas.role_jobs import RoleJobCreate, RoleJobResume
from app.services import role_book_service as sources
from app.services import role_preparation_job_service as jobs


@pytest.fixture
def provider(monkeypatch):
    settings = {"provider_id": 101, "provider_name": "合成测试模型", "model": "synthetic-model",
                "base_url": "http://127.0.0.1:1/v1", "disable_thinking": True}
    monkeypatch.setattr(jobs.llm_provider_service, "get_active_provider_config", lambda *a, **k: dict(settings))
    return settings


def create_book(db, *, name="岑砚", long=False):
    tail = "\n".join(f"卷宗{i}记录第{i}件合成物品的归档位置，均与真实书籍无关。" for i in range(40)) if long else ""
    bodies = [f"{name}是一名核验员，他说：先核对凭据，再判断事情。\n{tail}",
              f"后章独有：{name}听说密信已经送达，但没有看见藏处。\n{tail}",
              f"番外独有：{name}在平行设定中忘掉了全部经历。"]
    parts, entries, cursor = [], [], 0
    for index, body in enumerate(bodies):
        title = f"第{index + 1}章 合成准备场景"
        text = title + "\n" + body + "\n"
        entries.append((index, title, "extra" if index == 2 else "main", cursor, cursor + len(text)))
        parts.append(text)
        cursor += len(text)
    source = "".join(parts)
    book = Book(title="角色准备合成书", filename="synthetic-role.txt", encoding="UTF-8",
                protagonists=[], char_count=len(source), chapter_count=3, main_chapter_count=2)
    db.add(book)
    db.flush()
    for index, title, kind, start, end in entries:
        db.add(Chapter(book_id=book.id, idx=index, title=title, kind=kind,
                       char_start=start, char_end=end, word_count=end - start))
    db.add(ReaderProgress(book_id=book.id, chapter_idx=0, char_offset=0))
    db.commit()
    directory = UPLOADS_DIR / str(book.id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "source.txt").write_text(source, encoding="utf-8")
    return book.id


class FakeModel:
    def __init__(self):
        self.messages = []
        self.on_call = None
        self.fail = False
        self.long_claims = False

    def __call__(self, provider, messages, **kwargs):
        self.messages.append(messages)
        if self.on_call:
            self.on_call(len(self.messages))
        if self.fail:
            raise RuntimeError("private_provider_value_must_not_escape")
        data = json.loads(messages[1]["content"])
        if "paragraphs" in data:
            result = {"character": data["target"]["name"], "observations": [{
                "kind": "action", "claim": "待核对的合成行为" * 55 if self.long_claims else "本场景的人物观测，仍需语义评审", "attribution": "narrator",
                "knowledge": "unknown", "evidence": [{"paragraph_id": paragraph["paragraph_id"]}],
            } for paragraph in data["paragraphs"][:12]]}
        elif "scenes" in data:
            result = {"character": data["target"]["name"], "observations": [{
                "kind": "action", "claim": "待核对的合成行为" * 55 if self.long_claims else "本场景的人物观测，仍需语义评审", "attribution": "narrator",
                "knowledge": "unknown", "evidence": [{"scene_id": scene["scene_id"],
                                                           "quote": scene["text"][:40]}],
            } for scene in data["scenes"]]}
        else:
            observations = data.get("observations", data.get("evidence_catalog"))
            reference = observations[0][0] if isinstance(observations[0], list) else observations[0]["observation_id"]
            result = {"character": data["target"]["name"], "identity": [{
                "text": "合成档案核验员", "evidence_ids": [reference]}],
                "limitations": ["真实语义与末态尚需核验"]}
        return {"content": json.dumps(result, ensure_ascii=False)}


@pytest.fixture
def model(monkeypatch):
    fake = FakeModel()
    monkeypatch.setattr(jobs.llm_service, "chat_completion", fake)
    return fake


def run(job):
    jobs.run_job(job.id, job.run_token)
    with SessionLocal() as db:
        return db.get(RolePreparationJob, job.id)


def resume_without_thread(db, book_id, job_id, monkeypatch, **kwargs):
    monkeypatch.setattr(jobs, "launch", lambda *a: None)
    return jobs.resume_job(db, book_id, job_id, RoleJobResume(**kwargs))


def test_preview_is_read_only_full_book_and_excludes_extras(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
        result = jobs.preview(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        assert result["scope"] == "full_main_text" and not result["extras_included"]
        assert result["selected_scenes"] == 2
        assert result["model_called"] is False
        assert db.query(RolePreparationJob).count() == 0
        assert db.query(RoleCharacter).count() == 0
        scenes, _ = sources.load_scenes(db, book_id)
        assert "后章独有" in "".join(s.text for s in scenes)
        assert "番外独有" not in "".join(s.text for s in scenes)
        assert db.query(ReaderProgress).one().chapter_idx == 0
    assert model.messages == []


def test_role_requests_allow_same_timeout_as_summary_without_auto_retry(provider, monkeypatch):
    calls = []
    def timeout(provider, messages, **options):
        calls.append(options)
        return {"content": None, "error": "synthetic timeout", "error_code": "timeout"}
    monkeypatch.setattr(jobs.llm_service, "chat_completion", timeout)
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=0))
    stopped = run(job)
    assert stopped.status == "failed" and stopped.error == "model_request_timeout"
    assert stopped.calls == 1 and stopped.completed_batches == 0
    assert len(calls) == 1 and calls[0]["timeout"] == 300


@pytest.mark.disk_db
def test_pause_cancels_an_inflight_http_request_and_keeps_earlier_batches(client, provider, monkeypatch):
    entered, cancelled = threading.Event(), threading.Event()
    fake = FakeModel()
    requests = []
    async def respond(request):
        requests.append(request)
        if len(requests) == 1:
            result = fake(provider, json.loads(request.content)["messages"])
            return httpx.Response(200, json={"choices": [{"message": {"content": result["content"]}}],
                                            "usage": {"prompt_tokens": 80, "completion_tokens": 20, "total_tokens": 100}})
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
    root = f"/api/books/{book_id}/role-chat/preparation/jobs"
    created = client.post(root, json={"name": "岑砚", "batch_chars": 512,
                                      "request_interval_seconds": 0}).json()["data"]
    assert entered.wait(15)
    started = time.monotonic()
    client.post(root + f"/{created['id']}/pause")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = client.get(root + f"/{created['id']}").json()["data"]
        if current["status"] == "paused":
            break
        time.sleep(.01)
    assert current["status"] == "paused" and time.monotonic() - started < 3
    assert cancelled.wait(1) and len(requests) == 2
    assert current["completed_batches"] == 1 and current["calls"] == 2
    assert current["request_pending"] is False
    with SessionLocal() as db:
        job = db.get(RolePreparationJob, created["id"])
        assert job.metrics[0]["total_tokens"] == 100
        assert job.metrics[1]["cancelled"] is True
        assert db.query(RoleExtractionCache).count() == 1
        assert db.query(RoleProfileVersion).count() == 0


@pytest.mark.parametrize("status", ["running", "pausing"])
def test_expired_request_recovers_slot_and_rejects_late_results(provider, monkeypatch, status):
    clock = VirtualClock()
    monkeypatch.setattr(jobs, "time", clock)
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=0))
        job.status, job.calls = status, 1
        # Exercise legacy compatibility: a reservation with no completion metric.
        job.plan = {**job.plan, "last_call_at": clock.now}
        db.commit()
        old_token = job.run_token
        clock.now += jobs.REQUEST_TIMEOUT_SECONDS + jobs.RECOVERY_GRACE_SECONDS + 1
        recovered = jobs.get_job(db, book_id, job.id)
        assert recovered.status == ("paused" if status == "pausing" else "interrupted")
        assert recovered.active_slot is None and recovered.run_token != old_token
        assert recovered.calls == 1 and not jobs.job_to_dict(recovered)["request_pending"]
    jobs._finish(job.id, old_token, "failed", "obsolete_result")
    with SessionLocal() as db:
        assert db.get(RolePreparationJob, job.id).error != "obsolete_result"
        assert jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=0)).id != job.id


def test_character_suggestions_rank_material_before_pagination_and_deduplicate():
    with SessionLocal() as db:
        book_id = create_book(db)
        other_id = create_book(db)
        db.add_all([
            Character(book_id=book_id, canonical_name="甲舟", mention_count=1),
            Character(book_id=book_id, canonical_name="祝遥", mention_count=9),
            Character(book_id=book_id, canonical_name="岑砚", mention_count=9),
            Character(book_id=other_id, canonical_name="外书人物", mention_count=100),
            RoleCharacter(book_id=book_id, canonical_name="岑砚", role_key="synthetic_rank_1"),
            RoleCharacter(book_id=book_id, canonical_name="新人物", role_key="synthetic_rank_2"),
        ])
        db.commit()
        result = sources.characters(db, book_id, limit=1)
        assert result["total"] == 4
        assert result["items"] == [{"name": "岑砚", "mention_count": 9}]
        assert sources.characters(db, book_id, offset=1, limit=1)["items"][0]["name"] == "祝遥"
        assert [r["name"] for r in sources.characters(db, book_id)["items"]] == ["岑砚", "祝遥", "甲舟", "新人物"]
        assert sources.characters(db, book_id, query="祝")["items"] == [{"name": "祝遥", "mention_count": 9}]


class VirtualClock:
    def __init__(self):
        self.now = 1000.0
        self.on_sleep = None

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep()


def test_interval_applies_to_extraction_repair_and_synthesis(provider, model, monkeypatch):
    clock, starts = VirtualClock(), []
    monkeypatch.setattr(jobs, "time", clock)
    def complete(provider, messages, **kwargs):
        starts.append(clock.time())
        if len(starts) == 1:
            return {"content": "invalid synthetic JSON"}
        return model(provider, messages, **kwargs)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", complete)
    with SessionLocal() as db:
        book_id = create_book(db)
        assert jobs.preview(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=60))["request_interval_seconds"] == 60
        job = jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=60))
    assert run(job).status == "done"
    assert starts == [1000, 1060, 1120]


def test_pause_during_interval_does_not_spend_call_budget(provider, model, monkeypatch):
    clock = VirtualClock()
    monkeypatch.setattr(jobs, "time", clock)
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=60))
    def pause():
        with SessionLocal() as db:
            jobs.request_pause(db, book_id, job.id)
    clock.on_sleep = pause
    stopped = run(job)
    assert stopped.status == "paused" and stopped.calls == 1
    assert stopped.completed_batches == 1
    assert clock.now == 1000.5


def test_429_pauses_once_and_resume_keeps_provider_cooldown_and_cache(provider, model, monkeypatch):
    clock, starts = VirtualClock(), []
    monkeypatch.setattr(jobs, "time", clock)
    def complete(provider, messages, **kwargs):
        starts.append(clock.time())
        if len(starts) == 2:
            return {"status_code": 429, "error": "private-provider-quota-detail",
                    "retry_after_seconds": 180}
        return model(provider, messages, **kwargs)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", complete)
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
        job = jobs.create_job(db, book_id, RoleJobCreate(
            name="岑砚", batch_chars=512, request_interval_seconds=60))
    stopped = run(job)
    assert stopped.status == "paused" and stopped.error == "model_rate_limited"
    assert stopped.calls == 2 and stopped.completed_batches == 1
    with SessionLocal() as db:
        state = jobs.job_to_dict(db.get(RolePreparationJob, job.id))
        assert state["wait_seconds"] == 180 and state["resumable"]
        assert "private-provider" not in json.dumps(stopped.metrics)
        assert db.query(RoleExtractionCache).count() == 1
        resumed = resume_without_thread(db, book_id, job.id, monkeypatch,
                                       request_interval_seconds=120)
    assert run(resumed).status == "done"
    assert starts[:3] == [1000, 1060, 1240]
    assert all(b - a >= 120 for a, b in zip(starts[2:], starts[3:]))
    with SessionLocal() as db:
        final = db.get(RolePreparationJob, job.id)
        assert final.cache_hits == 1
        assert final.calls == len(starts)
        assert final.plan["request_interval_seconds"] == 120


def test_new_job_cannot_bypass_same_provider_429_cooldown(provider, model, monkeypatch):
    clock, starts = VirtualClock(), []
    monkeypatch.setattr(jobs, "time", clock)
    def complete(provider, messages, **kwargs):
        starts.append(clock.time())
        if len(starts) == 1:
            return {"status_code": 429, "error": "synthetic limit", "retry_after_seconds": 180}
        return model(provider, messages, **kwargs)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", complete)
    with SessionLocal() as db:
        book_id = create_book(db)
        first = jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=0))
    assert run(first).status == "paused"
    with SessionLocal() as db:
        second = jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=0))
    assert run(second).status == "done"
    assert starts == [1000, 1180, 1180]


def test_long_chapters_have_contiguous_coverage_and_global_offsets():
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
        scenes, _ = sources.load_scenes(db, book_id, batch_chars=512)
        raw, chapters, _ = sources.read_source(db, book_id)
        for scene in scenes:
            assert raw[scene.char_start:scene.char_start + len(scene.text)] == scene.text
            assert len(scene.text) <= 512
        for chapter in chapters[:2]:
            selected = [s for s in scenes if s.chapter_number == chapter.idx + 1]
            assert len(selected) > 1
            assert all(b.char_start <= a.char_start + len(a.text) for a, b in zip(selected, selected[1:]))
            assert selected[-1].char_start + len(selected[-1].text) == chapter.char_end


def test_unambiguous_alias_resolves_and_ambiguous_alias_is_not_used():
    with SessionLocal() as db:
        book_id = create_book(db)
        a = Character(book_id=book_id, canonical_name="岑砚")
        b = Character(book_id=book_id, canonical_name="祝遥")
        db.add_all([a, b]); db.flush()
        db.add_all([
            CharacterAlias(book_id=book_id, character_id=a.id, alias="岑先生", source="mention"),
            CharacterAlias(book_id=book_id, character_id=a.id, alias="先生", source="mention"),
            CharacterAlias(book_id=book_id, character_id=b.id, alias="先生", source="mention"),
        ])
        db.commit()
        target = sources.resolve_target(db, book_id, "岑先生")
        assert target.name == "岑砚" and target.aliases == ["岑先生"]
        with pytest.raises(BusinessException, match="多个人物"):
            sources.resolve_target(db, book_id, "先生")


def test_done_job_persists_profile_and_does_not_change_reading_data(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        role_id = job.role_id
    completed = run(job)
    assert completed.status == "done" and completed.active_slot is None
    assert completed.calls == 2
    with SessionLocal() as db:
        result = jobs.latest_profile(db, book_id, role_id)
        assert result["version"] == 1 and result["stale"] is False
        assert result["review_status"] == "semantic_review_required"
        assert db.query(RoleExtractionCache).filter_by(kind="extraction").count() == 1
        assert db.query(RoleExtractionCache).filter_by(kind="profile").count() == 1
        assert db.query(ReaderProgress).one().chapter_idx == 0
        assert db.query(Character).count() == 0  # A manual name needs no entity index.
        page = jobs.profile_evidence(db, book_id, result["profile_id"], limit=1)
        assert len(page["items"]) == 1 and page["total"] == 2
        assert page["items"][0]["evidence"][0]["quote_verified"]
        all_items = db.query(RoleProfileVersion).one().observations
        last_id = all_items[-1]["observation_id"]
        exact = jobs.profile_evidence(db, book_id, result["profile_id"], observation_id=last_id)
        assert [item["observation_id"] for item in exact["items"]] == [last_id]
        assert exact["total"] == 1
        assert jobs.profile_evidence(db, book_id, result["profile_id"], observation_id="unknown")["total"] == 0
    sent = json.dumps(model.messages, ensure_ascii=False)
    assert "后章独有" in sent and "番外独有" not in sent


def test_new_version_reuses_extraction_cache_and_stable_role(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
        first = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
    assert run(first).status == "done"
    with SessionLocal() as db:
        second = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        assert second.role_id == first.role_id
    assert run(second).calls == 0
    with SessionLocal() as db:
        assert jobs.latest_profile(db, book_id, first.role_id)["version"] == 2
        assert db.query(RoleProfileVersion).count() == 2


def test_group_synthesis_checkpoint_is_durable_across_resume(provider, model, monkeypatch):
    original_runner = jobs._runner
    monkeypatch.setattr(jobs, "_runner", lambda provider, **options: original_runner(
        provider, max_request_chars=8000, **options))
    model.long_claims = True
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚", mode="full", batch_chars=512))
    stopped = []
    def pause_first_synthesis(count):
        packet = json.loads(model.messages[-1][1]["content"])
        if "observations" in packet and not stopped:
            stopped.append(model.messages[-1])
            with SessionLocal() as db:
                jobs.request_pause(db, book_id, job.id)
    model.on_call = pause_first_synthesis
    paused = run(job)
    assert paused.status == "paused" and paused.stage == "synthesize"
    assert paused.draft_profile is None  # A partial group is not a finished role.
    with SessionLocal() as db:
        assert db.query(RoleExtractionCache).filter_by(kind="profile").count() == 1
        resumed = resume_without_thread(db, book_id, job.id, monkeypatch)
    before = len(model.messages)
    model.on_call = None
    assert run(resumed).status == "done"
    assert stopped[0] not in model.messages[before:]
    with SessionLocal() as db:
        assert db.query(RoleProfileVersion).one().coverage["grouped_synthesis"]


def test_pause_after_current_batch_and_resume_without_repeating_it(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚", batch_chars=512, mode="full"))
    def pause_first(count):
        if count == 1:
            with SessionLocal() as db:
                jobs.request_pause(db, book_id, job.id)
    model.on_call = pause_first
    paused = run(job)
    assert paused.status == "paused" and paused.completed_batches == 1
    assert paused.calls == 1
    model.on_call = None
    with SessionLocal() as db:
        resumed = resume_without_thread(db, book_id, job.id, monkeypatch)
    finished = run(resumed)
    assert finished.status == "done"
    assert finished.cache_hits == 1
    assert finished.calls == finished.plan["batch_count"] + 1


def test_pause_during_synthesis_preserves_draft_for_free_resume(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
    def pause_synthesis(count):
        if count == 2:
            with SessionLocal() as db:
                jobs.request_pause(db, book_id, job.id)
    model.on_call = pause_synthesis
    paused = run(job)
    assert paused.status == "paused" and paused.draft_profile
    model.on_call = None
    with SessionLocal() as db:
        resumed = resume_without_thread(db, book_id, job.id, monkeypatch)
    completed = run(resumed)
    assert completed.status == "done" and completed.calls == 2


@pytest.mark.disk_db
def test_interruption_survives_reopening_database_and_old_worker_cannot_publish(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        old_token = job.run_token
    def interrupt(count):
        with SessionLocal() as db:
            jobs.mark_interrupted_jobs(db)
    model.on_call = interrupt
    assert run(job).status == "interrupted"
    with SessionLocal() as db:
        persisted = db.get(RolePreparationJob, job.id)
        assert persisted.calls == 1 and persisted.run_token != old_token
        assert db.query(RoleProfileVersion).count() == 0
        jobs.run_job(job.id, old_token)
        assert db.query(RoleProfileVersion).count() == 0
        model.on_call = None
        resumed = resume_without_thread(db, book_id, job.id, monkeypatch)
    assert run(resumed).status == "done"


def test_source_change_with_preserved_mtime_rejects_resume_and_marks_profile_stale(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id = create_book(db)
        finished = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
    assert run(finished).status == "done"
    with SessionLocal() as db:
        pending = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        jobs.request_pause(db, book_id, pending.id)
    path = UPLOADS_DIR / str(book_id) / "source.txt"
    stamp = path.stat()
    text = path.read_text(encoding="utf-8").replace("密信", "密函")
    path.write_text(text, encoding="utf-8")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    with SessionLocal() as db:
        with pytest.raises(BusinessException, match="source_changed"):
            resume_without_thread(db, book_id, pending.id, monkeypatch)
        assert jobs.latest_profile(db, book_id, finished.role_id)["stale"]


def test_source_changed_while_request_inflight_never_publishes_profile(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
    def change_source(count):
        path = UPLOADS_DIR / str(book_id) / "source.txt"
        path.write_text(path.read_text(encoding="utf-8").replace("密信", "密函"), encoding="utf-8")
    model.on_call = change_source
    failed = run(job)
    assert failed.status == "failed" and failed.error == "source_changed"
    with SessionLocal() as db:
        assert db.query(RoleProfileVersion).count() == 0


def test_provider_change_does_not_mix_results(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        jobs.request_pause(db, book_id, job.id)
        provider["model"] = "different-synthetic-model"
        with pytest.raises(BusinessException, match="model_changed"):
            resume_without_thread(db, book_id, job.id, monkeypatch)
    assert model.messages == []


def test_changing_provider_between_batches_stops_before_next_request(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚", mode="full", batch_chars=512))
    model.on_call = lambda count: provider.update(model="changed-synthetic-model")
    failed = run(job)
    assert failed.status == "failed" and failed.error == "model_changed"
    assert failed.calls == 1 and len(model.messages) == 1
    with SessionLocal() as db:
        assert db.query(RoleProfileVersion).count() == 0


def test_alias_changes_invalidate_paused_snapshot(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id = create_book(db)
        character = Character(book_id=book_id, canonical_name="岑砚")
        db.add(character); db.commit()
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        jobs.request_pause(db, book_id, job.id)
        db.add(CharacterAlias(book_id=book_id, character_id=character.id, alias="岑先生", source="mention"))
        db.commit()
        with pytest.raises(BusinessException, match="character_identity_changed"):
            resume_without_thread(db, book_id, job.id, monkeypatch)
    assert model.messages == []


def test_failed_call_budget_is_persistent_and_diagnostics_are_redacted(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚", max_calls=2))
    model.fail = True
    failed = run(job)
    assert failed.calls == 1 and failed.error == "model_request_failed"
    model.fail = False
    with SessionLocal() as db:
        resumed = resume_without_thread(db, book_id, job.id, monkeypatch, max_calls=1)
    no_budget = run(resumed)
    assert no_budget.calls == 1 and no_budget.error == "model_budget_exhausted"
    assert len(model.messages) == 1
    with SessionLocal() as db:
        resumed = resume_without_thread(db, book_id, job.id, monkeypatch, max_calls=4)
    assert run(resumed).status == "done"
    with SessionLocal() as db:
        public = jobs.job_to_dict(db.get(RolePreparationJob, job.id))
        assert "private_provider_value" not in json.dumps(public)


def test_budget_preflight_and_global_active_slot(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
        with pytest.raises(BusinessException, match="预算不足"):
            jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚", mode="full", batch_chars=512, max_calls=1))
        assert db.query(RoleCharacter).count() == 0
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        with pytest.raises(BusinessException, match="已有角色"):
            jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        jobs.request_pause(db, book_id, job.id)
        another = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        assert another.id != job.id
    assert model.messages == []


def test_database_enforces_single_active_job_without_process_lock(provider):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
        db.add(RolePreparationJob(
            book_id=book_id, role_id=job.role_id, status="queued", active_slot=1,
            run_token="another_process", source_hash=job.source_hash,
            provider_id=101, model_hash=job.model_hash, prompt_version=job.prompt_version,
            plan=job.plan, max_calls=64,
        ))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.get(RolePreparationJob, job.id).status == "queued"


def test_entity_rebuild_does_not_delete_prepared_role(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
        db.add(Character(book_id=book_id, canonical_name="岑砚")); db.commit()
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
    assert run(job).status == "done"
    with SessionLocal() as db:
        key = db.get(RoleCharacter, job.role_id).role_key
        db.query(Character).filter_by(book_id=book_id).delete(); db.commit()
        db.add(Character(book_id=book_id, canonical_name="岑砚")); db.commit()
        assert db.get(RoleCharacter, job.role_id).role_key == key
        assert jobs.latest_profile(db, book_id, job.role_id)["stale"] is False


def test_cross_book_access_and_book_deletion_cascade(provider, model):
    with SessionLocal() as db:
        first = create_book(db)
        second = create_book(db)
        job = jobs.create_job(db, first, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
    assert run(job).status == "done"
    with SessionLocal() as db:
        version = db.query(RoleProfileVersion).one()
        for read in (lambda: jobs.get_job(db, second, job.id),
                     lambda: jobs.latest_profile(db, second, job.role_id),
                     lambda: jobs.profile_evidence(db, second, version.id)):
            with pytest.raises(BusinessException) as caught:
                read()
            assert caught.value.code == 4040
        db.delete(db.get(Book, first)); db.commit()
        assert db.query(RoleCharacter).count() == 0
        assert db.query(RoleExtractionCache).count() == 0
        assert db.query(RoleProfileVersion).count() == 0
        assert db.query(RolePreparationJob).count() == 0


def test_api_is_independent_of_reader_gate_and_progress_stays_unchanged(client, provider, model, monkeypatch):
    monkeypatch.setattr(jobs, "launch", lambda *a: None)
    with SessionLocal() as db:
        book_id = create_book(db)
    root = f"/api/books/{book_id}/role-chat"
    preview = client.post(root + "/preparation/preview", json={"name": "岑砚", "request_interval_seconds": 0}).json()
    assert preview["code"] == 200 and preview["data"]["model_called"] is False
    created = client.post(root + "/preparation/jobs", json={"name": "岑砚", "request_interval_seconds": 0}).json()["data"]
    with SessionLocal() as db:
        queued = db.get(RolePreparationJob, created["id"])
    assert run(queued).status == "done"
    result = client.get(root + f"/roles/{created['role_id']}/profile").json()["data"]
    assert result["available"]
    listed_roles = client.get(root + "/roles").json()["data"]
    assert listed_roles["items"][0]["role_id"] == created["role_id"]
    assert "observations" not in listed_roles["items"][0]
    assert client.get(root + "/preparation/jobs").json()["data"]["items"][0]["status"] == "done"
    assert client.get(root + "/characters?query=岑").json()["data"]["items"] == [{"name": "岑砚", "mention_count": 0}]
    blocked = client.post(f"/api/books/{book_id}/agent/model-tool", json={
        "tool": "read_chapter", "args": {"chapter_number": 2}}).json()["data"]
    assert blocked["ok"] is False
    progress = client.get(f"/api/books/{book_id}/progress").json()["data"]
    assert progress["chapter_idx"] == 0
    invalid = client.post(root + "/preparation/jobs", json={"name": "岑砚", "safe_chapter": 999}).json()
    assert invalid["code"] != 200


def test_pause_cannot_overwrite_a_completed_job(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(request_interval_seconds=0, name="岑砚"))
    assert run(job).status == "done"
    with SessionLocal() as db:
        assert jobs.request_pause(db, book_id, job.id).status == "done"


@pytest.mark.disk_db
def test_real_background_thread_pauses_and_resumes_via_api(client, provider, model):
    entered, release = threading.Event(), threading.Event()
    def wait_first(count):
        if count == 1:
            entered.set()
            assert release.wait(15)
    model.on_call = wait_first
    with SessionLocal() as db:
        book_id = create_book(db)
    root = f"/api/books/{book_id}/role-chat/preparation/jobs"
    try:
        response = client.post(root, json={"name": "岑砚", "request_interval_seconds": 0}).json()
        assert response["code"] == 200
        job_id = response["data"]["id"]
        assert entered.wait(15)
        paused = client.post(root + f"/{job_id}/pause").json()["data"]
        assert paused["status"] == "pausing"
    finally:
        release.set()
    def wait_status(expected):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = client.get(root + f"/{job_id}").json()["data"]
            if value["status"] == expected:
                return value
            time.sleep(.01)
        pytest.fail(f"role preparation did not reach {expected}")
    assert wait_status("paused")["completed_batches"] == 1
    assert client.post(root + f"/{job_id}/resume", json={}).json()["code"] == 200
    assert wait_status("done")["calls"] == 2
