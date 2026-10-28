"""Synthetic preparation contracts, evidence boundaries and benchmark isolation."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.schemas.role_preparation import ExtractionBatch, RoleProfile, RoleScene, RoleTarget
from app.services import role_preparation_prompt as prompts
from app.services.role_benchmark_service import comparison_plan, corpus_scenes, run_comparison
from app.services.role_preparation_service import (
    ModelRunner, PreparationError, actor_context, deduplicate_observations, ground_batch, make_batches,
    prepare_role, select_scenes, synthesize_role, validate_profile,
)


BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture
def target():
    return RoleTarget(book_key="synthetic_archive", name="岑砚", aliases=["岑先生"])


def scene(text="岑砚说：‘先给我看凭据。’", **kwargs):
    return RoleScene(scene_id="s1", book_key="synthetic_archive", chapter_number=1,
                     text=text, **kwargs)


def extraction(quote="先给我看凭据。", kind="speech", knowledge="not_applicable", **changes):
    observation = {"kind": kind, "claim": "先核对依据再判断", "attribution": "self_statement",
                   "knowledge": knowledge, "speaker": "岑砚",
                   "evidence": [{"scene_id": "s1", "quote": quote}]}
    observation.update(changes)
    return ExtractionBatch(character="岑砚", observations=[observation])


def paragraph_extraction(paragraph_id="s1:p1"):
    value = extraction().model_dump()
    value["observations"][0]["evidence"] = [{"paragraph_id": paragraph_id, "opening": "先给我看凭据。"}]
    return value


def corpus():
    return json.loads((BACKEND / "tests/fixtures/role_chat_synthetic.json").read_text(encoding="utf-8"))


class ScriptedModel:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.messages = []

    def __call__(self, messages, **kwargs):
        self.messages.append(messages)
        result = next(self.outputs)
        if callable(result):
            result = result(messages)
        if isinstance(result, dict) and "content" in result:
            return result
        return {"content": json.dumps(result, ensure_ascii=False)}


def profile_result(messages):
    data = json.loads(messages[1]["content"])
    first = data["observations"][0]
    reference = first[0] if isinstance(first, list) else first["observation_id"]
    return {"character": data["target"]["name"],
            "behavior_rules": [{"situation": "陌生人提出无凭指控", "response": "先追问依据",
                                "evidence_ids": [reference]}],
            "voice_evidence_ids": [reference], "limitations": ["末态资料不足。"]}


def test_full_and_targeted_exclude_extra_and_heldout_and_report_recall_gap():
    data = corpus()
    selected_target = RoleTarget(book_key=data["book_key"], **{
        k: data["targets"][0][k] for k in ("name", "aliases")})
    scenes = corpus_scenes(data)
    targeted, narrow = select_scenes(selected_target, scenes)
    full, broad = select_scenes(selected_target, scenes, "full")
    assert "source_02" in narrow["selected_scene_ids"]  # Same-chapter pronoun expansion.
    assert "source_04" not in narrow["selected_scene_ids"]  # Undiscovered alias.
    assert "source_04" in broad["selected_scene_ids"]
    assert not broad["recall_guaranteed"]
    assert len(full) > len(targeted)
    assert all(s.kind == "main" and s.split == "source" for s in full)


def test_wrong_book_duplicate_scene_and_overlapping_holdout_are_rejected(target):
    for sources, code in (
        ([scene().model_copy(update={"book_key": "another_book"})], "cross_book_scene"),
        ([scene(), scene()], "duplicate_scene_id"),
        ([scene(), scene().model_copy(update={"scene_id": "held", "split": "holdout"})],
         "holdout_overlap"),
    ):
        with pytest.raises(PreparationError, match=code):
            select_scenes(target, sources, "full")


def test_batching_preserves_complete_scene_and_rejects_oversize():
    scenes = [scene(), scene().model_copy(update={"scene_id": "s2", "chapter_number": 2})]
    batches = make_batches(scenes, len(scenes[0].text))
    assert batches == [[scenes[0]], [scenes[1]]]
    with pytest.raises(PreparationError, match="scene_too_large"):
        make_batches(scenes, len(scenes[0].text) - 1)


def test_quote_offsets_and_semantic_attribution_are_separate(target):
    source = scene(char_start=123)
    observation = ground_batch(target, [source], extraction())[0]
    quote = observation.evidence[0]
    assert quote.quote_verified
    assert source.text[quote.char_start - 123:quote.char_end - 123] == quote.quote
    assert observation.semantic_attribution == "model_assigned"
    assert observation.observation_id == ground_batch(target, [source], extraction())[0].observation_id
    changed = source.model_copy(update={"text": source.text + "雨停了。"})
    assert observation.observation_id != ground_batch(target, [changed], extraction())[0].observation_id


def test_overlapping_windows_do_not_count_same_physical_quote_twice(target):
    first = scene(text="窗外有雨。岑砚说：‘先给我看凭据。’", char_start=100)
    second = first.model_copy(update={"scene_id": "s2", "char_start": 105, "text": first.text[5:]})
    a = ground_batch(target, [first], extraction())[0]
    b_result = extraction(evidence=[{"scene_id": "s2", "quote": "先给我看凭据。"}])
    b = ground_batch(target, [second], b_result)[0]
    assert a.observation_id != b.observation_id
    assert deduplicate_observations([a, b]) == [a]


@pytest.mark.parametrize("quote,text,code", [
    ("并不存在的台词", "岑砚只是点头。", "quote_not_found"),
    ("先看凭据", "先看凭据。先看凭据。", "quote_ambiguous"),
    ("先查凭据", "岑砚说先看凭据。", "quote_not_found"),
])
def test_quotes_must_exist_unambiguously_and_without_rewriting(target, quote, text, code):
    with pytest.raises(PreparationError, match=code):
        ground_batch(target, [scene(text)], extraction(quote))


def test_evidence_cannot_reference_other_batch_extra_or_holdout(target):
    result = extraction(evidence=[{"scene_id": "unseen", "quote": "先给我看凭据。"}])
    with pytest.raises(PreparationError, match="unknown_scene_reference"):
        ground_batch(target, [scene()], result)
    for update in ({"kind": "extra"}, {"split": "holdout"}):
        with pytest.raises(PreparationError, match="scene_out_of_scope"):
            ground_batch(target, [scene().model_copy(update=update)], extraction())


def test_wrong_character_and_other_speaker_are_rejected(target):
    with pytest.raises(PreparationError, match="wrong_character"):
        ground_batch(target, [scene()], extraction().model_copy(update={"character": "祝遥"}))
    with pytest.raises(PreparationError, match="invalid_speech_attribution"):
        ground_batch(target, [scene()], extraction(speaker="祝遥"))


@pytest.mark.parametrize("knowledge,attribution", [
    ("reader_only", "narrator"), ("unknown", "narrator"),
    ("believed", "self_statement"), ("not_applicable", "self_statement"),
    ("learned", "inference"),
])
def test_nonknowledge_cannot_be_promoted_to_known_facts(target, knowledge, attribution):
    observed = ground_batch(target, [scene()], extraction(
        kind="belief", knowledge=knowledge, attribution=attribution))
    profile = RoleProfile(character=target.name, known_facts=[{
        "text": "知道密信位置", "evidence_ids": [observed[0].observation_id]}])
    with pytest.raises(PreparationError):
        validate_profile(target, observed, profile)


def test_reader_only_cannot_be_laundered_through_identity_or_voice(target):
    observed = ground_batch(target, [scene()], extraction(knowledge="reader_only"))
    reference = observed[0].observation_id
    for profile in (
        RoleProfile(character=target.name, identity=[{"text": "秘密", "evidence_ids": [reference]}]),
        RoleProfile(character=target.name, voice_evidence_ids=[reference]),
    ):
        with pytest.raises(PreparationError):
            validate_profile(target, observed, profile)


def test_known_beliefs_and_voice_keep_distinct_evidence_types(target):
    learned = ground_batch(target, [scene()], extraction(kind="action", knowledge="learned"))[0]
    belief = ground_batch(target, [scene()], extraction(kind="belief", knowledge="believed"))[0]
    voice = ground_batch(target, [scene()], extraction())[0]
    profile = RoleProfile(character=target.name,
                          known_facts=[{"text": "获知之事", "evidence_ids": [learned.observation_id]}],
                          beliefs=[{"text": "只是猜测", "evidence_ids": [belief.observation_id]}],
                          voice_evidence_ids=[voice.observation_id])
    validate_profile(target, [learned, belief, voice], profile)
    profile.voice_evidence_ids = [learned.observation_id]
    with pytest.raises(PreparationError, match="invalid_voice_reference"):
        validate_profile(target, [learned, belief, voice], profile)
    profile.voice_evidence_ids = ["invented"]
    with pytest.raises(PreparationError, match="invalid_voice_reference"):
        validate_profile(target, [learned, belief, voice], profile)


def test_profile_cannot_invent_evidence_ids(target):
    observed = ground_batch(target, [scene()], extraction())
    profile = RoleProfile(character=target.name, expression=[{
        "text": "短句追问依据", "evidence_ids": ["invented_reference"]}])
    with pytest.raises(PreparationError, match="unknown_observation_reference"):
        validate_profile(target, observed, profile)


def test_prepare_reuses_validated_batches_and_keeps_reader_bank_out_of_actor(target):
    model = ScriptedModel([paragraph_extraction(), profile_result, profile_result])
    runner = ModelRunner(model, "fake_model")
    cache = {}
    prepared = prepare_role(target, [scene()], runner, batch_cache=cache)
    repeated = prepare_role(target, [scene()], runner, batch_cache=cache)
    assert [m["purpose"] for m in runner.metrics] == ["extract", "synthesize", "synthesize"]
    assert repeated.coverage["cache_hits"] == 1
    context = actor_context(prepared)
    assert "observations" not in context
    assert context["voice_samples"][0]["quotes"] == ["先给我看凭据。’"]
    assert prepared.as_dict()["review_status"] == "semantic_review_required"
    prepared.coverage["gaps"] = ["旁白揭示的隐藏位置"]
    prepared.profile.limitations = ["角色不知道旁白揭示的隐藏位置"]
    assert "旁白揭示" not in json.dumps(actor_context(prepared), ensure_ascii=False)


def test_cache_is_invalidated_by_source_or_model_and_poisoning_is_rejected(target):
    cache = {}
    model = ScriptedModel([paragraph_extraction(), profile_result] * 3)
    runner = ModelRunner(model, "model_one")
    prepare_role(target, [scene()], runner, batch_cache=cache)
    changed = scene().model_copy(update={"text": scene().text + "灯熄了。"})
    prepare_role(target, [changed], runner, batch_cache=cache)
    prepare_role(target, [scene()], ModelRunner(model, "model_two"), batch_cache=cache)
    assert len(cache) == 3
    first_key = next(iter(cache))
    cache[first_key] = {"character": "另一个人", "observations": []}
    with pytest.raises(PreparationError, match="invalid_cached_batch"):
        prepare_role(target, [scene()], ModelRunner(model, "model_one"), batch_cache=cache)


def test_cache_does_not_reuse_another_books_matching_text(target):
    cache = {}
    model = ScriptedModel([paragraph_extraction(), profile_result] * 2)
    runner = ModelRunner(model, "fake")
    prepare_role(target, [scene()], runner, batch_cache=cache)
    other = target.model_copy(update={"book_key": "another_synthetic_book"})
    prepare_role(other, [scene().model_copy(update={"book_key": other.book_key})],
                 runner, batch_cache=cache)
    assert len(cache) == 2
    assert [m["purpose"] for m in runner.metrics].count("extract") == 2


def test_invalid_json_or_evidence_has_only_one_repair(target):
    model = ScriptedModel([{"content": "not json"}, paragraph_extraction(), profile_result])
    runner = ModelRunner(model, "fake")
    prepare_role(target, [scene()], runner)
    assert len(model.messages) == 3
    assert "invalid_model_json" in model.messages[1][-1]["content"]
    bad = ScriptedModel([paragraph_extraction("unknown:p1")] * 2)
    with pytest.raises(PreparationError, match="unknown_paragraph_reference"):
        prepare_role(target, [scene()], ModelRunner(bad, "fake"))
    assert len(bad.messages) == 2


def test_timeout_stops_without_json_repair_or_automatic_retry(target):
    model = ScriptedModel([{"content": None, "error": "synthetic timeout", "error_code": "timeout"}])
    with pytest.raises(PreparationError, match="model_request_timeout"):
        prepare_role(target, [scene()], ModelRunner(model, "fake"))
    assert len(model.messages) == 1


def test_unknown_short_evidence_alias_is_rejected_without_guessing(target):
    def invalid_reference(messages):
        output = profile_result(messages)
        output["behavior_rules"][0]["evidence_ids"] = ["E999"]
        return output
    model = ScriptedModel([paragraph_extraction(), invalid_reference])
    with pytest.raises(PreparationError, match="unknown_observation_reference"):
        prepare_role(target, [scene()], ModelRunner(model, "synthetic", max_repairs=0))
    assert len(model.messages) == 2


@pytest.mark.parametrize("knowledge,attribution", [
    ("unknown", "narrator"), ("believed", "narrator"), ("reader_only", "narrator"),
    ("experienced", "inference"), ("learned", "inference"),
])
def test_synthesis_omits_unsupported_known_facts_and_reports_it_without_more_calls(target, knowledge, attribution):
    observed = ground_batch(target, [scene()], extraction(kind="action", knowledge=knowledge, attribution=attribution))
    observed += ground_batch(target, [scene()], extraction(kind="action", knowledge="learned", attribution="narrator"))
    fake = ScriptedModel([{"character": target.name, "known_facts": [
        {"text": "不应成为人物记忆的合成线索", "evidence_ids": ["E1"]},
        {"text": "有明确获知依据的合成事实", "evidence_ids": ["E2"]}],
        "identity": [{"text": "合成核验员", "evidence_ids": ["E2"]}]}])
    profile = synthesize_role(target, observed, {}, ModelRunner(fake, "synthetic", max_repairs=0))
    assert profile.known_facts[0].evidence_ids == [observed[1].observation_id] and len(profile.known_facts) == 1
    assert profile.identity[0].evidence_ids == [observed[1].observation_id]
    assert any("1 条候选已知事实" in limitation for limitation in profile.limitations)
    assert len(fake.messages) == 1 and observed[0].knowledge == knowledge
    validate_profile(target, observed, profile)


def test_synthesis_uses_chronological_compact_evidence_and_reuses_unchanged_inputs(target):
    early = ground_batch(target, [scene()], extraction())[0]
    late_scene = scene().model_copy(update={"scene_id":"s9", "chapter_number":9, "char_start":400})
    late_value = extraction().model_dump()
    late_value["observations"][0]["evidence"][0]["scene_id"] = "s9"
    late = ground_batch(target, [late_scene], ExtractionBatch.model_validate(late_value))[0]
    coverage = {"selected_scene_ids":["s1", "s9"], "unselected_scene_ids":[], "gaps":[], "results_revision":1, "batch_revisions":[[1,1]]}
    messages = prompts.synthesis_messages(target, [late, early], coverage)
    payload = json.loads(messages[1]["content"])
    assert [row[1] for row in payload["source_paragraphs"]] == [1,9]
    assert "source_hash" not in messages[1]["content"] and "char_start" not in messages[1]["content"]
    assert payload["source_paragraphs"][0][2] == early.evidence[0].quote
    assert "曾经是" in messages[0]["content"] and "回忆" in messages[0]["content"]
    cache = {}
    fake = ScriptedModel([profile_result])
    profile = synthesize_role(target, [late, early], coverage, ModelRunner(fake,"synthetic",max_calls=None), profile_cache=cache)
    assert len(fake.messages) == 1
    sent = json.loads(fake.messages[0][1]["content"])
    assert {o[0] for o in sent["observations"]} == {"E1", "E2"}
    assert profile.behavior_rules[0].evidence_ids == [early.observation_id]
    coverage.update(results_revision=2, batch_revisions=[[1,2]])
    replay = ScriptedModel([])
    again = synthesize_role(target, [late, early], coverage, ModelRunner(replay,"synthetic",max_calls=None), profile_cache=cache)
    assert again == profile and not replay.messages


@pytest.mark.parametrize("envelope", [
    "```json\n%s\n```",
    "结果如下：\n```json\n%s\n```\n以上为结构化结果。",
    '<think>合成推理材料 {"character":"不应采用的人物"}</think>\n%s',
])
def test_json_envelopes_keep_strict_evidence_and_schema_checks(target, envelope):
    model = ScriptedModel([
        {"content": envelope % json.dumps(paragraph_extraction(), ensure_ascii=False)},
        profile_result,
    ])
    prepared = prepare_role(target, [scene()], ModelRunner(model, "fake"))
    assert len(model.messages) == 2  # No unnecessary repair for prose/fences.
    assert prepared.observations[0].evidence[0].quote == "先给我看凭据。’"
    assert prepared.observations[0].evidence[0].quote_verified


def test_schema_repair_names_count_error_without_echoing_rejected_model_text(target):
    payload = paragraph_extraction()
    payload["observations"] *= 31
    payload["gaps"] = ["synthetic_rejected_output_marker"]
    model = ScriptedModel([payload, paragraph_extraction(), profile_result])
    prepared = prepare_role(target, [scene()], ModelRunner(model, "fake"))
    assert len(prepared.observations) == 1
    repair = model.messages[1][-1]["content"]
    assert "invalid_model_schema" in repair and "observations: too_long" in repair
    assert "synthetic_rejected_output_marker" not in repair


@pytest.mark.parametrize("bad_text", [
    '{"character":"岑砚"}\n{"character":"另一个合成人物"}',
    '[{"character":"岑砚"}',
    '{"broken":, "nested":{"character":"岑砚"}}',
    '<think>未结束的合成推理 {"character":"岑砚"}',
])
def test_ambiguous_or_broken_json_never_salvages_nested_objects(target, bad_text):
    model = ScriptedModel([{"content": bad_text}] * 2)
    with pytest.raises(PreparationError, match="invalid_model_json"):
        prepare_role(target, [scene()], ModelRunner(model, "fake"))
    assert len(model.messages) == 2


def test_request_and_output_budgets_do_not_silently_truncate(target):
    fake = ScriptedModel([])
    with pytest.raises(PreparationError, match="model_budget_exhausted"):
        prepare_role(target, [scene()], ModelRunner(fake, "fake", max_calls=1))
    assert not fake.messages
    runner = ModelRunner(fake, "fake", max_request_chars=1)
    with pytest.raises(PreparationError, match="request_too_large"):
        prepare_role(target, [scene()], runner)
    assert not fake.messages
    truncated = ScriptedModel([{"content": "{}", "finish_reason": "length"}])
    with pytest.raises(PreparationError, match="model_output_truncated"):
        prepare_role(target, [scene()], ModelRunner(truncated, "fake"))


def test_empty_material_does_not_call_model_or_claim_ready(target):
    model = ScriptedModel([])
    prepared = prepare_role(target, [], ModelRunner(model, "fake"))
    assert not model.messages
    assert not prepared.observations
    assert prepared.profile.limitations
    assert prepared.as_dict()["review_status"] != "ready"


def test_model_failure_diagnostics_never_include_private_details(target):
    def failing(*args, **kwargs):
        raise RuntimeError("private_token_should_not_appear")
    with pytest.raises(PreparationError) as caught:
        prepare_role(target, [scene()], ModelRunner(failing, "fake"))
    assert str(caught.value) == "model_request_failed"


def test_comparison_never_sends_holdouts_rubrics_or_extras_to_model():
    data = corpus()
    captured = []

    def fake(messages, **kwargs):
        captured.append(json.dumps(messages, ensure_ascii=False))
        payload = json.loads(messages[1]["content"])
        if "output_schema" not in payload:
            return {"content": "先把经过说清楚。"}
        if "paragraphs" in payload:
            selected = payload["paragraphs"][0]
            output = {"character": payload["target"]["name"], "observations": [{
                "kind": "action", "claim": "待审查的合成观测", "attribution": "narrator",
                "knowledge": "unknown", "evidence": [{"paragraph_id": selected["paragraph_id"]}]}]}
        else:
            output = {"character": payload["target"]["name"], "limitations": ["语义未评测"]}
        return {"content": json.dumps(output, ensure_ascii=False)}

    report = run_comparison(data, ModelRunner(fake, "fake", max_calls=64))
    assert not report["has_errors"]
    assert report["quality_evaluated"] is False
    assert len(report["results"]) == 6
    sent = "\n".join(captured)
    for held in report["heldout_review_scenes"]:
        assert held["text"] not in sent
    for entry in data["targets"]:
        for case in entry["cases"]:
            assert case["rubric"] not in sent
    assert "平行番外里" not in sent
    assert "manual_review_required" == report["review_status"]


def test_plan_and_cli_need_no_optional_ai_or_database_imports():
    code = """
import importlib.abc, runpy, sys
class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        blocked = ('torch', 'FlagEmbedding', 'mcp', 'app.config', 'app.database', 'app.main')
        if any(fullname == b or fullname.startswith(b + '.') for b in blocked):
            raise AssertionError('unexpected import: ' + fullname)
sys.meta_path.insert(0, Blocker())
sys.argv = ['evaluate_role_chat.py']
runpy.run_path('scripts/evaluate_role_chat.py', run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True,
                            text=True, encoding="utf-8", env={**os.environ,
                                                               "PYTHONIOENCODING": "utf-8"}, timeout=30)
    assert result.returncode == 0, result.stderr
    plan = json.loads(result.stdout)
    assert plan["network_requested"] is False
    assert plan["quality_evaluated"] is False


def test_plan_exposes_baseline_and_candidate_coverage():
    plan = comparison_plan(corpus(), "岑砚")
    target_plan = plan["targets"][0]
    assert target_plan["arms"]["A"]["minimum_prepare_calls"] == 0
    assert "source_04" in target_plan["arms"]["B"]["coverage"]["unselected_scene_ids"]
    assert not target_plan["arms"]["C"]["coverage"]["unselected_scene_ids"]
    with pytest.raises(ValueError):
        comparison_plan(corpus(), "不存在的人物")


def test_cli_refuses_existing_report_before_any_model_import(tmp_path):
    # The service address is intentionally unusable: the existing output must
    # short-circuit before the model client is imported or a request attempted.
    destination = tmp_path / "existing.json"
    destination.write_text("preserve this synthetic report", encoding="utf-8")
    result = subprocess.run([
        sys.executable, "scripts/evaluate_role_chat.py", "--run-model",
        "--base-url", "invalid-local-service", "--model", "synthetic-test",
        "--output", str(destination),
    ], cwd=BACKEND, capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=30)
    assert result.returncode == 2
    assert destination.read_text(encoding="utf-8") == "preserve this synthetic report"


def test_prompt_separates_persona_evidence_and_reader_identity():
    assert "reader_only" in prompts.EXTRACTION_SYSTEM
    assert "失忆" in prompts.SYNTHESIS_SYSTEM
    assert "书外读者" in prompts.SYNTHESIS_SYSTEM
    assert "例外" in prompts.SYNTHESIS_SYSTEM


def test_grouped_synthesis_stays_bounded_and_resumes_from_validated_group_cache(target):
    observations = []
    for index in range(16):
        source = RoleScene(scene_id=f"group_{index}", book_key=target.book_key,
                           chapter_number=index + 1, text=f"第{index}处，岑砚核对合成收据后拒绝无凭指控。")
        result = ExtractionBatch(character=target.name, observations=[{
            "kind": "action", "claim": "需核对的合成行为" * 45, "attribution": "action",
            "knowledge": "unknown", "evidence": [{"scene_id": source.scene_id, "quote": source.text}],
        }])
        observations.extend(ground_batch(target, [source], result))
    sent = []
    def fake(messages, **kwargs):
        sent.append(messages)
        data = json.loads(messages[1]["content"])
        references = data.get("observations", data.get("evidence_catalog"))
        reference = references[0][0] if isinstance(references[0], list) else references[0]["observation_id"]
        output = {"character": target.name, "identity": [{"text": "合成核验员",
                  "evidence_ids": [reference]}]}
        return {"content": json.dumps(output, ensure_ascii=False)}
    runner = ModelRunner(fake, "grouped_fake", max_request_chars=11000)
    cache = {}
    def stop_after_group(key, value):
        cache[key] = value.model_dump()
        raise PreparationError("pause_requested")
    coverage = {"selected_scene_ids": [f"group_{i}" for i in range(16)], "gaps": [], "cache_hits": 0}
    with pytest.raises(PreparationError, match="pause_requested"):
        synthesize_role(target, observations, dict(coverage), runner, cache, stop_after_group)
    first_request = sent[0]
    before = len(sent)
    profile = synthesize_role(target, observations, {**coverage, "cache_hits": 16}, runner, cache)
    assert len(sent) > before
    assert first_request not in sent[before:]
    assert all(sum(len(m["content"]) for m in messages) <= 11000 for messages in sent)
    assert any("profiles" in json.loads(messages[1]["content"]) for messages in sent)
    validate_profile(target, observations, profile)
