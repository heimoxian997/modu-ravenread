"""Persistent, explicitly started role preparation; pause at model/batch boundaries."""

import threading
import time
import math
import uuid

from pydantic import ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.common.exceptions import BusinessException
from app.database import SessionLocal
from app.models.role_preparation import (
    RoleCharacter, RoleExtractionCache, RolePreparationJob, RoleProfileVersion,
)
from app.schemas.role_preparation import ExtractionBatch, RoleProfile, RoleTarget
from app.services import llm_provider_service, llm_service, role_book_service as books
from app.services import role_preparation_prompt as prompts
from app.services.role_preparation_service import (
    ModelRunner, PreparationError, batch_cache_key, deduplicate_observations, extract_role_batch, ground_batch,
    make_batches, select_scenes, synthesize_role, validate_profile,
)

ACTIVE = ("queued", "running", "pausing")
RESUMABLE = ("paused", "interrupted", "failed")
REQUEST_TIMEOUT_SECONDS = 300
RECOVERY_GRACE_SECONDS = 30
_lock = threading.RLock()
_threads: dict[tuple[int, str], threading.Thread] = {}


def _provider_hash(provider):
    return books.digest({k: provider.get(k) for k in (
        "provider_id", "base_url", "model", "disable_thinking")})


def get_job(db, book_id, job_id):
    books.require_book(db, book_id)
    job = db.query(RolePreparationJob).filter_by(id=job_id, book_id=book_id).first()
    if not job:
        raise BusinessException("角色准备任务不存在", code=4040)
    _recover_expired_request(db, job)
    return job


def _request_started_at(job):
    if "request_started_at" in job.plan:
        return job.plan["request_started_at"]
    # Compatibility with earlier jobs which only stored request reservations.
    return job.plan.get("last_call_at") if job.calls > len(job.metrics or []) else None


def _recover_expired_request(db, job):
    started = _request_started_at(job)
    if job.status not in ("running", "pausing") or started is None:
        return
    if time.time() - started <= REQUEST_TIMEOUT_SECONDS + RECOVERY_GRACE_SECONDS:
        return
    old_token, old_status = job.run_token, job.status
    changed = db.query(RolePreparationJob).filter_by(
        id=job.id, run_token=old_token, status=old_status).update({
            "status": "paused" if old_status == "pausing" else "interrupted",
            "error": None if old_status == "pausing" else "model_request_timeout",
            "active_slot": None, "run_token": uuid.uuid4().hex,
            "plan": {**job.plan, "request_started_at": None},
        }, synchronize_session=False)
    if changed:
        db.commit()
    db.refresh(job)


def _recover_active_requests(db):
    for job in db.query(RolePreparationJob).filter(RolePreparationJob.active_slot == 1).all():
        _recover_expired_request(db, job)


def job_to_dict(job):
    total = job.plan["batch_count"]
    started = _request_started_at(job) if job.status in ACTIVE else None
    latest = (job.metrics or [{}])[-1]
    synthesis_metrics = [m for m in job.metrics if m.get("purpose") == "synthesize"]
    return {
        "id": job.id, "book_id": job.book_id, "role_id": job.role_id,
        "character": job.plan["target"]["name"], "provider_id": job.provider_id,
        "workflow": job.plan.get("workflow", "legacy"),
        "results_revision": job.plan.get("results_revision", 0),
        "synthesis_state": job.plan.get("synthesis_state"),
        "has_synthesis_response": job.plan.get("synthesis_state", {}).get("key") in job.plan.get("synthesis_responses", {}),
        "synthesis_usage": {"calls": len(synthesis_metrics), **{k: sum(m.get(k, 0) for m in synthesis_metrics)
                           for k in ("prompt_tokens", "completion_tokens", "total_tokens")}},
        "run_attempted": job.plan.get("run_attempted", 0),
        "run_successes": job.plan.get("run_successes", 0),
        "run_failures": job.plan.get("run_failures", 0),
        "has_remaining_run": bool(job.plan.get("run_remaining")) or job.stage == "synthesize",
        "status": job.status, "stage": job.stage, "mode": job.plan["mode"],
        "completed_batches": job.completed_batches, "batch_count": total,
        "cache_hits": job.cache_hits, "calls": job.calls, "max_calls": None if job.plan.get("workflow") else job.max_calls,
        "request_interval_seconds": job.plan.get("request_interval_seconds", 60),
        "wait_seconds": max(0, math.ceil(_next_call_at(job.plan) - time.time())),
        "request_pending": started is not None,
        "request_elapsed_seconds": max(0, int(time.time() - started)) if started is not None else 0,
        "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "last_rate_limit_type": latest.get("rate_limit_type") if latest.get("rate_limited") else None,
        "last_usage": {key: latest[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens") if key in latest},
        "progress": (int(100 * job.completed_batches / max(1, total)) if job.plan.get("workflow") else
                     100 if job.status == "done" else min(95, int(95 * job.completed_batches / max(1, total)))),
        "error": job.error, "scope": "full_main_text", "extras_included": False,
        "resumable": job.status in RESUMABLE,
        "create_time": job.create_time.isoformat(), "update_time": job.update_time.isoformat(),
    }


def list_jobs(db, book_id, offset=0, limit=20):
    books.require_book(db, book_id)
    _recover_active_requests(db)
    query = db.query(RolePreparationJob).filter_by(book_id=book_id)
    return {"items": [job_to_dict(job) for job in query.order_by(
        RolePreparationJob.id.desc()).offset(offset).limit(limit)],
        "total": query.count(), "offset": offset, "limit": limit}


def list_roles(db, book_id, offset=0, limit=20):
    books.require_book(db, book_id)
    query = db.query(RoleCharacter).filter_by(book_id=book_id)
    roles = query.order_by(RoleCharacter.id.desc()).offset(offset).limit(limit).all()
    ids = [role.id for role in roles]
    profiles = {}
    # Select metadata only; listing must not deserialize the evidence bank.
    for row in db.query(RoleProfileVersion.role_id, RoleProfileVersion.id,
                        RoleProfileVersion.version).filter(RoleProfileVersion.role_id.in_(ids)).order_by(
                            RoleProfileVersion.version.desc()):
        profiles.setdefault(row.role_id, {"profile_id": row.id, "version": row.version})
    return {"items": [{"role_id": role.id, "role_key": role.role_key,
                       "name": role.canonical_name, "has_profile": role.id in profiles,
                       **profiles.get(role.id, {}), "freshness": "not_checked"} for role in roles],
            "total": query.count(), "offset": offset, "limit": limit}


def _runner(provider, *, cancel_requested=None, **kwargs):
    # Role extraction can take as long as chapter summaries on remote models.
    # A timeout stops the job; it does not automatically send another request.
    kwargs.setdefault("timeout", REQUEST_TIMEOUT_SECONDS)
    kwargs.setdefault("origin", {"provider_id": provider.get("provider_id"), "model": provider.get("model")})
    return ModelRunner(
        lambda messages, **options: llm_service.chat_completion(
            provider, messages, **options, **({"cancel_requested": cancel_requested} if cancel_requested else {})),
        model_key=_provider_hash(provider), **kwargs,
    )


def build_plan(db, book_id, request):
    target = books.resolve_target(db, book_id, request.name)
    scenes, stamp = books.load_scenes(db, book_id, request.batch_chars)
    if not any(name in s.text for s in scenes for name in [target.name, *target.aliases]):
        raise BusinessException("正文中未定位到该人物，请核对名称或选择已确认的称呼", code=4000)
    selected, coverage = select_scenes(target, scenes, request.mode)
    batches = make_batches(selected, request.batch_chars)
    provider = llm_provider_service.get_active_provider_config(db, request.provider_id)
    runner = _runner(provider)
    role = db.query(RoleCharacter).filter_by(book_id=book_id, canonical_name=target.name).first()
    cached = set()
    if role:
        cached = {key for key, in db.query(RoleExtractionCache.cache_key).filter_by(role_id=role.id)}
    minimum = sum(batch_cache_key(target, batch, runner) not in cached for batch in batches) + 1
    plan = {"target": target.model_dump(), "mode": request.mode,
            "request_interval_seconds": request.request_interval_seconds,
            "batch_chars": request.batch_chars, "batch_count": len(batches),
            "scene_version": books.SCENE_VERSION, "coverage": coverage,
            "minimum_calls": minimum}
    return plan, provider, stamp


def preview(db, book_id, request):
    plan, _, _ = build_plan(db, book_id, request)
    coverage = plan["coverage"]
    return {"target": plan["target"], "mode": plan["mode"],
            "batch_count": plan["batch_count"], "minimum_calls": plan["minimum_calls"],
            "max_calls": request.max_calls, "selected_chars": coverage["selected_chars"],
            "request_interval_seconds": plan["request_interval_seconds"],
            "selected_scenes": len(coverage["selected_scene_ids"]),
            "unselected_scenes": len(coverage["unselected_scene_ids"]),
            "scope": "full_main_text", "extras_included": False,
            "recall_guaranteed": False, "model_called": False,
            "segmentation": "overlapping_chapter_windows"}


def create_job(db, book_id, request):
    with _lock:
        _recover_active_requests(db)
        if db.query(RolePreparationJob.id).filter(RolePreparationJob.active_slot == 1).first():
            raise BusinessException("已有角色准备任务，请先暂停或等待完成", code=4090)
        plan, provider, stamp = build_plan(db, book_id, request)
        if plan["minimum_calls"] > request.max_calls:
            raise BusinessException("调用预算不足；请先预览准备范围并调整预算", code=4000)
        target = RoleTarget.model_validate(plan["target"])
        try:
            role = books.find_or_create_role(db, book_id, target)
            job = RolePreparationJob(
                book_id=book_id, role_id=role.id, status="queued", active_slot=1,
                run_token=uuid.uuid4().hex, source_hash=stamp, provider_id=provider["provider_id"],
                model_hash=_provider_hash(provider), prompt_version=prompts.PROMPT_VERSION,
                plan=plan, max_calls=request.max_calls, calls=0, completed_batches=0,
                cache_hits=0, metrics=[],
            )
            db.add(job)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise BusinessException("另一个窗口已启动角色准备，请刷新任务", code=4090) from None
        db.refresh(job)
        return job


def launch(job_id, token):
    with _lock:
        key = (job_id, token)
        if key in _threads:
            return
        thread = threading.Thread(target=run_job, args=key,
                                  name=f"role-preparation-job-{job_id}", daemon=True)
        _threads[key] = thread
        try:
            thread.start()
        except Exception:
            _threads.pop(key, None)
            _finish(job_id, token, "failed", "worker_start_failed")
            raise BusinessException("角色准备线程启动失败，可重试", code=5000) from None


def start_job(db, book_id, request):
    job = create_job(db, book_id, request)
    launch(job.id, job.run_token)
    return job


def request_pause(db, book_id, job_id):
    get_job(db, book_id, job_id)
    db.query(RolePreparationJob).filter_by(id=job_id, book_id=book_id, status="queued").update(
        {"status": "paused", "active_slot": None}, synchronize_session=False)
    db.query(RolePreparationJob).filter_by(id=job_id, book_id=book_id, status="running").update(
        {"status": "pausing"}, synchronize_session=False)
    db.commit()
    db.expire_all()
    return get_job(db, book_id, job_id)


def _assert_sources(db, job):
    if (job.prompt_version != prompts.PROMPT_VERSION
            or job.plan["scene_version"] != books.SCENE_VERSION):
        raise PreparationError("preparation_version_changed")
    if books.source_hash(db, job.book_id) != job.source_hash:
        raise PreparationError("source_changed")
    target = books.resolve_target(db, job.book_id, job.plan["target"]["name"])
    if target.model_dump() != job.plan["target"]:
        raise PreparationError("character_identity_changed")


def resume_job(db, book_id, job_id, request):
    with _lock:
        _recover_active_requests(db)
        job = get_job(db, book_id, job_id)
        if job.plan.get("workflow"):
            from app.services import role_batch_service
            return role_batch_service.resume(db, book_id, job_id, request)
        if job.status not in RESUMABLE:
            raise BusinessException("该任务当前不能续跑", code=4090)
        try:
            _assert_sources(db, job)
            provider = llm_provider_service.get_active_provider_config(db, job.provider_id)
            if _provider_hash(provider) != job.model_hash:
                raise PreparationError("model_changed")
        except PreparationError as exc:
            raise BusinessException(f"任务来源已变化（{exc.code}），请新建准备任务", code=4090) from None
        if db.query(RolePreparationJob.id).filter(RolePreparationJob.active_slot == 1).first():
            raise BusinessException("已有角色准备任务，请先暂停或等待完成", code=4090)
        budget = request.max_calls if request.max_calls is not None else job.max_calls
        if budget < job.calls:
            raise BusinessException("总预算不能小于已经发生的模型调用次数", code=4000)
        job.max_calls = budget
        job.plan = {**job.plan, "request_started_at": None}
        if request.request_interval_seconds is not None:
            job.plan = {**job.plan, "request_interval_seconds": request.request_interval_seconds}
        job.status, job.active_slot, job.error = "queued", 1, None
        job.run_token = uuid.uuid4().hex
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise BusinessException("另一个窗口已启动角色准备", code=4090) from None
        db.refresh(job)
        launch(job.id, job.run_token)
        return job


def _current(db, job_id, token, allow_pausing=False):
    job = db.get(RolePreparationJob, job_id)
    if not job or job.run_token != token:
        raise PreparationError("obsolete_worker")
    if job.status == "pausing" and not allow_pausing:
        raise PreparationError("pause_requested")
    if job.status not in (("running", "pausing") if allow_pausing else ("running",)):
        raise PreparationError("obsolete_worker")
    return job


def _checkpoint(job_id, token):
    with SessionLocal() as db:
        job = _current(db, job_id, token)
        _assert_sources(db, job)


def _next_call_at(plan):
    last = plan.get("last_call_at")
    return max(plan.get("rate_limit_until", 0),
               last + plan.get("request_interval_seconds", 60) if last is not None else 0)


def _provider_next_call_at(db, job):
    # All role jobs using this provider share its cooldown, including a newly
    # selected character. Existing companion/summary callers remain independent.
    last, cooldown = db.query(
        func.max(RolePreparationJob.plan["last_call_at"].as_float()),
        func.max(RolePreparationJob.plan["rate_limit_until"].as_float()),
    ).filter(RolePreparationJob.provider_id == job.provider_id).one()
    return max(_next_call_at(job.plan), cooldown or 0,
               last + job.plan.get("request_interval_seconds", 60) if last is not None else 0)


def _wait_for_call(job_id, token):
    # No transaction is held during the wait. Pause/shutdown is checked even
    # during long provider cooldowns, before reserving any new call budget.
    while True:
        with SessionLocal() as db:
            job = _current(db, job_id, token)
            if not job.plan.get("workflow") and job.calls >= job.max_calls:
                raise PreparationError("model_budget_exhausted")
            deadline = _provider_next_call_at(db, job)
            if deadline > _next_call_at(job.plan):
                job.plan = {**job.plan, "rate_limit_until": deadline}
                db.commit()
            remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.5, remaining))


def _before_call(job_id, token, messages, purpose):
    _wait_for_call(job_id, token)
    with SessionLocal() as db:
        job = _current(db, job_id, token)
        _assert_sources(db, job)
        provider = llm_provider_service.get_active_provider_config(db, job.provider_id)
        if _provider_hash(provider) != job.model_hash:
            raise PreparationError("model_changed")
        if not job.plan.get("workflow") and job.calls >= job.max_calls:
            raise PreparationError("model_budget_exhausted")
        # Commit before sending, so a crash cannot reset already-billed calls.
        reservation = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token, status="running")
        if not job.plan.get("workflow"):
            reservation = reservation.filter(RolePreparationJob.calls < RolePreparationJob.max_calls)
        changed = reservation.update(
                {"calls": RolePreparationJob.calls + 1,
                 "plan": {**job.plan, "last_call_at": time.time(), "request_started_at": time.time()}}, synchronize_session=False)
        if not changed:
            db.expire_all()
            _current(db, job_id, token)
            raise PreparationError("model_budget_exhausted")
        db.commit()


def _after_call(job_id, token, metric):
    with SessionLocal() as db:
        job = _current(db, job_id, token, allow_pausing=True)
        updates = {"metrics": [*job.metrics, metric],
                   "plan": {**job.plan, "request_started_at": None}}
        if metric.get("rate_limited"):
            # No automatic retry on quota/authentication limits. Persist the
            # server cooldown so manual resume/restart cannot bypass it.
            seconds = max(60, metric.get("retry_after_seconds", 60))
            updates["plan"]["rate_limit_until"] = time.time() + seconds
        changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token).filter(
            RolePreparationJob.status.in_(("running", "pausing"))).update(
                updates, synchronize_session=False)
        if not changed:
            raise PreparationError("obsolete_worker")
        db.commit()


def _cancel_requested(job_id, token):
    with SessionLocal() as db:
        row = db.query(RolePreparationJob.status, RolePreparationJob.run_token).filter_by(id=job_id).first()
        return row is None or row.run_token != token or row.status != "running"


def _finish(job_id, token, status, error=None):
    with SessionLocal() as db:
        db.query(RolePreparationJob).filter_by(id=job_id, run_token=token).filter(
            RolePreparationJob.status.in_(ACTIVE)).update(
                {"status": status, "active_slot": None, "error": error}, synchronize_session=False)
        db.commit()


def _save_batch(job_id, token, cache_key, result, completed, cache_hits):
    with SessionLocal() as db:
        job = _current(db, job_id, token, allow_pausing=True)
        _assert_sources(db, job)
        changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token).filter(
            RolePreparationJob.status.in_(("running", "pausing"))).update(
                {"completed_batches": completed, "cache_hits": cache_hits}, synchronize_session=False)
        if not changed:
            raise PreparationError("obsolete_worker")
        cached = db.query(RoleExtractionCache).filter_by(role_id=job.role_id, cache_key=cache_key).first()
        if not cached:
            cached = RoleExtractionCache(book_id=job.book_id, role_id=job.role_id,
                                         cache_key=cache_key, payload=result.model_dump())
            db.add(cached)
        else:
            cached.payload = result.model_dump()
        db.commit()


def _save_synthesis(job_id, token, cache_key, profile):
    with SessionLocal() as db:
        job = _current(db, job_id, token, allow_pausing=True)
        _assert_sources(db, job)
        owned = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token).filter(
            RolePreparationJob.status.in_(("running", "pausing"))).update(
                {"stage": "synthesize"}, synchronize_session=False)
        if not owned:
            raise PreparationError("obsolete_worker")
        cached = db.query(RoleExtractionCache).filter_by(role_id=job.role_id, cache_key=cache_key).first()
        if cached:
            cached.payload = profile.model_dump()
        else:
            db.add(RoleExtractionCache(book_id=job.book_id, role_id=job.role_id,
                                       cache_key=cache_key, kind="profile", payload=profile.model_dump()))
        db.commit()


def run_job(job_id, token):
    """Also callable synchronously in tests; every DB transaction is short-lived."""
    try:
        with SessionLocal() as db:
            claimed = db.query(RolePreparationJob).filter_by(
                id=job_id, run_token=token, status="queued").update({"status": "running"})
            db.commit()
            if not claimed:
                return
            job = db.get(RolePreparationJob, job_id)
            _assert_sources(db, job)
            plan = dict(job.plan)
            target = RoleTarget.model_validate(plan["target"])
            provider = llm_provider_service.get_active_provider_config(db, job.provider_id)
            if _provider_hash(provider) != job.model_hash:
                raise PreparationError("model_changed")
            scenes, _ = books.load_scenes(db, job.book_id, plan["batch_chars"])
            selected, coverage = select_scenes(target, scenes, plan["mode"])
            batches = make_batches(selected, plan["batch_chars"])
            if coverage != plan["coverage"] or len(batches) != plan["batch_count"]:
                raise PreparationError("source_plan_changed")
            cache = {row.cache_key: row.payload for row in db.query(RoleExtractionCache).filter_by(role_id=job.role_id)}
            draft = job.draft_profile
            max_calls = job.max_calls
        model = _runner(
            provider, max_calls=max_calls,
            cancel_requested=lambda: _cancel_requested(job_id, token),
            before_call=lambda messages, purpose: _before_call(job_id, token, messages, purpose),
            after_call=lambda metric: _after_call(job_id, token, metric),
        )
        observations, gaps, hits = {}, [], 0
        for index, batch in enumerate(batches):
            _checkpoint(job_id, token)
            key = batch_cache_key(target, batch, model)
            result = None
            if key in cache:
                try:
                    result = ExtractionBatch.model_validate(cache[key])
                    ground_batch(target, batch, result)
                except (ValidationError, PreparationError):
                    result = None
                else:
                    hits += 1
            if result is None:
                result = extract_role_batch(target, batch, model)
            grounded = ground_batch(target, batch, result)
            for observation in grounded:
                observations[observation.observation_id] = observation
            gaps.extend(result.gaps)
            _save_batch(job_id, token, key, result, index + 1, hits)
        _checkpoint(job_id, token)
        coverage.update(batch_count=len(batches), cache_hits=hits,
                        gaps=list(dict.fromkeys(gaps)), segmentation="overlapping_chapter_windows")
        values = deduplicate_observations(observations.values())
        with SessionLocal() as db:
            job = _current(db, job_id, token)
            changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token, status="running").update(
                {"stage": "synthesize"}, synchronize_session=False)
            if not changed:
                db.expire_all()
                _current(db, job_id, token)
                raise PreparationError("obsolete_worker")
            db.commit()
        if draft:
            profile = RoleProfile.model_validate(draft)
            validate_profile(target, values, profile)
        else:
            profile = synthesize_role(
                target, values, coverage, model, profile_cache=cache,
                on_partial=lambda key, value: _save_synthesis(job_id, token, key, value),
            )
            with SessionLocal() as db:
                job = _current(db, job_id, token, allow_pausing=True)
                _assert_sources(db, job)
                changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token).filter(
                    RolePreparationJob.status.in_(("running", "pausing"))).update(
                        {"draft_profile": profile.model_dump()}, synchronize_session=False)
                if not changed:
                    raise PreparationError("obsolete_worker")
                db.commit()
        _checkpoint(job_id, token)
        with SessionLocal() as db:
            job = _current(db, job_id, token)
            _assert_sources(db, job)
            changed = db.query(RolePreparationJob).filter_by(id=job_id, run_token=token, status="running").update(
                {"status": "done", "stage": "done", "active_slot": None, "draft_profile": None},
                synchronize_session=False)
            if not changed:
                db.expire_all()
                _current(db, job_id, token)
                raise PreparationError("obsolete_worker")
            version = (db.query(func.max(RoleProfileVersion.version)).filter_by(role_id=job.role_id).scalar() or 0) + 1
            db.add(RoleProfileVersion(
                book_id=job.book_id, role_id=job.role_id, job_id=job.id, version=version,
                source_hash=job.source_hash, prompt_version=job.prompt_version,
                target=target.model_dump(), profile=profile.model_dump(),
                observations=[o.model_dump() for o in values], coverage=coverage,
            ))
            db.commit()
    except PreparationError as exc:
        _finish(job_id, token, "paused" if exc.code in ("pause_requested", "model_rate_limited") else "failed",
                None if exc.code == "pause_requested" else exc.code)
    except Exception:
        # Neither provider configuration nor model/source text belongs in diagnostics.
        _finish(job_id, token, "failed", "preparation_failed")
    finally:
        with _lock:
            _threads.pop((job_id, token), None)


def mark_interrupted_jobs(db):
    for job in db.query(RolePreparationJob).filter(RolePreparationJob.status.in_(ACTIVE)):
        job.status, job.active_slot, job.error = "interrupted", None, "process_interrupted"
        job.run_token = uuid.uuid4().hex
    db.commit()


def shutdown():
    with SessionLocal() as db:
        db.query(RolePreparationJob).filter_by(status="queued").update(
            {"status": "interrupted", "active_slot": None}, synchronize_session=False)
        db.query(RolePreparationJob).filter_by(status="running").update(
            {"status": "pausing"}, synchronize_session=False)
        db.commit()
    with _lock:
        threads = list(_threads.values())
    for thread in threads:
        thread.join(timeout=1)


def latest_profile(db, book_id, role_id):
    role = books.get_role(db, book_id, role_id)
    profile = db.query(RoleProfileVersion).filter_by(role_id=role.id).order_by(RoleProfileVersion.version.desc()).first()
    if not profile:
        return {"role_id": role.id, "role_key": role.role_key, "available": False}
    try:
        stale = (books.source_hash(db, book_id) != profile.source_hash
                 or profile.prompt_version != prompts.PROMPT_VERSION
                 or books.resolve_target(db, book_id, role.canonical_name).model_dump() != profile.target)
        if profile.job_id is not None and "results_revision" in profile.coverage:
            origin = db.get(RolePreparationJob, profile.job_id)
            stale = stale or (origin is not None and origin.plan.get("results_revision") != profile.coverage["results_revision"])
    except BusinessException:
        stale = True
    return {"role_id": role.id, "role_key": role.role_key, "available": True,
            "profile_id": profile.id, "version": profile.version, "stale": stale,
            "review_status": "semantic_review_required", "profile": profile.profile,
            "evidence_count": len(profile.observations),
            "coverage": {key: profile.coverage[key] for key in (
                "selected_chars", "recall_guaranteed", "batch_count", "segmentation")}}


def profile_evidence(db, book_id, profile_id, offset=0, limit=20, observation_id=None):
    books.require_book(db, book_id)
    profile = db.query(RoleProfileVersion).filter_by(id=profile_id, book_id=book_id).first()
    if not profile:
        raise BusinessException("角色资料版本不存在", code=4040)
    observations = profile.observations
    if observation_id is not None:
        observations = [item for item in observations if item["observation_id"] == observation_id]
    return {"profile_id": profile.id, "version": profile.version,
            "items": observations[offset:offset + limit],
            "total": len(observations), "offset": offset, "limit": limit,
            "review_status": "semantic_review_required"}
