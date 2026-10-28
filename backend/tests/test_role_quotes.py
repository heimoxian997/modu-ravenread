"""Synthetic quote formatting: recover exact source, never missing words."""

import pytest

from app.schemas.role_preparation import RoleScene, RoleTarget, ExtractionBatch
from app.services.role_preparation_service import ground_batch, PreparationError


def grounded(source, quote):
    target = RoleTarget(book_key="synthetic", name="岑砚")
    scene = RoleScene(book_key="synthetic", scene_id="s1", chapter_number=3, char_start=20, text=source)
    value = ExtractionBatch(character="岑砚", observations=[{
        "kind":"action", "claim":"核对后回答", "attribution":"narrator", "knowledge":"unknown",
        "evidence":[{"scene_id":"s1", "quote":quote}]}])
    return ground_batch(target, [scene], value)[0].evidence[0]


def test_formatting_recovery_saves_original_text_and_offsets():
    source = "岑砚先核对凭据，\n随后才回答。"
    requested = "先核对凭据,随后才回答。"
    quote = grounded(source, requested)
    assert quote.match_method == "format_normalized" and quote.requested_quote == requested
    assert quote.quote == source[quote.char_start-20:quote.char_end-20]
    assert "，\n" in quote.quote and quote.quote_verified


def test_exact_quote_stays_exact_even_if_source_contains_ellipsis():
    quote = grounded("岑砚停下……然后走回档案馆。", "停下……然后走回档案馆。")
    assert quote.match_method == "exact" and quote.requested_quote is None


@pytest.mark.parametrize(("source", "quote", "code"), [
    ("先核对凭据，随后才回答。先核对凭据；随后才回答。", "先核对凭据随后才回答", "quote_ambiguous"),
    ("先核对凭据，接着询问来历，随后才回答。", "先核对凭据……随后才回答", "quote_omitted"),
    ("岑砚并没有交出钥匙。", "岑砚交出钥匙。", "quote_not_found"),
    ("编号 A-12 被划去；版本为 3.8。", "编号A12被划去版本为38", "quote_not_found"),
    ("最后的收支记为-500元。", "最后的收支记为500元", "quote_not_found"),
])
def test_recovery_rejects_ambiguity_omissions_and_changed_content(source, quote, code):
    with pytest.raises(PreparationError, match=code):
        grounded(source, quote)
