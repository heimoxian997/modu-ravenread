import re

import pytest

from app.common.exceptions import BusinessException
from app.services.parser_service import (
    chinese_number_to_int,
    derive_chapter_rule,
    derive_chapter_rules,
    parse_novel,
)


def test_derive_two_arabic_chinese_samples_share_rule():
    rule = derive_chapter_rule(["第一章 初见", "第1023章 大结局"])
    assert rule.prefix == "第"
    assert rule.unit == "章"
    regex = re.compile(rule.pattern)
    match = regex.match("第十二章 新的开始")
    assert match
    assert match.group(2) == "新的开始"
    assert regex.match("第12章：重逢")
    assert not regex.match("他翻开第十二章，继续阅读。")


def test_derive_no_prefix_rule():
    rule = derive_chapter_rule(["1. 初见", "2、离京"])
    regex = re.compile(rule.pattern)
    assert rule.prefix == ""
    assert rule.unit == ""
    assert regex.match("3. 归来").group(2) == "归来"
    assert regex.match("1024、终局")


def test_decorated_chapter_samples_share_rule():
    """行首 ☆、● 等装饰符不应影响前缀推导，英文前缀大小写也不应冲突。"""
    rule = derive_chapter_rule(["　　☆、chapter 3", "Chapter 4"])
    assert rule.prefix.lower() == "chapter"
    assert rule.unit == ""

    text = (
        "　　☆、chapter 3 初见\n"
        "第三章正文。\n"
        "chapter 4 离京\n"
        "第四章正文。\n"
    )
    result = parse_novel(text, ["　　☆、chapter 3", "Chapter 4"])
    assert len(result["chapters"]) == 2
    assert [item["title"] for item in result["chapters"]] == ["初见", "离京"]
    assert result["chapters"][0]["start"] == text.index("☆")
    assert result["chapters"][0]["end"] == result["chapters"][1]["start"]


def test_decorated_headings_match_with_single_sample():
    text = "☆、chapter 3 初见\n正文\nchapter 4 离京\n正文"
    result = parse_novel(text, ["chapter 3"])
    assert len(result["chapters"]) == 2
    assert [item["title"] for item in result["chapters"]] == ["初见", "离京"]


def test_derive_different_formats_as_two_rules():
    rules = derive_chapter_rules(["第一章 初见", "第二节 离京"])
    assert len(rules) == 2
    assert [(rule.prefix, rule.unit) for rule in rules] == [
        ("第", "章"),
        ("第", "节"),
    ]


def test_mixed_chapter_formats_parse_together():
    """同一本书同时存在 chapter 3 和 第104章 两套格式时，两套规则同时生效。"""
    samples = ["chapter 3", "第104章 chapter104"]
    text = (
        "　　☆、chapter 3\n"
        "第一段正文。\n"
        "chapter 4\n"
        "第二段正文。\n"
        "☆、第99章 chapter99\n"
        "第三段正文。\n"
        "第104章 chapter104\n"
        "第四段正文。\n"
    )
    result = parse_novel(text, samples)

    assert result["stats"]["chapter_count"] == 4
    assert len(result["rules"]["chapters"]) == 2
    assert result["rules"]["multiple_chapter_formats"] is True
    assert [item["title"] for item in result["chapters"]] == [
        "☆、chapter 3",
        "chapter 4",
        "chapter99",
        "chapter104",
    ]
    assert any(
        item["code"] == "multiple_chapter_formats" for item in result["warnings"]
    )
    for before, after in zip(result["chapters"], result["chapters"][1:]):
        assert before["end"] == after["start"]


def test_derive_sample_without_number_raises():
    with pytest.raises(BusinessException):
        derive_chapter_rule(["楔子"])


def test_chinese_number_conversion():
    assert chinese_number_to_int("一") == 1
    assert chinese_number_to_int("十") == 10
    assert chinese_number_to_int("一百零二") == 102
    assert chinese_number_to_int("一千零二十三") == 1023
    assert chinese_number_to_int("一万零一") == 10001
    assert chinese_number_to_int("二〇二三") == 2023
    assert chinese_number_to_int("1023") == 1023


def test_parse_full_novel_stats_and_front_matter(sample_novel):
    result = parse_novel(
        sample_novel,
        ["第一章 初见", "第1023章 大结局"],
        ["番外一 春日", "番外：婚后"],
    )
    stats = result["stats"]
    assert stats["chapter_count"] == 7
    assert stats["main_chapter_count"] == 5
    assert stats["extra_chapter_count"] == 2
    assert result["chapters"][0]["title"] == "初见"
    assert result["chapters"][-1]["kind"] == "extra"
    assert result["front_matter_chars"] > 0
    assert stats["total_chars"] == len(sample_novel)
    assert stats["min_word_count"] > 0
    assert stats["max_word_count"] > 0
    # 边界连续且覆盖从首章到末尾
    assert result["chapters"][0]["start"] == result["front_matter_chars"]
    for before, after in zip(result["chapters"], result["chapters"][1:]):
        assert before["end"] == after["start"]
    assert result["chapters"][-1]["end"] == len(sample_novel)
    assert result["rules"]["confidence"] == "high"


def test_prose_containing_chapter_text_not_matched():
    text = (
        "第一章 开始\n"
        "他翻开书，想起第3章里的一句话。\n"
        "这句话并不在行首。\n"
        "第二章 结束\n"
        "完。\n"
    )
    result = parse_novel(text, ["第一章 开始", "第二章 结束"])
    assert len(result["chapters"]) == 2
    assert result["chapters"][0]["start"] == 0


def test_indented_heading_and_front_matter():
    text = "作者的话\n\n  第一章 初见\n正文\n第二章 离京\n正文二"
    result = parse_novel(text, ["第一章 初见", "第二章 离京"])
    assert result["chapters"][0]["start"] == text.index("第一章")
    assert text[: result["chapters"][0]["start"]].endswith("  ")


def test_extra_forms():
    for sample, heading, expected_title in [
        ("番外一 春日", "番外一 春日", "春日"),
        ("番外：婚后", "番外：婚后", "婚后"),
        ("番外篇·标题", "番外篇·标题", "标题"),
    ]:
        text = f"{heading}\n番外正文\n第一章 占位\n正文内容"
        result = parse_novel(text, ["第一章 占位"], [sample])
        assert result["chapters"][0]["kind"] == "extra"
        assert result["chapters"][0]["title"] == expected_title
        # 正文中以“番外”开头的普通句子不能被当成第二个番外标题。
        assert len(result["chapters"]) == 2


def test_extra_english_prefix():
    text = "第一章 占位\n正文\nExtra 1 标题\n番外正文"
    result = parse_novel(text, ["第一章 占位"], ["Extra 12 标题"])
    assert len(result["chapters"]) == 2
    assert result["chapters"][1]["kind"] == "extra"
    assert result["chapters"][1]["title"] == "标题"


def test_extra_priority_and_not_last_warning():
    text = "第一章 开始\n正文\n番外一 春日\n番外正文\n第二章 回归\n正文"
    result = parse_novel(text, ["第一章 开始", "第2章 回归"], ["番外一 春日"])
    assert [item["kind"] for item in result["chapters"]] == ["main", "extra", "main"]
    assert any(item["code"] == "extra_not_last" for item in result["warnings"])


def test_extra_at_beginning_warns():
    text = "番外一 春日\n番外正文\n第一章 开始\n正文"
    result = parse_novel(text, ["第一章 开始"], ["番外一 春日"])
    assert any(item["code"] == "extra_not_last" for item in result["warnings"])


def test_zero_hit_warns_and_fallback():
    text = "这是一段没有任何章节标题的长文本。" * 500
    result = parse_novel(text, ["第一章 初见"], fallback=False)
    assert result["chapters"] == []
    assert any(item["code"] == "no_chapters" for item in result["warnings"])
    assert result["front_matter_chars"] == len(text)

    fallback = parse_novel(text, ["第一章 初见"], fallback=True)
    assert len(fallback["chapters"]) >= 2
    assert fallback["rules"]["fallback"] is True
    assert fallback["rules"]["confidence"] == "low"
    assert any(item["code"] == "fallback_used" for item in fallback["warnings"])
    assert fallback["chapters"][0]["start"] == 0
    assert fallback["chapters"][-1]["end"] == len(text)
    for before, after in zip(fallback["chapters"], fallback["chapters"][1:]):
        assert before["end"] == after["start"]


def test_duplicate_and_backwards_heading_warnings():
    text = "第三章 甲\n正文\n第三章 乙\n正文\n第二章 丙\n正文"
    result = parse_novel(text, ["第三章 甲"])
    codes = {item["code"] for item in result["warnings"]}
    assert "duplicate_number" in codes
    assert "number_backwards" in codes


def test_empty_chapter_warning():
    text = "第一章 空章\n第二章 有正文\n正文"
    result = parse_novel(text, ["第一章 空章", "第二章 有正文"])
    assert any(item["code"] == "empty_chapter" for item in result["warnings"])
    # 空章仍保留，允许用户合并或删除边界
    assert len(result["chapters"]) == 2


def test_suspected_heading_warning():
    text = "第一章 开始\n正文\n序章 被遗漏的标题\n正文"
    result = parse_novel(text, ["第一章 开始"])
    assert any(item["code"] == "possible_missed" for item in result["warnings"])


def test_no_extra_sample_warns_about_possible_extra():
    text = "第一章 开始\n正文\n番外 春日\n番外正文"
    result = parse_novel(text, ["第一章 开始"])
    assert any(item["code"] == "possible_extra" for item in result["warnings"])
    assert all(item["kind"] == "main" for item in result["chapters"])


def test_overlong_heading_not_matched():
    text = "第一章 " + "长" * 100 + "\n正文"
    result = parse_novel(text, ["第一章 初见"])
    assert result["chapters"] == []


def test_single_chapter_warning():
    result = parse_novel("第一章 唯一\n正文", ["第一章 初见"])
    assert any(item["code"] == "single_chapter" for item in result["warnings"])
