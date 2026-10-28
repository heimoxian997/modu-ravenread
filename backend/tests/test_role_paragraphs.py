"""Paragraph grounding and sentence completion use synthetic prose only."""

import json

import pytest
from pydantic import ValidationError

from app.schemas.role_preparation import ExtractionBatch, ParagraphExtractionBatch, RoleScene, RoleTarget, RoleProfile
from app.services.role_paragraph_service import source_paragraphs, resolve_opening
from app.services.role_preparation_service import (
    ground_batch, PreparationError, actor_context, PreparedRole, validate_profile,
)
from app.services import role_preparation_prompt as prompts


def source(text="  岑砚说：‘先核对凭据，再作判断。’\r\n祝遥在门外等候。", **changes):
    return RoleScene(scene_id="synthetic:100", book_key="synthetic", chapter_number=1,
                     char_start=100, text=text, **changes)


def target():
    return RoleTarget(book_key="synthetic", name="岑砚")


def value(ref=None, **changes):
    return ExtractionBatch(character="岑砚", observations=[{
        "kind": "speech", "claim": "先核验凭据", "attribution": "self_statement",
        "knowledge": "not_applicable", "speaker": "岑砚",
        "evidence": [ref or {"paragraph_id": "synthetic:100:p1"}], **changes,
    }])


def test_paragraphs_preserve_original_crlf_indentation_and_offsets():
    scene = source()
    paragraphs = source_paragraphs([scene])
    assert [p["paragraph_id"] for p in paragraphs] == ["synthetic:100:p1", "synthetic:100:p2"]
    for paragraph in paragraphs:
        assert scene.text[paragraph["char_start"] - 100:paragraph["char_end"] - 100] == paragraph["text"]
    assert paragraphs[0]["text"].startswith("  岑砚")
    assert paragraphs == source_paragraphs([scene])
    assert scene.text.endswith("祝遥在门外等候。")


def test_long_unbroken_paragraphs_are_bounded_contiguous_and_complete():
    scene = source("合成档案" * 1500 + "。")
    paragraphs = source_paragraphs([scene])
    assert all(0 < len(p["text"]) <= 1600 for p in paragraphs)
    assert "".join(p["text"] for p in paragraphs) == scene.text
    assert all(a["char_end"] == b["char_start"] for a, b in zip(paragraphs, paragraphs[1:]))


@pytest.mark.parametrize("opening,expected,method", [
    (None, "旁白。岑砚说：‘先核对凭据，再作判断。’他推开了门。", "paragraph"),
    ("先核对凭据", "先核对凭据，再作判断。’", "opening_sentence"),
    ("先核对凭据，再作判断。", "先核对凭据，再作判断。’", "opening_sentence"),
    ("先核对凭据,再作", "先核对凭据，再作判断。’", "opening_sentence"),
    ("不存在的开头", "旁白。岑砚说：‘先核对凭据，再作判断。’他推开了门。", "paragraph"),
    ("先核对……再作", "旁白。岑砚说：‘先核对凭据，再作判断。’他推开了门。", "paragraph"),
    ("嗯", "旁白。岑砚说：‘先核对凭据，再作判断。’他推开了门。", "paragraph"),
])
def test_opening_completion_is_always_original_text(opening, expected, method):
    text = "旁白。岑砚说：‘先核对凭据，再作判断。’他推开了门。"
    start, end, actual, warning = resolve_opening(text, opening)
    assert text[start:end] == expected and actual == method
    if opening and method == "paragraph":
        assert warning


def test_ambiguous_opening_and_missing_sentence_end_retain_full_paragraph():
    for text, warning in [("先核对凭据。先核对凭据。", "quote_ambiguous"),
                          ("岑砚说先核对凭据", "sentence_end_missing")]:
        start, end, method, reason = resolve_opening(text, "先核对凭据")
        assert text[start:end] == text and method == "paragraph" and reason == warning


def test_paragraph_and_opening_grounding_record_exact_source_locations():
    scene = source()
    whole = ground_batch(target(), [scene], value())[0]
    narrowed = ground_batch(target(), [scene], value({"paragraph_id": "synthetic:100:p1", "opening": "先核对凭据"}))[0]
    for observation in (whole, narrowed):
        q = observation.evidence[0]
        assert scene.text[q.char_start - 100:q.char_end - 100] == q.quote
        assert q.quote_verified and q.paragraph_id == "synthetic:100:p1"
    assert narrowed.evidence[0].quote == "先核对凭据，再作判断。’"
    assert whole.semantic_attribution == "model_assigned"
    assert whole.evidence[0].match_method == "paragraph"


def test_unknown_paragraph_wrong_book_and_invalid_speaker_are_rejected():
    with pytest.raises(PreparationError, match="unknown_paragraph_reference"):
        ground_batch(target(), [source()], value({"paragraph_id": "other_scene:p1"}))
    with pytest.raises(PreparationError, match="cross_book_scene"):
        ground_batch(target().model_copy(update={"book_key": "other"}), [source()], value())
    with pytest.raises(PreparationError, match="invalid_speech_attribution"):
        ground_batch(target(), [source()], value(speaker="祝遥"))
    with pytest.raises(PreparationError, match="scene_out_of_scope"):
        ground_batch(target(), [source(kind="extra")], value())


def test_model_contract_is_twelve_paragraph_observations_legacy_twenty_survive():
    generated = value().model_dump()
    generated["observations"] *= 12
    assert len(ParagraphExtractionBatch.model_validate(generated).observations) == 12
    generated["observations"] += generated["observations"][:1]
    with pytest.raises(ValidationError):
        ParagraphExtractionBatch.model_validate(generated)
    legacy = value({"scene_id": "synthetic:100", "quote": "先核对凭据"}).model_dump()
    legacy["observations"] *= 20
    assert len(ExtractionBatch.model_validate(legacy).observations) == 20
    with pytest.raises(ValidationError):
        ParagraphExtractionBatch.model_validate(legacy)


def test_shared_paragraph_is_sent_once_and_whole_paragraph_is_not_an_actor_quote():
    a = ground_batch(target(), [source()], value())[0]
    b = ground_batch(target(), [source()], value(claim="遇到争议时先核验"))[0]
    messages = prompts.synthesis_messages(target(), [a, b], {})
    payload = json.loads(messages[1]["content"])
    assert len(payload["source_paragraphs"]) == 1
    assert messages[1]["content"].count(source_paragraphs([source()])[0]["text"]) == 1
    profile = RoleProfile(character="岑砚", voice_evidence_ids=[a.observation_id])
    prepared = PreparedRole(target(), profile, [a], {"unselected_scene_ids": [], "gaps": []}, [])
    assert actor_context(prepared)["voice_samples"] == []


def test_reader_only_paragraph_still_cannot_become_character_knowledge():
    a = ground_batch(target(), [source()], value(kind="action", knowledge="reader_only"))[0]
    profile = RoleProfile(character="岑砚", known_facts=[{"text": "合成秘密", "evidence_ids": [a.observation_id]}])
    with pytest.raises(PreparationError, match="reader_only_profile_reference"):
        validate_profile(target(), [a], profile)


def test_model_input_contains_only_numbered_sources_and_no_quote_schema():
    data = json.loads(prompts.extraction_messages(target(), [source()])[1]["content"])
    assert "scenes" not in data and len(data["paragraphs"]) == 2
    assert "quote" not in json.dumps(data["output_schema"])
    assert "paragraph_id" in json.dumps(data["output_schema"])


def test_known_fact_allowlist_excludes_inference_even_when_marked_learned():
    inferred = ground_batch(target(), [source()], value(kind="belief", knowledge="learned", attribution="inference"))[0]
    learned = ground_batch(target(), [source()], value(kind="action", knowledge="learned", attribution="narrator"))[0]
    believed = ground_batch(target(), [source()], value(kind="belief", knowledge="believed", attribution="narrator"))[0]
    payload = json.loads(prompts.synthesis_messages(target(), [inferred, learned, believed], {})[1]["content"])
    assert payload["eligible_evidence_ids"]["known_facts"] == ["E2"]
    assert payload["eligible_evidence_ids"]["beliefs"] == ["E3"]
    assert payload["eligible_evidence_ids"]["voice"] == []
