"""Staged role preparation: synthetic text and local fake models only."""

import json

import pytest

from app.database import SessionLocal
from app.models.role_preparation import RolePreparationJob, RolePreparationBatch, RoleBatchRevision, RoleProfileVersion
from app.schemas.role_jobs import RoleJobCreate, RoleBatchRun, RoleJobResume, RoleBatchEdit
from app.schemas.role_preparation import ExtractionBatch, QuoteReference, ParagraphReference
from app.services import role_batch_service as batches, role_preparation_job_service as jobs
from app.common.exceptions import BusinessException
from test_role_jobs import create_book, provider, model, FakeModel


@pytest.fixture(autouse=True)
def no_background(monkeypatch):
    monkeypatch.setattr(batches, "launch", lambda *args: None)


def run(job):
    batches.run_job(job.id, job.run_token)
    with SessionLocal() as db:
        return db.get(RolePreparationJob, job.id)


def prepared(db, *, long=True):
    book_id = create_book(db, long=long)
    return book_id, batches.prepare(db, book_id, RoleJobCreate(name="岑砚", batch_chars=512))


def test_prepare_is_durable_no_model_no_budget_gate_and_chapter_only(provider, model):
    with SessionLocal() as db:
        book_id = create_book(db, long=True)
        request = RoleJobCreate(name="岑砚", batch_chars=512, max_calls=1)
        preview = batches.preview(db, book_id, request)
        assert not model.messages and db.query(RolePreparationJob).count() == 0
        job = batches.prepare(db, book_id, request)
        assert job.status == "ready" and job.active_slot is None and job.calls == 0
        assert job.plan["request_interval_seconds"] == 0 and job.plan["batch_count"] > 2
        for item in db.query(RolePreparationBatch).filter_by(job_id=job.id):
            assert {scene["chapter_number"] for scene in item.scenes} == {item.chapter_number}
            assert all(scene["kind"] == "main" for scene in item.scenes)
        assert batches.list_batches(db, book_id, job.id)["total"] == preview["batch_count"]
    assert model.messages == []


def test_generate_two_no_interval_no_repair_no_auto_synthesis_and_continue(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
    stopped = run(job)
    assert stopped.status == "ready" and stopped.calls == 2 and stopped.completed_batches == 2
    assert all('"paragraphs"' in message[1]["content"] for message in model.messages)
    with SessionLocal() as db:
        assert db.query(RoleProfileVersion).count() == 0
        job = batches.extract(db, book_id, job.id, RoleBatchRun(stop_at=3))
    stopped = run(job)
    assert stopped.calls == 3 and stopped.completed_batches == 3


def test_bad_batch_is_visible_editable_and_never_automatically_retried(provider, model, monkeypatch):
    def invalid(*args, **kwargs):
        return {"content": "synthetic invalid output"}
    monkeypatch.setattr(jobs.llm_service, "chat_completion", invalid)
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
    stopped = run(job)
    assert stopped.status == "ready" and stopped.calls == 2
    with SessionLocal() as db:
        detail = batches.batch_detail(db, book_id, job.id, 1)
        assert detail["status"] == "failed" and detail["raw_content"] == "synthetic invalid output"
        with pytest.raises(BusinessException):
            batches.synthesize(db, book_id, job.id, RoleJobResume())
        scene = detail["scenes"][0]
        result = ExtractionBatch(character="岑砚", observations=[{
            "kind":"action", "claim":"人工核对的合成行为", "attribution":"narrator", "knowledge":"unknown",
            "evidence":[{"scene_id":scene["scene_id"], "quote":scene["text"][:30]}]}])
        payload = RoleBatchEdit(revision=0, result=result)
        saved = batches.edit_batch(db, book_id, job.id, 1, payload)
        assert saved["status"] == "done" and saved["revision"] == 1
        assert db.query(RoleBatchRevision).one().source == "user"
        with pytest.raises(BusinessException, match="修改"):
            batches.edit_batch(db, book_id, job.id, 1, payload)
        bad = result.model_copy(deep=True)
        bad.observations[0].evidence[0].quote = "不在原文里的合成引文"
        with pytest.raises(BusinessException, match="quote_not_found"):
            batches.edit_batch(db, book_id, job.id, 1, RoleBatchEdit(revision=1, result=bad))


def test_429_stops_selection_without_delay_or_hidden_requests(provider, model, monkeypatch):
    count = []
    def limited(provider, messages, **options):
        count.append(1)
        if len(count) == 2:
            return {"status_code":429, "error":"synthetic RPM limit"}
        return model(provider, messages, **options)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", limited)
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=5))
    stopped = run(job)
    assert stopped.status == "paused" and stopped.error == "model_rate_limited"
    assert stopped.calls == 2 and stopped.completed_batches == 1 and len(count) == 2
    assert len(stopped.plan["run_remaining"]) == min(5, stopped.plan["batch_count"]) - 1


def test_manual_merge_edit_marks_stale_remerge_and_delete_keeps_snapshot(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db, long=False)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(stop_at=job.plan["batch_count"]))
    assert run(job).status == "ready"
    with SessionLocal() as db:
        assert db.query(RoleProfileVersion).count() == 0
        job = batches.synthesize(db, book_id, job.id, RoleJobResume())
    assert run(job).status == "done"
    with SessionLocal() as db:
        original = jobs.latest_profile(db, book_id, job.role_id)
        assert not original["stale"]
        detail = batches.batch_detail(db, book_id, job.id, 1)
        result = ExtractionBatch.model_validate(detail["result"])
        result.observations[0].claim = "已人工修订的合成行为"
        batches.edit_batch(db, book_id, job.id, 1, RoleBatchEdit(revision=detail["revision"], result=result))
        assert jobs.latest_profile(db, book_id, job.role_id)["stale"]
        job = batches.synthesize(db, book_id, job.id, RoleJobResume())
    assert run(job).status == "done"
    with SessionLocal() as db:
        assert jobs.latest_profile(db, book_id, job.role_id)["version"] == 2
        batches.delete_plan(db, book_id, job.id)
        assert db.query(RolePreparationBatch).count() == 0 and db.query(RoleBatchRevision).count() == 0
        saved = jobs.latest_profile(db, book_id, job.role_id)
        assert saved["available"] and not saved["stale"]
        assert db.query(RoleProfileVersion).count() == 2
        assert all(p.job_id is None for p in db.query(RoleProfileVersion))
        assert jobs.profile_evidence(db, book_id, saved["profile_id"])["total"] > 0
        batches.delete_role(db, book_id, job.role_id)
        assert db.query(RoleProfileVersion).count() == 0


def test_active_delete_and_cross_book_access_are_rejected(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        other = create_book(db)
        for operation in (lambda: batches.batch_detail(db, other, job.id, 1), lambda: batches.delete_plan(db, other, job.id),
                          lambda: batches.delete_role(db, other, job.role_id)):
            with pytest.raises(BusinessException):
                operation()
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
        for operation in (lambda: batches.delete_plan(db, book_id, job.id), lambda: batches.delete_role(db, book_id, job.role_id)):
            with pytest.raises(BusinessException):
                operation()
        jobs.request_pause(db, book_id, job.id)
        batches.delete_plan(db, book_id, job.id)


def test_staged_endpoints_prepare_without_requests(client, provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
    root = f"/api/books/{book_id}/role-chat"
    prepared = client.post(root + "/preparation/plans", json={"name":"岑砚"}).json()["data"]
    assert prepared["status"] == "ready" and prepared["workflow"] == batches.WORKFLOW
    assert not model.messages
    page = client.get(root + f"/preparation/jobs/{prepared['id']}/batches").json()["data"]
    assert page["total"] == 2
    assert client.delete(root + f"/preparation/jobs/{prepared['id']}").json()["code"] == 200


def test_legacy_profile_schema_upgrade_keeps_snapshots_and_allows_detach(provider, model):
    from app import database
    from sqlalchemy.schema import CreateTable
    with SessionLocal() as db:
        book_id = create_book(db)
        job = jobs.create_job(db, book_id, RoleJobCreate(name="岑砚", request_interval_seconds=0))
    jobs.run_job(job.id, job.run_token)
    with SessionLocal() as db:
        profile_id = db.query(RoleProfileVersion).one().id
    old_sql = str(CreateTable(RoleProfileVersion.__table__).compile(database.engine))
    old_sql = old_sql.replace("job_id INTEGER,", "job_id INTEGER NOT NULL UNIQUE,").replace("ON DELETE SET NULL", "ON DELETE CASCADE")
    with database.engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.exec_driver_sql("ALTER TABLE role_profile_versions RENAME TO role_profiles_backup")
        connection.exec_driver_sql(old_sql)
        columns = ', '.join('"' + c.name + '"' for c in RoleProfileVersion.__table__.columns)
        connection.exec_driver_sql(f'INSERT INTO role_profile_versions ({columns}) SELECT {columns} FROM role_profiles_backup')
        connection.exec_driver_sql("DROP TABLE role_profiles_backup")
        connection.commit()
    batches.ensure_schema()
    batches.ensure_schema()  # Reopening the app is idempotent.
    with SessionLocal() as db:
        assert db.get(RoleProfileVersion, profile_id).job_id == job.id
        batches.delete_plan(db, book_id, job.id)
        assert db.get(RoleProfileVersion, profile_id).job_id is None
        assert jobs.latest_profile(db, book_id, job.role_id)["available"]


def test_pause_resume_keeps_selected_range_and_does_not_repeat_completed_batch(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
    def pause_first(count):
        if count == 1:
            with SessionLocal() as db:
                jobs.request_pause(db, book_id, job.id)
    model.on_call = pause_first
    stopped = run(job)
    assert stopped.status == "paused" and stopped.completed_batches == 1
    assert len(stopped.plan["run_remaining"]) == 1
    with SessionLocal() as db:
        resumed = batches.resume(db, book_id, job.id, RoleJobResume())
    completed = run(resumed)
    assert completed.status == "ready" and completed.calls == 2 and completed.completed_batches == 2


def test_failed_raw_draft_identifies_invalid_observation_without_adopting_it(provider):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        item = db.query(RolePreparationBatch).filter_by(job_id=job.id, number=1).one()
        scene = item.scenes[0]
        good = {"kind":"action", "claim":"合成行为", "attribution":"narrator", "knowledge":"unknown",
                "evidence":[{"scene_id":scene["scene_id"], "quote":scene["text"][:30]}]}
        bad = {**good, "evidence":[{"scene_id":scene["scene_id"], "quote":"原文没有的合成引文"}]}
        item.raw_content = json.dumps({"character":"岑砚", "observations":[good, bad]}, ensure_ascii=False)
        item.status, item.error = "failed", "quote_not_found"
        db.commit()
        detail = batches.batch_detail(db, book_id, job.id, 1)
        assert detail["result"] is None and len(detail["draft_result"]["observations"]) == 2
        assert detail["issues"] == [{"observation_index":1, "code":"quote_not_found"}]
        assert item.revision == 0 and job.completed_batches == 0


def test_existing_staged_record_runs_beyond_its_old_lifetime_limit(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job.calls, job.max_calls = 1000, 64
        db.commit()
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
    stopped = run(job)
    assert stopped.status == "ready" and stopped.calls == 1002 and stopped.completed_batches == 2
    assert len(model.messages) == 2 and stopped.max_calls == 0


def test_new_plan_api_has_no_budget_and_never_calls_model(client, provider, model):
    with SessionLocal() as db:
        book_id = create_book(db)
    result = client.post(f"/api/books/{book_id}/role-chat/preparation/plans", json={"name":"岑砚"}).json()
    assert result["code"] == 200 and result["data"]["max_calls"] is None
    assert not model.messages


def test_old_record_uses_current_active_provider_for_each_explicit_run(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db, long=False)
        old_id = job.provider_id
        provider.update(provider_id=202, model="synthetic-second")
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=1))
        assert job.provider_id != old_id and job.model_hash == jobs._provider_hash(provider)
    assert run(job).completed_batches == 1
    with SessionLocal() as db:
        provider.update(provider_id=303, model="synthetic-third")
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=1))
    assert run(job).completed_batches == 2
    with SessionLocal() as db:
        provider.update(provider_id=404, model="synthetic-fourth")
        job = batches.synthesize(db, book_id, job.id, RoleJobResume())
        assert job.provider_id == 404
    assert run(job).status == "done" and len(model.messages) == 3


def test_resumed_range_resolves_active_provider_without_losing_completed_batches(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
    def pause_first(count):
        if count == 1:
            with SessionLocal() as db:
                jobs.request_pause(db, book_id, job.id)
    model.on_call = pause_first
    assert run(job).completed_batches == 1
    with SessionLocal() as db:
        provider.update(provider_id=202, model="synthetic-resume")
        job = batches.resume(db, book_id, job.id, RoleJobResume())
        assert job.provider_id == 202
    assert run(job).completed_batches == 2 and len(model.messages) == 2


def test_run_pins_active_provider_until_next_explicit_start(provider, model, monkeypatch):
    configs = {101: dict(provider), 202: {**provider, "provider_id": 202, "model": "synthetic-next"}}
    active, used = [101], []
    def config(db, provider_id=None):
        return dict(configs[active[0] if provider_id is None else provider_id])
    def complete(config, messages, **options):
        used.append(config["provider_id"])
        active[0] = 202
        return model(config, messages, **options)
    monkeypatch.setattr(jobs.llm_provider_service, "get_active_provider_config", config)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", complete)
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
    assert run(job).completed_batches == 2 and used == [101, 101]
    with SessionLocal() as db:
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=1))
    run(job)
    assert used[-1] == 202


def test_no_active_provider_rejects_run_but_local_edits_still_work(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        detail = batches.batch_detail(db, book_id, job.id, 1)
        scene = detail["scenes"][0]
        result = ExtractionBatch(character="岑砚", observations=[{
            "kind": "action", "claim": "合成行为", "attribution": "narrator", "knowledge": "unknown",
            "evidence": [{"scene_id": scene["scene_id"], "quote": scene["text"][:30]}]}])
        def no_active(*args, **kwargs):
            raise BusinessException("请先激活模型服务", code=4000)
        monkeypatch.setattr(jobs.llm_provider_service, "get_active_provider_config", no_active)
        saved = batches.edit_batch(db, book_id, job.id, 1, RoleBatchEdit(revision=0, result=result))
        assert saved["status"] == "done"
        with pytest.raises(BusinessException, match="激活"):
            batches.extract(db, book_id, job.id, RoleBatchRun(limit=1))
        db.refresh(job)
        assert job.status == "ready" and job.calls == 0
    assert not model.messages


def test_legacy_ellipsis_can_be_repaired_by_selecting_paragraph_and_mixed_batches_merge(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db, long=False)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=2))
    assert run(job).completed_batches == 2
    with SessionLocal() as db:
        detail = batches.batch_detail(db, book_id, job.id, 1)
        assert detail["paragraphs"] and detail["result"]["observations"][0]["evidence"][0].get("paragraph_id")
        paragraph = detail["paragraphs"][0]
        result = ExtractionBatch.model_validate(detail["result"])
        # A prior manually validated quote can coexist with new paragraph refs.
        result.observations[0].evidence = [QuoteReference(scene_id=paragraph["scene_id"], quote=paragraph["text"][:20])]
        result = ExtractionBatch.model_validate(result.model_dump())
        saved = batches.edit_batch(db, book_id, job.id, 1, RoleBatchEdit(revision=detail["revision"], result=result))
        assert "quote" in saved["result"]["observations"][0]["evidence"][0]
        converted = ExtractionBatch.model_validate(saved["result"])
        converted.observations[0].evidence = [ParagraphReference(paragraph_id=paragraph["paragraph_id"], opening="不存在……省略的开头")]
        converted = ExtractionBatch.model_validate(converted.model_dump())
        saved = batches.edit_batch(db, book_id, job.id, 1, RoleBatchEdit(revision=saved["revision"], result=converted))
        assert saved["status"] == "done" and not saved["issues"]
        assert saved["resolved_evidence"][0]["evidence"][0]["location_warning"] == "quote_omitted"
        # Leave one legacy reference alongside the new paragraph batch for merge.
        batches.edit_batch(db, book_id, job.id, 1, RoleBatchEdit(revision=saved["revision"], result=result))
        job = batches.synthesize(db, book_id, job.id, RoleJobResume())
    assert run(job).status == "done"


def test_oversized_new_generation_retains_raw_without_automatic_retry(provider, monkeypatch):
    def oversized(provider, messages, **options):
        data = json.loads(messages[1]["content"])
        item = {"kind":"action", "claim":"合成观察", "attribution":"narrator", "knowledge":"unknown",
                "evidence":[{"paragraph_id":data["paragraphs"][0]["paragraph_id"]}]}
        return {"content":json.dumps({"character":data["target"]["name"], "observations":[item]*13})}
    monkeypatch.setattr(jobs.llm_service, "chat_completion", oversized)
    with SessionLocal() as db:
        book_id, job = prepared(db, long=False)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=1))
    stopped = run(job)
    assert stopped.calls == 1 and stopped.completed_batches == 0
    with SessionLocal() as db:
        detail = batches.batch_detail(db, book_id, job.id, 1)
        assert detail["status"] == "failed" and detail["error"] == "invalid_model_schema"
        assert len(detail["draft_result"]["observations"]) == 13 and detail["result"] is None


def test_retry_all_failed_is_global_ordered_once_and_preserves_other_batches(provider, model, monkeypatch):
    with SessionLocal() as db:
        book_id, job = prepared(db)
        job = batches.extract(db, book_id, job.id, RoleBatchRun(limit=1))
    assert run(job).completed_batches == 1
    with SessionLocal() as db:
        original = batches.batch_detail(db, book_id, job.id, 1)
        for number in (2, 4):
            row = db.query(RolePreparationBatch).filter_by(job_id=job.id, number=number).one()
            row.status, row.error = "failed", "invalid_model_json"
        db.commit()
        page = batches.list_batches(db, book_id, job.id, offset=0, limit=1)
        assert page["failed_total"] == 2 and page["items"][0]["status"] == "done"
        job = batches.extract(db, book_id, job.id, RoleBatchRun(retry_failed=True))
        assert job.plan["run_remaining"] == [2, 4]
    attempted = []
    def retry(provider, messages, **options):
        attempted.append(json.loads(messages[1]["content"])["paragraphs"][0]["paragraph_id"])
        if len(attempted) == 1:
            return {"content": "synthetic failed again"}
        return model(provider, messages, **options)
    monkeypatch.setattr(jobs.llm_service, "chat_completion", retry)
    stopped = run(job)
    assert stopped.calls == 3 and stopped.completed_batches == 2 and len(attempted) == 2
    assert stopped.status == "ready" and stopped.plan["run_remaining"] == []
    with SessionLocal() as db:
        unchanged = batches.batch_detail(db, book_id, job.id, 1)
        assert unchanged["result"] == original["result"] and unchanged["revision"] == original["revision"]
        page = batches.list_batches(db, book_id, job.id)
        assert page["failed_total"] == 1
        states = {row["number"]: row["status"] for row in page["items"]}
        assert states[2] == "failed" and states[3] == "pending" and states[4] == "done"
        assert db.query(RoleProfileVersion).count() == 0


def test_retry_failed_pause_resume_keeps_exact_queue(provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db, long=False)
        for row in db.query(RolePreparationBatch).filter_by(job_id=job.id):
            row.status = "failed"
        db.commit()
        job = batches.extract(db, book_id, job.id, RoleBatchRun(retry_failed=True))
    def pause_first(count):
        if count == 1:
            with SessionLocal() as db:
                jobs.request_pause(db, book_id, job.id)
    model.on_call = pause_first
    stopped = run(job)
    assert stopped.status == "paused" and stopped.plan["run_remaining"] == [2]
    with SessionLocal() as db:
        job = batches.resume(db, book_id, job.id, RoleJobResume())
    stopped = run(job)
    assert stopped.status == "ready" and stopped.completed_batches == 2 and stopped.calls == 2


def test_retry_failed_rate_limit_pauses_with_unfinished_queue(provider, monkeypatch):
    attempted = []
    def limited(*args, **kwargs):
        attempted.append(1)
        return {"status_code":429, "error":"synthetic request limit"}
    monkeypatch.setattr(jobs.llm_service, "chat_completion", limited)
    with SessionLocal() as db:
        book_id, job = prepared(db, long=False)
        for row in db.query(RolePreparationBatch).filter_by(job_id=job.id):
            row.status = "failed"
        db.commit()
        job = batches.extract(db, book_id, job.id, RoleBatchRun(retry_failed=True))
    stopped = run(job)
    assert stopped.status == "paused" and stopped.error == "model_rate_limited"
    assert stopped.plan["run_remaining"] == [1,2] and len(attempted) == 1


def test_retry_failed_empty_and_active_and_cross_book_do_not_send_requests(client, provider, model):
    with SessionLocal() as db:
        book_id, job = prepared(db, long=False)
        job_id = job.id
        other = create_book(db)
    url = f"/api/books/{book_id}/role-chat/preparation/jobs/{job_id}/extract"
    assert client.post(url, json={"retry_failed":True}).json()["code"] == 4000
    with SessionLocal() as db:
        row = db.query(RolePreparationBatch).filter_by(job_id=job_id, number=1).one()
        row.status = "failed"; db.commit()
    assert client.post(url.replace(f"books/{book_id}/", f"books/{other}/"), json={"retry_failed":True}).json()["code"] == 4040
    result = client.post(url, json={"retry_failed":True}).json()
    assert result["code"] == 200 and result["data"]["status"] == "queued"
    assert client.post(url, json={"retry_failed":True}).json()["code"] == 4090
    assert not model.messages


@pytest.mark.parametrize("extra", [{"limit":2}, {"stop_at":3}, {"batch_number":1}, {"force":True}])
def test_retry_failed_cannot_mix_with_another_selection(extra):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        RoleBatchRun(retry_failed=True, **extra)
