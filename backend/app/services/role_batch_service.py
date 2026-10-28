"""Explicit prepare/extract/synthesize operations; role data never becomes canon."""

import threading
import uuid

from sqlalchemy import MetaData, func, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable, CreateIndex

from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.role_preparation import RoleCharacter, RolePreparationJob, RoleProfileVersion, RolePreparationBatch, RoleBatchRevision
from app.schemas.role_preparation import RoleTarget, RoleScene, ExtractionBatch, RoleProfile
from app.services import role_book_service as books, role_preparation_job_service as jobs
from app.services import role_preparation_prompt as prompts, llm_provider_service
from app.services.role_paragraph_service import source_paragraphs
from app.services import role_synthesis_service as synthesis
from app.services.role_preparation_service import (
    PreparationError, select_scenes, ground_batch, extract_role_batch, deduplicate_observations,
    synthesize_role, validate_profile,
)

WORKFLOW = "chapter_batches_v1"


def ensure_schema():
    """Retain published snapshots when deleting source jobs in older SQLite DBs."""
    from app import database
    engine = database.engine
    info = inspect(engine)
    if not info.has_table("role_profile_versions"):
        return
    nullable = next(c["nullable"] for c in info.get_columns("role_profile_versions") if c["name"] == "job_id")
    unique_job = any(c["column_names"] == ["job_id"] for c in info.get_unique_constraints("role_profile_versions"))
    fks = info.get_foreign_keys("role_profile_versions")
    set_null = any(f["constrained_columns"] == ["job_id"] and
                   f.get("options", {}).get("ondelete", "").upper() == "SET NULL" for f in fks)
    if nullable and not unique_job and set_null:
        return
    # No tables reference profile versions. Rebuild only this role-owned table,
    # with explicit transactional DDL; all ids, snapshots and timestamps survive.
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            metadata = MetaData()
            metadata.reflect(bind=connection, only=["role_preparation_jobs", "role_characters"])
            temporary = RoleProfileVersion.__table__.to_metadata(metadata, name="_role_profiles_upgrade")
            connection.execute(CreateTable(temporary))
            columns = ", ".join('"' + c.name + '"' for c in temporary.columns)
            connection.exec_driver_sql(f'INSERT INTO _role_profiles_upgrade ({columns}) SELECT {columns} FROM role_profile_versions')
            connection.exec_driver_sql("DROP TABLE role_profile_versions")
            connection.exec_driver_sql("ALTER TABLE _role_profiles_upgrade RENAME TO role_profile_versions")
            for index in RoleProfileVersion.__table__.indexes:
                connection.execute(CreateIndex(index))
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def is_staged(job):
    return job.plan.get("workflow") == WORKFLOW


def require_plan(db, book_id, job_id):
    job = jobs.get_job(db, book_id, job_id)
    if not is_staged(job):
        raise BusinessException("这是旧流程记录，请重新准备批次；已生成资料仍可查看", code=4090)
    return job


def chapter_batches(scenes, max_chars):
    result, current, count = [], [], 0
    for scene in scenes:
        if current and (scene.chapter_number != current[0].chapter_number or count + len(scene.text) > max_chars):
            result.append(current)
            current, count = [], 0
        current.append(scene)
        count += len(scene.text)
    if current:
        result.append(current)
    return result


def build_plan(db, book_id, request):
    target = books.resolve_target(db, book_id, request.name)
    scenes, stamp = books.load_scenes(db, book_id, request.batch_chars)
    if not any(name in s.text for s in scenes for name in [target.name, *target.aliases]):
        raise BusinessException("正文中未定位到该人物，请核对人物名称", code=4000)
    selected, coverage = select_scenes(target, scenes, request.mode)
    batches = chapter_batches(selected, request.batch_chars)
    provider = llm_provider_service.get_active_provider_config(db)
    plan = {"workflow": WORKFLOW, "target": target.model_dump(), "mode": request.mode,
            "batch_chars": request.batch_chars, "batch_count": len(batches), "coverage": coverage,
            "scene_version": books.SCENE_VERSION, "request_interval_seconds": 0,
            "results_revision": 0, "run_remaining": [], "run_attempted": 0, "run_successes": 0, "run_failures": 0}
    return plan, provider, stamp, batches


def preview(db, book_id, request):
    plan, _, _, _ = build_plan(db, book_id, request)
    return {"target": plan["target"], "mode": plan["mode"], "batch_count": plan["batch_count"],
            "minimum_calls": plan["batch_count"], "selected_chars": plan["coverage"]["selected_chars"],
            "model_called": False, "extras_included": False, "scope": "full_main_text"}


def prepare(db, book_id, request):
    plan, provider, stamp, batches = build_plan(db, book_id, request)
    with jobs._lock:
        try:
            role = books.find_or_create_role(db, book_id, RoleTarget.model_validate(plan["target"]))
            job = RolePreparationJob(book_id=book_id, role_id=role.id, status="ready", stage="prepare",
                                     active_slot=None, run_token=uuid.uuid4().hex, source_hash=stamp,
                                     provider_id=provider["provider_id"], model_hash=jobs._provider_hash(provider),
                                     prompt_version=prompts.PROMPT_VERSION, plan=plan, max_calls=0,
                                     calls=0, completed_batches=0, cache_hits=0, metrics=[])
            db.add(job); db.flush()
            for number, scenes in enumerate(batches, 1):
                db.add(RolePreparationBatch(job_id=job.id, number=number, chapter_number=scenes[0].chapter_number,
                                            scenes=[s.model_dump() for s in scenes], status="pending", revision=0))
            db.commit(); db.refresh(job)
            return job
        except IntegrityError:
            db.rollback()
            raise BusinessException("另一个窗口正在创建资料，请刷新后重试", code=4090) from None


def _idle(job):
    if job.status in jobs.ACTIVE:
        raise BusinessException("请先暂停任务，待停止后再操作", code=4090)


def _sources(db, job):
    try:
        jobs._assert_sources(db, job)
    except PreparationError as exc:
        raise BusinessException(f"来源或配置已变化（{exc.code}），请重新准备批次", code=4090) from None


def _batch(db, job, number):
    item = db.query(RolePreparationBatch).filter_by(job_id=job.id, number=number).first()
    if not item:
        raise BusinessException("批次不存在", code=4040)
    return item


def batch_dict(item, detail=False):
    result = {"number": item.number, "chapter_number": item.chapter_number, "status": item.status,
              "revision": item.revision, "error": item.error,
              "body_chars": sum(len(s["text"]) for s in item.scenes),
              "observation_count": len((item.result or {}).get("observations", []))}
    if detail:
        result.update(scenes=item.scenes, result=item.result, raw_content=item.raw_content)
    return result


def list_batches(db, book_id, job_id, offset=0, limit=20):
    job = require_plan(db, book_id, job_id)
    query = db.query(RolePreparationBatch).filter_by(job_id=job.id)
    return {"items": [batch_dict(row) for row in query.order_by(RolePreparationBatch.number).offset(offset).limit(limit)],
            "total": query.count(), "failed_total": query.filter_by(status="failed").count(), "offset": offset, "limit": limit}


def batch_detail(db, book_id, job_id, number):
    job = require_plan(db, book_id, job_id)
    item = _batch(db, job, number)
    draft = None
    if item.result is None and item.raw_content:
        from app.services.role_preparation_service import _parse
        try:
            draft = _parse(item.raw_content, ExtractionBatch).model_dump()
        except PreparationError:
            pass
    issues, resolved = [], []
    candidate = item.result or draft
    if candidate:
        parsed = ExtractionBatch.model_validate(candidate)
        for index, observation in enumerate(parsed.observations):
            try:
                grounded = _validated(job, item, ExtractionBatch(character=parsed.character, observations=[observation]))
                resolved.append({"observation_index": index, "evidence": [q.model_dump() for q in grounded[0].evidence]})
            except PreparationError as exc:
                issues.append({"observation_index": index, "code": exc.code})
    return {**batch_dict(item, True), "character": job.plan["target"]["name"], "draft_result": draft, "issues": issues,
            "paragraphs": source_paragraphs([RoleScene.model_validate(s) for s in item.scenes]), "resolved_evidence": resolved}


def _validated(job, batch, value):
    target = RoleTarget.model_validate(job.plan["target"])
    scenes = [RoleScene.model_validate(s) for s in batch.scenes]
    return ground_batch(target, scenes, value)


def _save_result(db, job, item, result, source):
    item.revision += 1
    item.result, item.status, item.error = result.model_dump(), "done", None
    db.add(RoleBatchRevision(batch_id=item.id, revision=item.revision, result=item.result, source=source))
    job.plan = {**job.plan, "results_revision": job.plan["results_revision"] + 1}
    job.draft_profile = None
    db.flush()
    job.completed_batches = db.query(RolePreparationBatch).filter_by(job_id=job.id, status="done").count()


def edit_batch(db, book_id, job_id, number, payload):
    with jobs._lock:
        job = require_plan(db, book_id, job_id); _idle(job); _sources(db, job)
        item = _batch(db, job, number)
        if item.revision != payload.revision:
            raise BusinessException("该批次已在其他窗口修改，请重新打开后编辑", code=4090)
        try:
            _validated(job, item, payload.result)
        except PreparationError as exc:
            raise BusinessException(f"结果未通过校验（{exc.code}），请对照原文修改", code=4000) from None
        # Reserve the parent row before writing the child; concurrent tabs cannot
        # race a start/delete operation or another editor revision.
        owned = db.query(RolePreparationJob).filter_by(id=job.id, run_token=job.run_token).filter(
            ~RolePreparationJob.status.in_(jobs.ACTIVE)).update({"run_token": uuid.uuid4().hex}, synchronize_session=False)
        if not owned:
            db.rollback()
            raise BusinessException("任务已变化，请刷新后重试", code=4090)
        db.refresh(job)
        db.refresh(item)
        if item.revision != payload.revision:
            db.rollback()
            raise BusinessException("该批次已有新版本，请刷新后重试", code=4090)
        _save_result(db, job, item, payload.result, "user")
        job.status, job.stage, job.error = "ready", "extract", None
        db.commit()
        return batch_detail(db, book_id, job_id, number)


def _start(db, job, request, stage, remaining, resume=False):
    _idle(job); _sources(db, job); jobs._recover_active_requests(db)
    if db.query(RolePreparationJob.id).filter_by(active_slot=1).first():
        raise BusinessException("已有角色抽取任务，请先暂停或等待完成", code=4090)
    plan = {**job.plan, "run_remaining": remaining, "request_started_at": None, "request_interval_seconds": 0}
    if stage == "synthesize":
        plan["synthesis_run_start_calls"] = job.calls
    # Resolve settings at each explicit run, then pin that run's provider.
    provider = llm_provider_service.get_active_provider_config(db)
    if stage == "synthesize" and getattr(request, "preview_key", None) and request.preview_key != _preview_key(job, provider):
        raise BusinessException("激活模型或资料已变化，请重新预览合成计划", code=4090)
    model_hash = jobs._provider_hash(provider)
    if model_hash != job.model_hash:
        plan.pop("rate_limit_until", None)
    if not resume:
        plan.update(run_attempted=0, run_successes=0, run_failures=0)
    token = uuid.uuid4().hex
    try:
        changed = db.query(RolePreparationJob).filter_by(id=job.id, run_token=job.run_token).filter(
            ~RolePreparationJob.status.in_(jobs.ACTIVE)).update({"plan": plan, "stage": stage, "status": "queued",
                "active_slot": 1, "error": None, "run_token": token, "max_calls": 0,
                "provider_id": provider["provider_id"], "model_hash": model_hash}, synchronize_session=False)
        if not changed:
            raise BusinessException("任务已被其他窗口操作，请刷新", code=4090)
        db.commit(); db.refresh(job)
    except IntegrityError:
        db.rollback()
        raise BusinessException("另一个窗口已启动抽取，请刷新", code=4090) from None
    launch(job.id, token)
    return job


def extract(db, book_id, job_id, request):
    with jobs._lock:
        job = require_plan(db, book_id, job_id)
        query = db.query(RolePreparationBatch).filter_by(job_id=job.id)
        if request.retry_failed:
            numbers = [row.number for row in query.filter_by(status="failed").order_by(RolePreparationBatch.number)]
        elif request.batch_number is not None:
            item = _batch(db, job, request.batch_number)
            if item.status == "done" and not request.force:
                raise BusinessException("该批次已完成；需要重新抽取时请明确选择重抽", code=4090)
            numbers = [item.number]
        else:
            query = query.filter(RolePreparationBatch.status.in_(("pending", "running")))
            if request.stop_at is not None:
                if request.stop_at > job.plan["batch_count"]:
                    raise BusinessException("截止批号超出范围", code=4000)
                query = query.filter(RolePreparationBatch.number <= request.stop_at)
            query = query.order_by(RolePreparationBatch.number)
            if request.limit is not None:
                query = query.limit(request.limit)
            numbers = [row.number for row in query]
        if not numbers:
            if request.retry_failed:
                raise BusinessException("当前记录没有失败批次，请刷新后查看", code=4000)
            raise BusinessException("所选范围没有待处理批次；失败批次可以批量重试或编辑", code=4000)
        return _start(db, job, request, "extract", numbers)


def synthesize(db, book_id, job_id, request):
    with jobs._lock:
        job = require_plan(db, book_id, job_id)
        unfinished = db.query(RolePreparationBatch).filter_by(job_id=job.id).filter(RolePreparationBatch.status != "done").count()
        if unfinished:
            raise BusinessException("请先完成所有批次；失败项可批量重试或编辑修复", code=4090)
        provider = llm_provider_service.get_active_provider_config(db)
        if getattr(request, "preview_key", None) and request.preview_key != _preview_key(job, provider):
            raise BusinessException("资料或激活模型已变化，请重新预览合成计划", code=4090)
        plan = {**job.plan, "synthesis_run_limit": getattr(request, "limit", None)}
        if getattr(request, "retry_failed", False):
            key = plan.get("synthesis_state", {}).get("key")
            responses = dict(plan.get("synthesis_responses", {}))
            if key in responses:
                responses[key] = {**responses[key], "reuse": False}
            plan["synthesis_responses"] = responses
        job.plan = plan
        return _start(db, job, request, "synthesize", [])


def _preview_key(job, provider):
    return books.digest({"job": job.id, "source": job.source_hash, "revision": job.plan["results_revision"],
                         "model": jobs._provider_hash(provider), "format": synthesis.FORMAT_VERSION,
                         "group_chars": synthesis.GROUP_CHARS})


def _collect_synthesis(db, job):
    observations, gaps, revisions = [], [], []
    for item in db.query(RolePreparationBatch).filter_by(job_id=job.id).order_by(RolePreparationBatch.number):
        if item.status != "done":
            raise PreparationError("incomplete_batches")
        value = ExtractionBatch.model_validate(item.result)
        observations.extend(_validated(job, item, value)); gaps.extend(value.gaps)
        revisions.append([item.number, item.revision])
    coverage = {**job.plan["coverage"], "batch_count": job.plan["batch_count"], "gaps": list(dict.fromkeys(gaps)),
                "segmentation": WORKFLOW, "results_revision": job.plan["results_revision"], "batch_revisions": revisions}
    return RoleTarget.model_validate(job.plan["target"]), deduplicate_observations(observations), coverage


def synthesis_preview(db, book_id, job_id):
    job = require_plan(db, book_id, job_id); _sources(db, job)
    if job.completed_batches != job.plan["batch_count"]:
        raise BusinessException("请先完成全部抽取批次，再预览合成", code=4090)
    target, values, coverage = _collect_synthesis(db, job)
    provider = llm_provider_service.get_active_provider_config(db)
    model = jobs._runner(provider, max_repairs=0)
    return {**synthesis.preview(target, values, coverage, model, job.plan.get("synthesis_cache", {}),
                               synthesis.GROUP_CHARS, job.plan.get("synthesis_origins", {})),
            "preview_key": _preview_key(job, provider), "model": provider["model"]}


def synthesis_response(db, book_id, job_id):
    job = require_plan(db, book_id, job_id)
    key = job.plan.get("synthesis_state", {}).get("key")
    record = job.plan.get("synthesis_responses", {}).get(key)
    if not record:
        return {"available": False}
    return {"available": True, "cache_key": key,
            **{k: record.get(k) for k in ("revision", "raw_content", "error", "hint")}}


def edit_synthesis_response(db, book_id, job_id, payload):
    with jobs._lock:
        job = require_plan(db, book_id, job_id); _idle(job); _sources(db, job)
        record = job.plan.get("synthesis_responses", {}).get(payload.cache_key)
        if not record or record["revision"] != payload.revision or record["results_revision"] != job.plan["results_revision"]:
            raise BusinessException("合成返回或批次资料已变化，请重新打开后修订", code=4090)
        target, values, _ = _collect_synthesis(db, job)
        by_id = {o.observation_id: o for o in values}
        try:
            if record.get("error") == "model_output_truncated" and payload.raw_content == record["raw_content"]:
                raise PreparationError("model_output_truncated", "模型输出达到上限，返回内容不完整；请重新请求失败组。")
            allowed = [by_id[key] for key in record["allowed_ids"]]
            profile = synthesis.validate_response(target, allowed, payload.raw_content)
        except (KeyError, PreparationError) as exc:
            hint = exc.repair_hint if isinstance(exc, PreparationError) else "来源已变化"
            code = exc.code if isinstance(exc, PreparationError) else "source_changed"
            raise BusinessException(f"本地校验未通过（{code}）：{hint}", code=4000) from None
        state = {**job.plan.get("synthesis_state", {}), "status": "repaired", "error": None, "hint": None}
        if state.get("phase") == "group" and payload.cache_key not in job.plan.get("synthesis_cache", {}):
            state["group_completed"] = min(state.get("group_total", 1), state.get("group_completed", 0) + 1)
        plan = {**job.plan, "synthesis_state": state,
                "synthesis_cache": {**job.plan.get("synthesis_cache", {}), payload.cache_key: profile.model_dump()},
                "synthesis_origins": {**job.plan.get("synthesis_origins", {}), payload.cache_key: record.get("origin", {})},
                "synthesis_responses": {**job.plan["synthesis_responses"], payload.cache_key:
                    {**record, "raw_content": payload.raw_content, "error": None, "hint": None,
                     "reuse": True, "revision": payload.revision + 1}}}
        changed = db.query(RolePreparationJob).filter_by(id=job.id, run_token=job.run_token).filter(
            ~RolePreparationJob.status.in_(jobs.ACTIVE)).update(
                {"plan": plan, "status": "ready", "stage": "synthesize", "error": None, "run_token": uuid.uuid4().hex}, synchronize_session=False)
        if not changed:
            db.rollback()
            raise BusinessException("任务已变化，请刷新后重试", code=4090)
        db.commit(); db.refresh(job)
        return synthesis_response(db, book_id, job_id)


def resume(db, book_id, job_id, request):
    with jobs._lock:
        job = require_plan(db, book_id, job_id)
        if job.status not in jobs.RESUMABLE:
            raise BusinessException("该任务当前不需要续跑", code=4090)
        if job.stage != "synthesize" and not job.plan.get("run_remaining"):
            raise BusinessException("上次抽取已结束，请重新选择批次数", code=4090)
        if job.stage == "synthesize":
            job.plan = {**job.plan, "synthesis_run_limit": job.plan.get("synthesis_run_limit") or 1}
        return _start(db, job, request, job.stage, job.plan.get("run_remaining", []), True)


def launch(job_id, token):
    with jobs._lock:
        key = (job_id, token)
        if key in jobs._threads:
            return
        thread = threading.Thread(target=run_job, args=key, name=f"role-preparation-job-{job_id}", daemon=True)
        jobs._threads[key] = thread
        try:
            thread.start()
        except Exception:
            jobs._threads.pop(key, None)
            jobs._finish(job_id, token, "failed", "worker_start_failed")
            raise


def _owned(db, job_id, token, pausing=False):
    job = jobs._current(db, job_id, token, allow_pausing=pausing)
    jobs._assert_sources(db, job)
    changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token).filter(
        RolePreparationJob.status.in_(("running", "pausing") if pausing else ("running",))).update(
            {"stage": job.stage}, synchronize_session=False)
    if not changed:
        raise PreparationError("obsolete_worker")
    return job


def _attempt_result(job_id, token, number, value=None, error=None, raw=None):
    with SessionLocal() as db:
        job = _owned(db, job_id, token, True)
        item = _batch(db, job, number)
        if raw is not None:
            item.raw_content = raw
            db.commit()
            return
        if value is not None:
            _validated(job, item, value)
            _save_result(db, job, item, value, "model")
        else:
            item.status = "done" if item.result else "pending" if error == "pause_requested" else "failed"
            item.error = None if error == "pause_requested" else error
        plan = dict(job.plan)
        if value is not None or error not in ("pause_requested", "model_rate_limited", "model_request_timeout", "model_request_failed", "model_budget_exhausted"):
            plan["run_remaining"] = [n for n in plan["run_remaining"] if n != number]
        plan["run_attempted"] += error != "pause_requested"
        plan["run_successes"] += value is not None
        plan["run_failures"] += error not in (None, "pause_requested")
        job.plan = plan
        db.commit()


def _partial(job_id, token, key, value, origin=None):
    with SessionLocal() as db:
        job = _owned(db, job_id, token, True)
        job.plan = {**job.plan, "synthesis_cache": {**job.plan.get("synthesis_cache", {}), key: value.model_dump()},
                    "synthesis_origins": {**job.plan.get("synthesis_origins", {}), key: origin or {}}}
        db.commit()


def _synthesis_progress(job_id, token, state):
    with SessionLocal() as db:
        job = _owned(db, job_id, token, True)
        job.plan = {**job.plan, "synthesis_state": state}
        db.commit()


def _synthesis_response(job_id, token, key, record):
    with SessionLocal() as db:
        job = _owned(db, job_id, token, True)
        prior = job.plan.get("synthesis_responses", {}).get(key, {})
        job.plan = {**job.plan, "synthesis_responses": {**job.plan.get("synthesis_responses", {}), key:
                    {**record, "revision": prior.get("revision", 0) + 1, "results_revision": job.plan["results_revision"]}}}
        db.commit()


def run_job(job_id, token):
    try:
        with SessionLocal() as db:
            changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token, status="queued").update({"status": "running"})
            db.commit()
            if not changed:
                return
            job = _owned(db, job_id, token)
            provider = llm_provider_service.get_active_provider_config(db, job.provider_id)
            if jobs._provider_hash(provider) != job.model_hash:
                raise PreparationError("model_changed")
            target = RoleTarget.model_validate(job.plan["target"])
            stage, remaining = job.stage, list(job.plan["run_remaining"])
        def before_call(messages, purpose):
            if stage == "synthesize" and job.plan.get("synthesis_run_limit") and len(model.metrics) >= job.plan["synthesis_run_limit"]:
                raise PreparationError("synthesis_run_complete")
            jobs._before_call(job_id, token, messages, purpose)
        model = jobs._runner(provider, max_calls=None, max_repairs=0,
                             **({"max_tokens": synthesis.MAX_OUTPUT_TOKENS} if stage == "synthesize" else {}),
                             cancel_requested=lambda: jobs._cancel_requested(job_id, token),
                             before_call=before_call,
                             after_call=lambda metric: jobs._after_call(job_id, token, metric))
        if stage == "extract":
            complete = model.complete
            for number in remaining:
                with SessionLocal() as db:
                    job = _owned(db, job_id, token)
                    item = _batch(db, job, number)
                    scenes = [RoleScene.model_validate(s) for s in item.scenes]
                    item.status = "running"; db.commit()
                def capture(messages, **options):
                    response = complete(messages, **options)
                    if isinstance(response, dict) and isinstance(response.get("content"), str):
                        _attempt_result(job_id, token, number, raw=response["content"])
                    return response
                model.complete = capture
                try:
                    value = extract_role_batch(target, scenes, model)
                    _attempt_result(job_id, token, number, value=value)
                except PreparationError as exc:
                    _attempt_result(job_id, token, number, error=exc.code)
                    if exc.code in ("pause_requested", "model_rate_limited", "model_request_timeout", "model_request_failed", "model_budget_exhausted", "obsolete_worker", "model_changed", "source_changed", "preparation_version_changed"):
                        raise
                finally:
                    model.complete = complete
            jobs._checkpoint(job_id, token)
            jobs._finish(job_id, token, "ready")
            return
        with SessionLocal() as db:
            job = _owned(db, job_id, token)
            target, values, coverage = _collect_synthesis(db, job)
            cache, draft = job.plan.get("synthesis_cache", {}), job.draft_profile
            responses = job.plan.get("synthesis_responses", {})
        profile = RoleProfile.model_validate(draft) if draft else synthesize_role(
            target, values, coverage, model, profile_cache=cache, group_chars=synthesis.GROUP_CHARS,
            response_cache=responses,
            on_response=lambda key, record: _synthesis_response(job_id, token, key, record),
            on_progress=lambda state: _synthesis_progress(job_id, token, state),
            on_partial=lambda key, value: _partial(job_id, token, key, value, responses.get(key, {}).get("origin")))
        validate_profile(target, values, profile)
        with SessionLocal() as db:
            job = _owned(db, job_id, token, True)
            job.draft_profile = profile.model_dump(); db.commit()
        jobs._checkpoint(job_id, token)
        with SessionLocal() as db:
            job = _owned(db, job_id, token)
            version = (db.query(func.max(RoleProfileVersion.version)).filter_by(role_id=job.role_id).scalar() or 0) + 1
            changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token, status="running").update(
                {"status": "done", "stage": "done", "active_slot": None, "draft_profile": None})
            if not changed:
                raise PreparationError("obsolete_worker")
            db.add(RoleProfileVersion(book_id=job.book_id, role_id=job.role_id, job_id=job.id, version=version,
                                      source_hash=job.source_hash, prompt_version=job.prompt_version,
                                      target=target.model_dump(), profile=profile.model_dump(), observations=[o.model_dump() for o in values], coverage=coverage))
            db.commit()
    except PreparationError as exc:
        if exc.code == "synthesis_run_complete":
            jobs._finish(job_id, token, "ready")
            return
        jobs._finish(job_id, token, "paused" if exc.code in ("pause_requested", "model_rate_limited", "model_request_timeout", "model_request_failed", "model_budget_exhausted") else "failed",
                     None if exc.code == "pause_requested" else exc.code)
    except Exception:
        jobs._finish(job_id, token, "failed", "preparation_failed")
    finally:
        with jobs._lock:
            jobs._threads.pop((job_id, token), None)


def delete_plan(db, book_id, job_id):
    with jobs._lock:
        job = jobs.get_job(db, book_id, job_id); _idle(job)
        changed = db.query(RolePreparationJob).filter_by(id=job.id, run_token=job.run_token).filter(
            ~RolePreparationJob.status.in_(jobs.ACTIVE)).delete(synchronize_session=False)
        if not changed:
            raise BusinessException("任务已变化，请刷新后重试", code=4090)
        db.commit()
        return {"deleted": True, "profiles_preserved": True}


def delete_role(db, book_id, role_id):
    with jobs._lock:
        role = books.get_role(db, book_id, role_id)
        jobs._recover_active_requests(db)
        if db.query(RolePreparationJob.id).filter_by(role_id=role.id).filter(RolePreparationJob.status.in_(jobs.ACTIVE)).first():
            raise BusinessException("请先暂停该人物的任务，待停止后删除", code=4090)
        active = db.query(RolePreparationJob.id).filter_by(role_id=role.id).filter(RolePreparationJob.status.in_(jobs.ACTIVE)).exists()
        changed = db.query(RoleCharacter).filter_by(id=role.id, book_id=book_id).filter(~active).delete(synchronize_session=False)
        if not changed:
            raise BusinessException("人物已有运行任务，请暂停后再删除", code=4090)
        db.commit()
        return {"deleted": True}
