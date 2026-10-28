"""Synthesis cost controls and local recovery; synthetic sources/fake LLM only."""

import json

import pytest

from app.database import SessionLocal
from app.models.role_preparation import RolePreparationJob, RoleProfileVersion
from app.schemas.role_jobs import RoleBatchRun, RoleSynthesisRun, RoleSynthesisEdit
from app.services import role_batch_service as batches, role_preparation_job_service as jobs, role_synthesis_service as synthesis
from app.common.exceptions import BusinessException
from app.schemas.role_preparation import RoleTarget, RoleProfile, GroundedObservation, VerifiedQuote
from app.services.role_preparation_service import ModelRunner, _partition, _profile_refs, _message_size
from app.services import role_preparation_prompt as prompts
from test_role_jobs import provider, model
from test_role_batches import prepared, run, no_background


def test_stalled_later_layer_reuses_existing_work_and_finishes_with_compact_merge():
    target = RoleTarget(book_key="synthetic_merge", name="岑砚")
    observations = [GroundedObservation(
        observation_id=f"obs_{index:024x}", kind="identity", claim="合成档案中的阶段身份",
        attribution="narrator", knowledge="experienced", evidence=[VerifiedQuote(
            scene_id=f"s{index}", chapter_number=index + 1, source_hash="synthetic",
            quote="合成场景中的档案记载。" * 1400, char_start=0, char_end=15400)])
        for index in range(4)]
    calls = []
    def complete(messages, **options):
        payload = json.loads(messages[1]["content"])
        assert payload["evidence_columns"] == ["observation_id", "kind", "knowledge", "attribution", "chapters"]
        assert all(isinstance(row, list) for row in payload["evidence_catalog"])
        assert _message_size(messages) <= 60000
        calls.append(messages)
        return {"content": json.dumps({"character": "岑砚", "identity": [
            {"text": "合成档案核验员，曾任见习记录员", "evidence_ids": [payload["evidence_catalog"][0][0]]}]})}
    model = ModelRunner(complete, "synthetic", max_repairs=0)
    groups, budget = synthesis._groups(target, observations, {}, model, 32000)
    assert len(groups) == 4
    cache = {}
    profiles = []
    for group in groups:
        statement = {"text": "合成资料中的历史身份与当前职责。" * 30,
                     "evidence_ids": [group[0].observation_id]}
        profile = RoleProfile(character="岑砚", identity=[statement] * 6,
                              current_state=[statement] * 6, expression=[statement] * 8)
        cache[synthesis.cache_key(model, prompts.synthesis_messages(target, group, {}))] = profile.model_dump()
        profiles.append(profile)
    coverage = {"grouped_synthesis": True, "synthesis_group_count": 4}
    def merge_input(group):
        refs = set().union(*(_profile_refs(p) for p in group))
        allowed = [o for o in observations if o.observation_id in refs]
        return prompts.merge_messages(target, group, allowed, coverage), allowed
    packets = _partition(profiles, lambda g: merge_input(g)[0], budget)
    assert 1 < len(packets) < len(profiles)
    for packet in packets:
        if len(packet) == 1:
            continue
        messages, allowed = merge_input(packet)
        statement = {"text": "综合后的合成身份、关系与状态变化。" * 30,
                     "evidence_ids": [allowed[0].observation_id]}
        profile = RoleProfile(character="岑砚", identity=[statement] * 6, current_state=[statement] * 6,
                              expression=[statement] * 8, relationships=[statement] * 10)
        cache[synthesis.cache_key(model, messages)] = profile.model_dump()
    before = json.dumps(cache, sort_keys=True)
    progress = []
    result = synthesis.synthesize(target, observations, {}, model, profile_cache=cache,
                                  group_chars=32000, on_progress=progress.append)
    assert len(calls) == 1 and result.identity[0].evidence_ids[0].startswith("obs_")
    assert all(cache[key] == value for key, value in json.loads(before).items())
    assert any(s.get("compact") and s.get("level") == 2 for s in progress)
    assert progress[-1]["status"] == "done"
    again = synthesis.synthesize(target, observations, {}, model, profile_cache=cache, group_chars=32000)
    assert again == result and len(calls) == 1


def extracted(db, long=False):
    book_id, job = prepared(db, long=long)
    return book_id, batches.extract(db, book_id, job.id, RoleBatchRun(stop_at=job.plan["batch_count"]))


def test_preview_is_free_and_each_run_sends_at_most_one_new_request(provider, model, monkeypatch):
    monkeypatch.setattr(synthesis, "GROUP_CHARS", 6500)
    requests = []
    def record(provider, messages, **options):
        payload = json.loads(messages[1]["content"])
        phase = "merge" if "profiles" in payload else "group" if "observations" in payload else "extract"
        requests.append((phase, options["max_tokens"]))
        return model(provider, messages, **options)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", record)
    with SessionLocal() as db:
        book_id, job = extracted(db, long=True)
    stopped = run(job)
    baseline = len(model.messages)
    with SessionLocal() as db:
        plan = batches.synthesis_preview(db, book_id, job.id)
        assert plan["group_count"] > 1 and all(g["input_chars"] <= 6500 for g in plan["groups"])
        assert not plan["model_called"] and len(model.messages) == baseline
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun(preview_key=plan["preview_key"]))
    stopped = run(job)
    assert stopped.status == "ready" and len(model.messages) == baseline + 1
    assert len(stopped.plan["synthesis_cache"]) == 1 and stopped.plan["synthesis_state"]["group_completed"] == 1
    for _ in range(plan["group_count"] * 3 + 10):
        before = len(model.messages)
        with SessionLocal() as db:
            next_plan = batches.synthesis_preview(db, book_id, job.id)
            assert next_plan["cached_groups"] >= 1
            job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun(preview_key=next_plan["preview_key"]))
        stopped = run(job)
        assert len(model.messages) - before <= 1
        if stopped.status == "done":
            break
        assert stopped.status == "ready"
    assert stopped.status == "done" and stopped.plan["synthesis_state"]["status"] == "done"
    assert {phase for phase, _ in requests} == {"extract", "group", "merge"}
    assert all(tokens == (4096 if phase == "extract" else 16384) for phase, tokens in requests)
    with SessionLocal() as db:
        assert db.query(RoleProfileVersion).count() == 1


def test_bad_schema_keeps_raw_and_hint_and_local_repair_never_calls_model(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id, job = extracted(db)
    run(job)
    def invalid(provider, messages, **options):
        data = json.loads(messages[1]["content"])
        item = {"text": "合成身份", "evidence_ids": [data["observations"][0][0]]}
        return {"content": json.dumps({"character":data["target"]["name"], "identity":[item]*7})}
    monkeypatch.setattr(jobs.llm_service, "chat_completion", invalid)
    with SessionLocal() as db:
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun())
    stopped = run(job)
    assert stopped.error == "invalid_model_schema" and stopped.plan["synthesis_state"]["status"] == "failed"
    with SessionLocal() as db:
        raw = batches.synthesis_response(db, book_id, job.id)
        assert raw["available"] and "identity: too_long" in raw["hint"]
        assert not jobs.job_to_dict(db.get(RolePreparationJob, job.id)).get("raw_content")
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun())
    assert run(job).calls == stopped.calls  # Cached invalid response is checked locally.
    with SessionLocal() as db:
        content = json.loads(raw["raw_content"]); content["identity"] = content["identity"][:1]
        edit = RoleSynthesisEdit(cache_key=raw["cache_key"], revision=raw["revision"], raw_content=json.dumps(content))
        result = batches.edit_synthesis_response(db, book_id, job.id, edit)
        assert result["error"] is None
        with pytest.raises(BusinessException, match="变化"):
            batches.edit_synthesis_response(db, book_id, job.id, edit)
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun())
    finished = run(job)
    assert finished.status == "done" and finished.calls == stopped.calls


def test_stale_preview_cannot_start_a_different_model(provider, model):
    with SessionLocal() as db:
        book_id, job = extracted(db)
    run(job)
    with SessionLocal() as db:
        plan = batches.synthesis_preview(db, book_id, job.id)
        provider["model"] = "synthetic-other"
        with pytest.raises(BusinessException, match="重新预览"):
            batches.synthesize(db, book_id, job.id, RoleSynthesisRun(preview_key=plan["preview_key"]))


def test_truncated_response_is_preserved_before_model_runner_rejects_it(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id, job = extracted(db)
    run(job)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", lambda *a,**k:
                        {"content":'{"character":', "finish_reason":"length"})
    with SessionLocal() as db:
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun())
    assert run(job).error == "model_output_truncated"
    with SessionLocal() as db:
        result = batches.synthesis_response(db, book_id, job.id)
        assert result["raw_content"] == '{"character":' and result["error"] == "model_output_truncated"
        calls = db.get(RolePreparationJob, job.id).calls
        with pytest.raises(BusinessException, match="model_output_truncated.*输出达到上限"):
            batches.edit_synthesis_response(db, book_id, job.id, RoleSynthesisEdit(
                cache_key=result["cache_key"], revision=result["revision"], raw_content=result["raw_content"]))
        assert db.get(RolePreparationJob, job.id).calls == calls


def test_synthesis_endpoints_preview_without_requests_and_reject_wrong_book(client, provider, model):
    with SessionLocal() as db:
        book_id, job = extracted(db)
    run(job); calls = len(model.messages)
    base = f"/api/books/{book_id}/role-chat/preparation/jobs/{job.id}/synthesis"
    data = client.get(base + "/preview").json()["data"]
    assert data["group_count"] == 1 and len(model.messages) == calls
    assert client.get(base + "/response").json()["data"] == {"available":False}
    assert client.get(base.replace(f"books/{book_id}","books/999") + "/preview").json()["code"] == 4040


def test_switch_model_keeps_completed_group_and_requests_the_next_one(provider, model, monkeypatch):
    monkeypatch.setattr(synthesis, "GROUP_CHARS", 6500)
    with SessionLocal() as db:
        book_id, job = extracted(db, long=True)
    run(job)
    first_model = provider["model"]
    with SessionLocal() as db:
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun())
    stopped = run(job)
    assert stopped.status == "ready" and stopped.plan["synthesis_state"]["group_completed"] == 1
    first_key, first_profile = next(iter(stopped.plan["synthesis_cache"].items()))
    before = len(model.messages)
    provider.update(provider_id=202, model="synthetic-second-provider")
    with SessionLocal() as db:
        plan = batches.synthesis_preview(db, book_id, job.id)
        assert plan["cached_groups"] == 1 and plan["groups"][0]["origin"]["model"] == first_model
        assert len(model.messages) == before
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun(preview_key=plan["preview_key"]))
    stopped = run(job)
    assert len(model.messages) == before + 1
    assert stopped.plan["synthesis_state"]["group_completed"] == 2
    assert stopped.plan["synthesis_state"]["current"] == 3
    assert stopped.plan["synthesis_cache"][first_key] == first_profile
    assert {o["model"] for o in stopped.plan["synthesis_origins"].values()} == {first_model, provider["model"]}


def test_switch_model_does_not_silently_resend_invalid_response_and_explicit_retry_records_new_origin(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id, job = extracted(db)
    run(job)
    def invalid(provider, messages, **options):
        data = json.loads(messages[1]["content"])
        return {"content":json.dumps({"character":data["target"]["name"], "identity":[
            {"text":"合成身份", "evidence_ids":[data["observations"][0][0]]}]*7})}
    monkeypatch.setattr(jobs.llm_service, "chat_completion", invalid)
    with SessionLocal() as db:
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun())
    stopped = run(job); calls = stopped.calls
    provider.update(provider_id=303, model="synthetic-new-model")
    monkeypatch.setattr(jobs.llm_service, "chat_completion", model)
    with SessionLocal() as db:
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun())
    assert run(job).calls == calls
    with SessionLocal() as db:
        job = batches.synthesize(db, book_id, job.id, RoleSynthesisRun(retry_failed=True))
    stopped = run(job)
    assert stopped.status == "done" and stopped.calls == calls + 1
    assert list(stopped.plan["synthesis_origins"].values())[0]["model"] == provider["model"]
