"""Pure role-preparation pipeline. No database, library access or background jobs.

Callers provide scenes and a model callable. The synthetic benchmark is the first
consumer; production book/session APIs are deliberately separate from this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any, Callable, Literal

from pydantic import ValidationError

from app.schemas.role_preparation import (
    ExtractionBatch, ParagraphExtractionBatch, ParagraphReference, GroundedObservation, RoleProfile, RoleScene, RoleTarget,
    VerifiedQuote,
)
from app.services import role_preparation_prompt as prompts
from app.services.role_quote_service import locate_quote, QuoteMatchError
from app.services.role_paragraph_service import source_paragraphs, resolve_opening, PARAGRAPH_VERSION


class PreparationError(Exception):
    """Safe diagnostic code, never includes model responses or credentials."""

    def __init__(self, code: str, repair_hint: str = ""):
        self.code = code
        self.repair_hint = repair_hint
        super().__init__(code)


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass
class ModelRunner:
    complete: Callable[..., dict[str, Any]]
    model_key: str
    max_calls: int | None = 32
    max_request_chars: int = 60000
    max_tokens: int = 4096
    timeout: int = 60
    max_repairs: int = 1
    metrics: list[dict[str, Any]] = field(default_factory=list)
    origin: dict[str, Any] = field(default_factory=dict)
    before_call: Callable[[list[dict[str, str]], str], None] | None = None
    after_call: Callable[[dict[str, Any]], None] | None = None

    def __post_init__(self):
        if (self.max_calls is not None and self.max_calls < 1) or self.max_repairs not in (0, 1) or not self.model_key.strip() or min(
            self.max_request_chars, self.max_tokens, self.timeout
        ) < 1:
            raise ValueError("invalid model budget")

    def call(self, messages: list[dict[str, str]], purpose: str) -> str:
        if self.max_calls is not None and len(self.metrics) >= self.max_calls:
            raise PreparationError("model_budget_exhausted")
        input_chars = sum(len(m["content"]) for m in messages)
        if input_chars > self.max_request_chars:
            raise PreparationError("request_too_large")
        if self.before_call:
            self.before_call(messages, purpose)
        record = {"purpose": purpose, "input_chars": input_chars,
                  "output_chars": 0, "elapsed_ms": 0, "ok": False}
        self.metrics.append(record)
        started = time.monotonic()
        try:
            try:
                result = self.complete(
                    messages, max_tokens=self.max_tokens, temperature=0.1,
                    timeout=self.timeout,
                )
            except Exception:
                raise PreparationError("model_request_failed") from None
            if isinstance(result, dict):
                record["http_status"] = result.get("status_code")
                usage = result.get("usage") or {}
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    if type(usage.get(key)) is int and usage[key] >= 0:
                        record[key] = usage[key]
                if result.get("error_code") == "cancelled":
                    record["cancelled"] = True
                    raise PreparationError("pause_requested")
            if isinstance(result, dict) and result.get("status_code") == 429:
                record["rate_limited"] = True
                category = result.get("rate_limit_type")
                record["rate_limit_type"] = category if category in {
                    "requests", "tokens", "requests_and_tokens"} else "unknown"
                record["retry_after_seconds"] = result.get("retry_after_seconds") or 60
                raise PreparationError("model_rate_limited")
            if isinstance(result, dict) and result.get("error_code") == "timeout":
                raise PreparationError("model_request_timeout")
            if not isinstance(result, dict) or result.get("error"):
                raise PreparationError("model_request_failed")
            if result.get("finish_reason") == "length":
                raise PreparationError("model_output_truncated")
            content = result.get("content")
            if not isinstance(content, str) or not content.strip():
                raise PreparationError("empty_model_response")
            record.update(output_chars=len(content), ok=True)
            return content
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000)
            if self.after_call:
                self.after_call(dict(record))


def _parse(content: str, schema):
    raw = content.strip()
    # Ignore a complete leading reasoning block, never an unfinished one or
    # objects within it. Accept one JSON object wrapped in prose/code fences.
    if raw.startswith("<think>"):
        closing = raw.find("</think>")
        if closing < 0:
            raise PreparationError("invalid_model_json")
        raw = raw[closing + len("</think>"):].strip()
    try:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            if start < 0 or any(c in raw[:start] for c in "[]}"):
                raise ValueError("missing object")
            value, end = json.JSONDecoder().raw_decode(raw, start)
            if any(c in raw[end:] for c in "{}[]"):
                raise ValueError("multiple or incomplete objects")
    except (ValueError, TypeError):
        raise PreparationError("invalid_model_json") from None
    try:
        return schema.model_validate(value)
    except ValidationError as exc:
        # Field paths and fixed error types guide repair without forwarding
        # rejected quotes, claims, names or Pydantic's input/error messages.
        allowed = {"character", "observations", "gaps", "kind", "claim", "attribution", "knowledge",
                   "speaker", "evidence", "scene_id", "quote", "identity", "current_state",
                   "behavior_rules", "expression", "relationships", "known_facts", "beliefs",
                   "voice_evidence_ids", "limitations", "text", "evidence_ids", "situation",
                   "response", "exceptions", "paragraph_id", "opening"}
        hints = []
        for item in exc.errors(include_input=False, include_context=False, include_url=False)[:4]:
            path = "/".join(str(p) if isinstance(p, int) or p in allowed else "field" for p in item["loc"])
            hints.append(f"{path or 'root'}: {item['type']}")
        raise PreparationError("invalid_model_schema", "；".join(hints)) from None


def _structured(model, messages, schema, validate, purpose, on_response=None):
    # One repair at most. Network errors are not automatically retried or billed twice.
    for attempt in range(model.max_repairs + 1):
        content = model.call(messages, purpose)
        if on_response:
            on_response(content)
        try:
            result = _parse(content, schema)
            validate(result)
            return result
        except PreparationError as exc:
            if on_response:
                on_response(content, exc)
            if attempt >= model.max_repairs:
                raise
            messages = [*messages, {"role": "user", "content": (
                f"上次结果未通过校验（{exc.code}）。重新依据原始输入输出完整 JSON；"
                f"字段检查：{exc.repair_hint or '只输出一个完整 JSON 对象'}。"
                "遵守 schema 的列表数量、文本长度和枚举限制；列表过多时合并重复观测并择要保留。"
                "证据只引用输入已有的段落或观测编号，不补写原文；表达开头只需连续短片段。"
            )}]
    raise AssertionError("unreachable")


def source_scenes(target: RoleTarget, scenes: list[RoleScene]) -> list[RoleScene]:
    ids: set[str] = set()
    for scene in scenes:
        if scene.book_key != target.book_key:
            raise PreparationError("cross_book_scene")
        if scene.scene_id in ids:
            raise PreparationError("duplicate_scene_id")
        ids.add(scene.scene_id)
    sources = [s for s in scenes if s.kind == "main" and s.split == "source"]
    holdouts = [s for s in scenes if s.kind == "main" and s.split == "holdout"]
    for scene in sources:
        for held in holdouts:
            if scene.chapter_number == held.chapter_number and max(
                scene.char_start, held.char_start
            ) < min(scene.char_start + len(scene.text), held.char_start + len(held.text)):
                raise PreparationError("holdout_overlap")
    return sorted(sources, key=lambda s: (s.chapter_number, s.char_start, s.scene_id))


def select_scenes(
    target: RoleTarget, scenes: list[RoleScene],
    mode: Literal["targeted", "full"] = "targeted",
) -> tuple[list[RoleScene], dict[str, Any]]:
    if mode not in {"targeted", "full"}:
        raise ValueError("unknown preparation mode")
    available = source_scenes(target, scenes)
    names = [target.name, *target.aliases]
    hits = {s.scene_id for s in available if any(n in s.text for n in names)}
    selected = set(hits)
    if mode == "full":
        selected = {s.scene_id for s in available}
    else:
        # Expand immediate neighboring scenes in the same chapter. Cross-chapter
        # pronouns and undiscovered aliases remain a measurable recall limitation.
        for index, scene in enumerate(available):
            if scene.scene_id not in hits:
                continue
            for neighbor in available[max(0, index - 1):index + 2]:
                if neighbor.chapter_number == scene.chapter_number:
                    selected.add(neighbor.scene_id)
    picked = [s for s in available if s.scene_id in selected]
    return picked, {
        "mode": mode,
        "scope": "main_source_scenes_only",
        "available_scene_ids": [s.scene_id for s in available],
        "selected_scene_ids": [s.scene_id for s in picked],
        "unselected_scene_ids": [s.scene_id for s in available if s.scene_id not in selected],
        "selected_chars": sum(len(s.text) for s in picked),
        "recall_guaranteed": False,
    }


def make_batches(scenes: list[RoleScene], max_chars: int = 8000) -> list[list[RoleScene]]:
    if max_chars < 1:
        raise ValueError("max_chars must be positive")
    batches: list[list[RoleScene]] = []
    batch: list[RoleScene] = []
    count = 0
    for scene in scenes:
        if len(scene.text) > max_chars:
            # Never silently truncate or split away dialogue attribution context.
            raise PreparationError("scene_too_large")
        if batch and count + len(scene.text) > max_chars:
            batches.append(batch)
            batch, count = [], 0
        batch.append(scene)
        count += len(scene.text)
    if batch:
        batches.append(batch)
    return batches


def ground_batch(
    target: RoleTarget, scenes: list[RoleScene], batch: ExtractionBatch,
) -> list[GroundedObservation]:
    source_scenes(target, scenes)
    if any(s.kind != "main" or s.split != "source" for s in scenes):
        raise PreparationError("scene_out_of_scope")
    if batch.character != target.name:
        raise PreparationError("wrong_character")
    source_map = {s.scene_id: s for s in scenes}
    paragraphs = {p["paragraph_id"]: p for p in source_paragraphs(scenes)}
    grounded: list[GroundedObservation] = []
    for item in batch.observations:
        if item.kind == "speech" and (
            item.speaker not in [target.name, *target.aliases]
            or item.attribution != "self_statement"
        ):
            raise PreparationError("invalid_speech_attribution")
        quotes = []
        for ref in item.evidence:
            if isinstance(ref, ParagraphReference):
                paragraph = paragraphs.get(ref.paragraph_id)
                if paragraph is None:
                    raise PreparationError("unknown_paragraph_reference")
                scene = source_map[paragraph["scene_id"]]
                start, end, method, warning = resolve_opening(paragraph["text"], ref.opening)
                quotes.append(VerifiedQuote(
                    scene_id=scene.scene_id, chapter_number=scene.chapter_number,
                    source_hash=_hash(scene.model_dump(exclude={"split"})),
                    quote=paragraph["text"][start:end], char_start=paragraph["char_start"] + start,
                    char_end=paragraph["char_start"] + end, match_method=method,
                    paragraph_id=ref.paragraph_id, requested_quote=ref.opening, location_warning=warning,
                ))
                continue
            scene = source_map.get(ref.scene_id)
            if scene is None:
                raise PreparationError("unknown_scene_reference")
            try:
                start, end, method = locate_quote(scene.text, ref.quote)
            except QuoteMatchError as exc:
                raise PreparationError(str(exc)) from None
            quotes.append(VerifiedQuote(
                scene_id=scene.scene_id, chapter_number=scene.chapter_number,
                source_hash=_hash(scene.model_dump(exclude={"split"})),
                quote=scene.text[start:end], char_start=scene.char_start + start,
                char_end=scene.char_start + end, match_method=method,
                requested_quote=ref.quote if method != "exact" else None,
            ))
        payload = item.model_dump(exclude={"evidence"})
        observation_id = "obs_" + _hash({
            "target": target.model_dump(), "observation": payload,
            "evidence": [q.model_dump(exclude={"match_method", "requested_quote", "paragraph_id", "location_warning"}) for q in quotes],
        })[:24]
        grounded.append(GroundedObservation(
            observation_id=observation_id, **payload, evidence=quotes,
        ))
    return list({o.observation_id: o for o in grounded}.values())


def validate_profile(target, observations, profile):
    if profile.character != target.name:
        raise PreparationError("wrong_character")
    by_id = {o.observation_id: o for o in observations}
    sections = (profile.identity, profile.current_state, profile.behavior_rules, profile.expression,
                profile.relationships, profile.known_facts, profile.beliefs)
    for section in sections:
        for statement in section:
            if any(ref not in by_id for ref in statement.evidence_ids):
                raise PreparationError("unknown_observation_reference")
            if any(by_id[ref].knowledge == "reader_only" for ref in statement.evidence_ids):
                raise PreparationError("reader_only_profile_reference")
    for statement in profile.known_facts:
        if any(by_id[ref].knowledge not in {"experienced", "learned"}
               or by_id[ref].attribution == "inference" for ref in statement.evidence_ids):
            raise PreparationError("unsupported_character_knowledge")
    for statement in profile.beliefs:
        if any(by_id[ref].knowledge != "believed" for ref in statement.evidence_ids):
            raise PreparationError("unsupported_character_belief")
    for ref in profile.voice_evidence_ids:
        if (ref not in by_id or by_id[ref].kind != "speech"
                or by_id[ref].knowledge == "reader_only"):
            raise PreparationError("invalid_voice_reference")


def deduplicate_observations(observations):
    """Do not count the same physical quote twice just because windows overlap."""
    unique = {}
    for observation in observations:
        key = _hash({
            "claim": observation.claim, "kind": observation.kind,
            "attribution": observation.attribution, "knowledge": observation.knowledge,
            "speaker": observation.speaker,
            "quotes": sorted((q.chapter_number, q.char_start, q.char_end, q.quote)
                             for q in observation.evidence),
        })
        unique.setdefault(key, observation)
    return list(unique.values())


def batch_cache_key(target, scenes, model):
    return _hash({"prompt": prompts.PROMPT_VERSION, "extraction": PARAGRAPH_VERSION, "model": model.model_key,
                  "max_tokens": model.max_tokens, "target": target.model_dump(),
                  "scenes": [s.model_dump() for s in scenes]})


def extract_role_batch(target, scenes, model):
    return _structured(
        model, prompts.extraction_messages(target, scenes), ParagraphExtractionBatch,
        lambda result: ground_batch(target, scenes, result), "extract",
    )


def _profile_refs(profile):
    references = set(profile.voice_evidence_ids)
    for section in (profile.identity, profile.current_state, profile.behavior_rules,
                    profile.expression, profile.relationships, profile.known_facts, profile.beliefs):
        for item in section:
            references.update(item.evidence_ids)
    return references


def _message_size(messages):
    return sum(len(m["content"]) for m in messages)


def _partition(items, message_builder, budget):
    groups, current = [], []
    for item in items:
        trial = [*current, item]
        if _message_size(message_builder(trial)) <= budget:
            current = trial
        else:
            if not current or _message_size(message_builder([item])) > budget:
                raise PreparationError("request_too_large")
            groups.append(current)
            current = [item]
    if current:
        groups.append(current)
    return groups


def synthesize_role(target, observations, coverage, model, profile_cache=None, on_partial=None,
                    response_cache=None, on_response=None, on_progress=None, group_chars=None):
    # Lazy import keeps the pure pipeline usable without application/AI dependencies.
    from app.services.role_synthesis_service import synthesize
    return synthesize(target, observations, coverage, model, profile_cache, on_partial,
                      response_cache, on_response, on_progress, group_chars)


@dataclass
class PreparedRole:
    target: RoleTarget
    profile: RoleProfile
    observations: list[GroundedObservation]
    coverage: dict[str, Any]
    metrics: list[dict[str, Any]]

    def as_dict(self):
        return {
            "prompt_version": prompts.PROMPT_VERSION,
            "target": self.target.model_dump(), "profile": self.profile.model_dump(),
            "observations": [o.model_dump() for o in self.observations],
            "coverage": self.coverage, "metrics": self.metrics,
            "review_status": "semantic_review_required",
        }


def prepare_role(
    target: RoleTarget, scenes: list[RoleScene], model: ModelRunner,
    mode: Literal["targeted", "full"] = "targeted", max_batch_chars: int = 8000,
    batch_cache: dict[str, dict[str, Any]] | None = None,
) -> PreparedRole:
    selected, coverage = select_scenes(target, scenes, mode)
    batches = make_batches(selected, max_batch_chars)
    started = len(model.metrics)
    cache = batch_cache if batch_cache is not None else {}
    work = []
    for batch in batches:
        key = batch_cache_key(target, batch, model)
        work.append((key, batch))
    minimum_calls = sum(key not in cache for key, _ in work) + bool(batches)
    if model.max_calls is not None and minimum_calls > model.max_calls - started:
        raise PreparationError("model_budget_exhausted")
    observations: dict[str, GroundedObservation] = {}
    gaps: list[str] = []
    cache_hits = 0
    for key, batch in work:
        validate = lambda result: ground_batch(target, batch, result)
        if key in cache:
            try:
                result = ExtractionBatch.model_validate(cache[key])
                validate(result)
            except (ValidationError, PreparationError):
                raise PreparationError("invalid_cached_batch") from None
            cache_hits += 1
        else:
            result = extract_role_batch(target, batch, model)
            cache[key] = result.model_dump()
        for observation in ground_batch(target, batch, result):
            observations[observation.observation_id] = observation
        gaps.extend(result.gaps)
    coverage.update(batch_count=len(batches), cache_hits=cache_hits,
                    gaps=list(dict.fromkeys(gaps)))
    values = deduplicate_observations(observations.values())
    profile = synthesize_role(target, values, coverage, model)
    return PreparedRole(target, profile, values, coverage, model.metrics[started:])


def actor_context(prepared: PreparedRole) -> dict[str, Any]:
    """Do not forward the full observer evidence bank or reader-only facts to chat."""
    validate_profile(prepared.target, prepared.observations, prepared.profile)
    by_id = {o.observation_id: o for o in prepared.observations}
    return {
        "character": prepared.target.name,
        # Free-form preparation gaps may themselves mention reader-only secrets.
        # Keep those observer diagnostics in the report, not in the actor prompt.
        "profile": prepared.profile.model_dump(exclude={"limitations"}),
        "voice_samples": [
            {"quotes": [q.quote for q in by_id[ref].evidence if q.match_method != "paragraph"],
             "attribution": "model_assigned_review_required"}
            for ref in prepared.profile.voice_evidence_ids
            if any(q.match_method != "paragraph" for q in by_id[ref].evidence)
        ],
        "scope_limitations": {
            "complete_character_recall": False,
            "unselected_scene_count": len(prepared.coverage["unselected_scene_ids"]),
            "gap_count": len(prepared.coverage["gaps"]),
            "limitation_count": len(prepared.profile.limitations),
        },
    }
