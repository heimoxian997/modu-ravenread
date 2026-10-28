"""Previewable, resumable role synthesis; raw attempts stay in role storage."""

from pydantic import ValidationError

from app.schemas.role_preparation import RoleProfile
from app.services import role_preparation_prompt as prompts
from app.services.role_preparation_service import (
    PreparationError, _hash, _message_size, _partition, _parse, _structured,
    _profile_refs, validate_profile,
)

GROUP_CHARS = 32000
MAX_OUTPUT_TOKENS = 16384
FORMAT_VERSION = "compact_synthesis_v1"


def cache_key(model, messages):
    # Validated work belongs to this input, independent of the next provider.
    return _hash({"phase": "role_profile_group", "prompt": prompts.PROMPT_VERSION, "messages": messages})


def legacy_cache_key(model, messages):
    return _hash({"phase": "role_profile_group", "prompt": prompts.PROMPT_VERSION,
                  "model": model.model_key, "max_tokens": model.max_tokens, "messages": messages})


def restore_profile(target, allowed, value):
    aliases = {f"E{index}": o.observation_id for index, o in enumerate(allowed, 1)}
    for section in (value.identity, value.current_state, value.behavior_rules, value.expression,
                    value.relationships, value.known_facts, value.beliefs):
        for statement in section:
            statement.evidence_ids = [aliases.get(ref, ref) for ref in statement.evidence_ids]
    value.voice_evidence_ids = [aliases.get(ref, ref) for ref in value.voice_evidence_ids]
    by_id = {o.observation_id: o for o in allowed}
    unsupported = [s for s in value.known_facts if all(r in by_id for r in s.evidence_ids)
                   and any(by_id[r].knowledge not in {"experienced", "learned"}
                           or by_id[r].attribution == "inference" for r in s.evidence_ids)]
    if unsupported:
        value.known_facts = [s for s in value.known_facts if s not in unsupported]
        notice = f"有 {len(unsupported)} 条候选已知事实缺少合规的知情依据，未采用；请核对来源观测。"
        if len(value.limitations) < 12:
            value.limitations.append(notice)
        else:
            value.limitations[-1] = notice + " " + value.limitations[-1][:599-len(notice)]
    validate_profile(target, allowed, value)
    return value


def validate_response(target, allowed, raw):
    return restore_profile(target, allowed, _parse(raw, RoleProfile))


def _cached(target, allowed, cache, key):
    try:
        value = RoleProfile.model_validate(cache[key])
        validate_profile(target, allowed, value)
        return value
    except (KeyError, ValidationError, PreparationError):
        return None


def _groups(target, observations, coverage, model, group_chars):
    budget = max(1, model.max_request_chars - (500 if model.max_repairs else 0))
    budget = min(budget, group_chars) if group_chars else budget
    return _partition(observations, lambda g: prompts.synthesis_messages(target, g, coverage), budget), budget


def preview(target, observations, coverage, model, cache=None, group_chars=GROUP_CHARS, origins=None):
    groups, _ = _groups(target, observations, coverage, model, group_chars)
    rows = []
    for index, group in enumerate(groups, 1):
        messages = prompts.synthesis_messages(target, group, coverage)
        rows.append({"number": index, "observation_count": len(group), "input_chars": _message_size(messages),
                     "cached": _cached(target, group, cache or {}, cache_key(model, messages)) is not None,
                     "origin": (origins or {}).get(cache_key(model, messages))})
    return {"group_count": len(rows), "cached_groups": sum(r["cached"] for r in rows), "groups": rows,
            "observation_count": len(observations), "initial_input_chars": sum(r["input_chars"] for r in rows),
            "merge_required": len(rows) > 1, "model_called": False}


def synthesize(target, observations, coverage, model, profile_cache=None, on_partial=None,
               response_cache=None, on_response=None, on_progress=None, group_chars=None):
    if not observations:
        return RoleProfile(character=target.name, limitations=["未得到有原文依据的人物观测，不能视为可用角色资料。"])
    cache = profile_cache if profile_cache is not None else {}
    responses = response_cache if response_cache is not None else {}
    groups, budget = _groups(target, observations, coverage, model, group_chars)
    completed = set()
    for index, group in enumerate(groups, 1):
        if _cached(target, group, cache, cache_key(model, prompts.synthesis_messages(target, group, coverage))):
            completed.add(index)
    state = {"group_total": len(groups), "group_completed": len(completed), "phase": "group", "level": 0}

    def emit(**fields):
        state.update(fields, group_completed=len(completed))
        if on_progress:
            on_progress(dict(state))

    emit(status="planned", current=0, total=len(groups))

    def build(messages, allowed, phase, index, total, level):
        key = cache_key(model, messages)
        emit(phase=phase, level=level, current=index, total=total, key=key,
             input_chars=_message_size(messages), error=None, hint=None, status="pending")
        def saved(raw, error=None):
            record = {"raw_content": raw, "error": error.code if error else None,
                      "hint": error.repair_hint if error else None, "reuse": True,
                      "allowed_ids": [o.observation_id for o in allowed],
                      "origin": responses.get(key, {}).get("origin", model.origin)}
            responses[key] = record
            if on_response:
                on_response(key, record)
        try:
            result = _cached(target, allowed, cache, key)
            hit = result is not None
            if result is None:
                prior = responses.get(key, {})
                if prior.get("reuse", True) and prior.get("raw_content"):
                    result = validate_response(target, allowed, prior["raw_content"])
                else:
                    emit(status="requesting")
                    complete = model.complete
                    def capture(*args, **kwargs):
                        response = complete(*args, **kwargs)
                        if isinstance(response, dict) and isinstance(response.get("content"), str):
                            # Explicit re-generation records the new producer.
                            responses[key] = {**responses.get(key, {}), "origin": dict(model.origin)}
                            saved(response["content"])
                        return response
                    model.complete = capture
                    try:
                        result = _structured(model, messages, RoleProfile,
                                             lambda v: restore_profile(target, allowed, v), "synthesize",
                                             on_response=lambda raw, error=None: saved(raw, error) if error else None)
                    finally:
                        model.complete = complete
                cache[key] = result.model_dump()
                if on_partial:
                    on_partial(key, result)
            if phase == "group":
                completed.add(index)
            emit(status="cached" if hit else "saved")
            return result
        except PreparationError as exc:
            prior = responses.get(key, {})
            if exc.code != "synthesis_run_complete" and prior.get("raw_content") and prior.get("error") != exc.code:
                saved(prior["raw_content"], exc)
            emit(status="pending" if exc.code == "synthesis_run_complete" else "failed",
                 error=None if exc.code == "synthesis_run_complete" else exc.code, hint=exc.repair_hint or None)
            raise

    profiles = [build(prompts.synthesis_messages(target, group, coverage), group, "group", index, len(groups), 0)
                for index, group in enumerate(groups, 1)]
    if len(groups) > 1:
        coverage.update(grouped_synthesis=True, synthesis_group_count=len(groups))
    def merge_input(group, *, compact=False):
        refs = set().union(*(_profile_refs(p) for p in group))
        allowed = [o for o in observations if o.observation_id in refs]
        return prompts.merge_messages(target, group, allowed, coverage, compact=compact), allowed
    level = 0
    while len(profiles) > 1:
        level += 1
        compact = False
        try:
            packets = _partition(profiles, lambda g: merge_input(g)[0], budget)
        except PreparationError as exc:
            if exc.code != "request_too_large":
                raise
            packets = [[profile] for profile in profiles]
        if len(packets) == len(profiles):
            # Keep existing groups/merge caches; only stalled later layers use
            # the compact catalog and the runner's independent request limit.
            compact = True
            merge_budget = max(1, model.max_request_chars - (500 if model.max_repairs else 0))
            packets = _partition(profiles, lambda g: merge_input(g, compact=True)[0], merge_budget)
            if len(packets) == len(profiles):
                raise PreparationError("synthesis_cannot_reduce", "汇总资料仍无法放入同一请求，已保存结果保留；请检查汇总输入限制。")
        emit(phase="merge", level=level, current=0, total=len(packets), status="planned",
             input_chars=None, key=None, error=None, hint=None, compact=compact)
        profiles = [packet[0] if len(packet) == 1 else build(*merge_input(packet, compact=compact), "merge", index, len(packets), level)
                    for index, packet in enumerate(packets, 1)]
    validate_profile(target, observations, profiles[0])
    emit(status="done", error=None, hint=None)
    return profiles[0]
