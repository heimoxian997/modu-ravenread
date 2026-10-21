"""切块纯函数测试。"""

import pytest

from app.services.chunk_service import (
    split_children,
    split_parents,
    strip_title_prefix,
)


def test_strip_title_exact_line():
    body, removed = strip_title_prefix("第一章 初见\n正文开始。", "第一章 初见")
    assert body == "正文开始。"
    assert removed == len("第一章 初见\n")


def test_strip_title_when_title_is_substring_of_first_line():
    body, removed = strip_title_prefix(
        "☆、第51章 chapter51\n正文开始。", "chapter51"
    )
    assert body == "正文开始。"
    assert removed == len("☆、第51章 chapter51\n")


def test_strip_title_keeps_body_when_not_matched():
    chapter_text = "这一行不是标题。\n正文开始。"
    body, removed = strip_title_prefix(chapter_text, "第一章 初见")
    assert body == chapter_text
    assert removed == 0


def test_strip_title_empty_title_does_not_remove():
    chapter_text = "正文第一行\n正文第二行"
    assert strip_title_prefix(chapter_text, "") == (chapter_text, 0)


def test_strip_only_title_line_results_in_empty_body():
    body, removed = strip_title_prefix("第一章 只有标题", "第一章 只有标题")
    assert body == ""
    assert removed == len("第一章 只有标题")


def test_strip_title_substring_ignored_when_first_line_too_long():
    title = "第一章 初见"
    chapter_text = title + "附" * 130 + "\n正文"
    body, removed = strip_title_prefix(chapter_text, title)
    assert body == chapter_text
    assert removed == 0


def test_split_parents_accumulates_paragraphs_without_exceeding_limit():
    paragraph = "甲" * 700 + "。\n"
    text = paragraph * 4
    ranges = split_parents(text, 1500)

    assert len(ranges) == 2
    assert all(end - start <= 1500 for start, end in ranges)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(text)
    assert "".join(text[start:end] for start, end in ranges) == text
    for before, after in zip(ranges, ranges[1:]):
        assert before[1] == after[0]


def test_split_parents_splits_long_paragraph_at_sentence_boundary():
    text = "这是一个长句子。" * 500
    ranges = split_parents(text, 1500)

    assert len(ranges) > 1
    assert all(end - start <= 1500 for start, end in ranges)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(text)
    assert "".join(text[start:end] for start, end in ranges) == text
    for start, end in ranges[:-1]:
        assert text[end - 1] in "。！？!?；;\n"


def test_split_parents_empty_text():
    assert split_parents("", 1500) == []
    assert split_parents("   \n\n", 1500) == []


def test_split_children_limits_overlap_and_sentence_boundaries():
    text = "这是一个测试句子。" * 300
    ranges = split_children(text, 0, len(text), 100, 20, 120)

    assert len(ranges) > 2
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(text)
    assert all(end - start <= 120 for start, end in ranges)
    for start, end in ranges[:-1]:
        assert end - start > 0
        assert text[end - 1] in "。！？!?；;\n"

    for (_, previous_end), (start, _) in zip(ranges, ranges[1:]):
        overlap = previous_end - start
        assert 0 < overlap <= 20


def test_split_children_hard_cut_when_no_sentence_boundary():
    text = "a" * 1000
    ranges = split_children(text, 0, len(text), 100, 20, 120)

    assert ranges[0] == (0, 100)
    assert all(end - start <= 120 for start, end in ranges)
    assert ranges[-1][1] == len(text)


def test_split_children_stays_inside_parent():
    text = "甲" * 200 + "。" + "乙" * 200 + "。" + "丙" * 200 + "。"
    parent_start = 200
    parent_end = 603
    ranges = split_children(text, parent_start, parent_end, 100, 20, 120)

    assert ranges[0][0] == parent_start
    assert ranges[-1][1] == parent_end
    assert all(
        parent_start <= start < end <= parent_end for start, end in ranges
    )


def test_split_children_rejects_invalid_size():
    with pytest.raises(ValueError):
        split_children("正文", 0, 2, 0, 10, 20)
