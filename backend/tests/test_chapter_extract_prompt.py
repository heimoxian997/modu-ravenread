"""章节抽取 JSON、字段预算与编辑校验测试（v6：只做单章，不再有剧情线）。"""

from __future__ import annotations

import json

import pytest

from app.services import chapter_extract_prompt as prompt


def make_payload(summary_text: str = "第一章摘要。"):
    return {
        "summary": {
            "summary": summary_text,
            "characters": ["林晚", "秦屿"],
            "key_events": [
                "林晚在旧楼发现半张化验单，确认父亲曾参与联合项目",
                "秦屿当面否认认识林晚父亲，两人不欢而散",
            ],
            "cliffhanger": "门外有人。",
        },
        "characters": {
            "mentions": [
                {"name": "林晚", "aliases": ["晚晚"], "role": "调查者"}
            ],
            "card_deltas": [
                {
                    "character": "林晚",
                    "identity": "前联合项目成员",
                    "traits": ["冷静"],
                    "relations": ["与秦屿合作"],
                    "known_facts": ["失忆"],
                    "voice_sample": "我不知道。",
                    "arc": "开始追查",
                    "reason": "本章新增身份",
                }
            ],
            "new_aliases": [{"alias": "晚晚", "canonical": "林晚"}],
        },
        "lore": [
            {
                "name": "联合项目",
                "category": "organization",
                "aliases": ["T"],
                "summary": "秘密研究计划。",
                "details": ["双胞胎研究"],
                "entities": ["林晚"],
            }
        ],
    }


def test_prompt_version_and_structure_are_v6():
    assert prompt.PROMPT_VERSION == "chapter_extract_v6"
    assert prompt.SUMMARY_KEYS == {
        "summary",
        "characters",
        "key_events",
        "cliffhanger",
    }
    for word in (
        "thread_events",
        "loose_events",
        "open_threads",
        "known_threads",
        "未命名线",
        "progress",
        "disclosure",
        "is_new",
    ):
        assert word not in prompt.SYSTEM_PROMPT
    assert "key_events" in prompt.SYSTEM_PROMPT


def test_parse_valid_fenced_and_explained():
    raw = json.dumps(make_payload(), ensure_ascii=False)
    assert prompt.parse_extraction(raw)["summary"]["summary"] == "第一章摘要。"
    fenced = f"说明文字\n```json\n{raw}\n```\n结束"
    parsed = prompt.parse_extraction(fenced)
    assert parsed["summary"]["characters"] == ["林晚", "秦屿"]
    assert parsed["summary"]["key_events"][0].startswith("林晚在旧楼")
    assert prompt.parse_extraction(f"前言 {raw} 后记")["lore"][0]["name"] == "联合项目"


def test_parse_missing_fields_raises():
    data = make_payload()
    del data["lore"]
    with pytest.raises(prompt.ExtractionParseError):
        prompt.parse_extraction(json.dumps(data, ensure_ascii=False))

    data = make_payload()
    del data["summary"]["cliffhanger"]
    with pytest.raises(prompt.ExtractionParseError):
        prompt.parse_extraction(json.dumps(data, ensure_ascii=False))

    data = make_payload()
    del data["summary"]["key_events"]
    with pytest.raises(prompt.ExtractionParseError):
        prompt.parse_extraction(json.dumps(data, ensure_ascii=False))

    data = make_payload()
    del data["summary"]["characters"]
    with pytest.raises(prompt.ExtractionParseError):
        prompt.parse_extraction(json.dumps(data, ensure_ascii=False))


def test_parse_drops_empty_key_events_and_normalizes_lore_category():
    data = make_payload()
    data["summary"]["key_events"] = ["有事件", "", "   "]
    data["lore"][0]["category"] = "place"
    parsed = prompt.parse_extraction(json.dumps(data, ensure_ascii=False))
    assert parsed["summary"]["key_events"] == ["有事件"]
    assert parsed["lore"][0]["category"] == "world_rule"


def test_enforce_summary_and_field_limits():
    data = make_payload("很长" * 300)
    data["summary"]["key_events"] = [f"事件{i}" * 30 for i in range(8)]
    data["characters"]["mentions"] = [
        {"name": f"人物{i}", "aliases": [], "role": "角色"}
        for i in range(15)
    ]
    data["lore"] = [
        {
            "name": f"设定{i}",
            "category": "location",
            "aliases": [],
            "summary": "x" * 100,
            "details": ["a", "b", "c", "d"],
            "entities": [],
        }
        for i in range(6)
    ]
    parsed, summary_truncated, output_trimmed = prompt.enforce_limits(data)
    assert summary_truncated is True
    assert output_trimmed is True
    assert len(parsed["summary"]["summary"]) <= 400
    assert len(parsed["summary"]["key_events"]) == 5
    assert all(len(item) <= 50 for item in parsed["summary"]["key_events"])
    assert len(parsed["characters"]["mentions"]) == 12
    assert len(parsed["lore"]) == 4
    assert len(parsed["lore"][0]["details"]) == 3


def test_total_content_budget_trims_and_counts_chinese_only():
    data = make_payload("摘要" * 200)
    data["summary"]["key_events"] = ["推进" * 20 for _ in range(5)]
    data["characters"]["mentions"] = [
        {"name": f"人物{i}", "aliases": ["称谓" * 20], "role": "重要角色" * 20}
        for i in range(12)
    ]
    data["characters"]["card_deltas"] = [
        {
            "character": f"人物{i}",
            "identity": "身份" * 50,
            "traits": ["性格" * 50],
            "relations": ["关系" * 50],
            "known_facts": ["事实" * 50],
            "voice_sample": "台词" * 50,
            "arc": "弧线" * 50,
            "reason": "原因" * 50,
        }
        for i in range(5)
    ]
    data["lore"] = [
        {
            "name": f"设定{i}",
            "category": "world_rule",
            "aliases": ["别名" * 20],
            "summary": "说明" * 50,
            "details": ["细节" * 50 for _ in range(3)],
            "entities": ["实体" * 50 for _ in range(5)],
        }
        for i in range(4)
    ]
    parsed, summary_truncated, output_trimmed = prompt.enforce_limits(data)
    assert output_trimmed is True
    assert prompt.content_char_count(parsed) <= prompt.TOTAL_CONTENT_LIMIT
    assert summary_truncated in {True, False}
    assert set(parsed["summary"]) == {
        "summary",
        "characters",
        "key_events",
        "cliffhanger",
    }


def test_validate_edit_fields_new_shape():
    data = make_payload()
    data["summary"]["summary"] = "长" * 401
    with pytest.raises(prompt.ExtractionValidationError):
        prompt.validate_edit_fields(data["summary"])

    data = make_payload()
    data["summary"]["key_events"] = [f"事件{i}" for i in range(6)]
    with pytest.raises(prompt.ExtractionValidationError):
        prompt.validate_edit_fields(data["summary"])

    data = make_payload()
    data["summary"]["thread_events"] = [{"thread": "x"}]
    data["summary"]["loose_events"] = ["x"]
    cleaned = prompt.validate_edit_fields(data["summary"])
    assert set(cleaned) == {"summary", "key_events", "characters", "cliffhanger"}
