"""单章别名自检测试：规则层 + 可选模型层。"""

from __future__ import annotations

import json

from app.database import SessionLocal
from app.models.chapter_extraction import ChapterExtraction
from app.services import alias_check_service, chapter_extract_service
from test_chapter_extract_service import (  # noqa: F401
    _create_book,
    _fake_chat,
    _payload,
)

TEXT = "韩砚清走进院子，姐姐在檐下缝衣。韩叔在门口站着。"


def _parsed(mentions=None, new_aliases=None):
    return {
        "summary": {"summary": "摘要", "characters": [], "key_events": []},
        "characters": {
            "mentions": mentions or [],
            "card_deltas": [],
            "new_aliases": new_aliases or [],
        },
        "lore": [],
    }


def _chat(monkeypatch, responses):
    """按顺序返回响应，记录每次调用的 messages 与 kwargs。"""
    calls = []

    def fake(provider, messages, **kwargs):
        calls.append({"messages": messages, "kwargs": kwargs})
        value = responses.pop(0)
        if isinstance(value, dict):
            return value
        return {
            "content": value,
            "latency_ms": 5,
            "status_code": 200,
            "error": None,
            "finish_reason": "stop",
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    return calls


def _empty():
    return {
        "content": "",
        "latency_ms": 5,
        "status_code": 200,
        "error": None,
        "finish_reason": "length",
    }


def _provider():
    return {"model": "fake", "disable_thinking": False, "base_url": "http://x/v1"}


def _wrong_verdict(refers_to="韩砚清", quote="姐姐在檐下缝衣"):
    return json.dumps(
        [
            {
                "id": 1,
                "verdict": "wrong",
                "refers_to": refers_to,
                "evidence": 1,
                "quote": quote,
                "reason": "指的是韩砚清",
            }
        ],
        ensure_ascii=False,
    )


def test_prune_drops_alias_absent_from_text():
    parsed = _parsed(
        new_aliases=[{"alias": "陆公子", "canonical": "陆知节"}],
    )
    result, issues = alias_check_service.prune(parsed, TEXT)
    assert result["characters"]["new_aliases"] == []
    assert issues[0]["alias"] == "陆公子"
    assert issues[0]["stage"] == "rule"
    assert "本章正文里没有" in issues[0]["reason"]


def test_prune_keeps_alias_present_in_text():
    parsed = _parsed(new_aliases=[{"alias": "姐姐", "canonical": "顾青珩"}])
    result, issues = alias_check_service.prune(parsed, TEXT)
    assert result["characters"]["new_aliases"] == [
        {"alias": "姐姐", "canonical": "顾青珩"}
    ]
    assert issues == []


def test_prune_tolerates_typeset_spaces():
    parsed = _parsed(new_aliases=[{"alias": "韩砚清", "canonical": "林晚"}])
    result, issues = alias_check_service.prune(parsed, "韩 砚 清 走 进 院 子。")
    assert result["characters"]["new_aliases"] != []
    assert issues == []


def test_prune_drops_bad_format_aliases():
    parsed = _parsed(
        new_aliases=[
            {"alias": "韩", "canonical": "韩砚清"},
            {"alias": "一个明显超过八个字的称呼", "canonical": "韩砚清"},
            {"alias": "A1", "canonical": "韩砚清"},
        ]
    )
    result, issues = alias_check_service.prune(parsed, TEXT)
    assert result["characters"]["new_aliases"] == []
    assert len(issues) == 3


def test_prune_drops_self_alias():
    parsed = _parsed(
        mentions=[{"name": "韩砚清", "aliases": ["韩砚清", "姐姐"], "role": ""}],
        new_aliases=[{"alias": "顾青珩", "canonical": "顾青珩"}],
    )
    result, issues = alias_check_service.prune(parsed, TEXT)
    assert result["characters"]["mentions"][0]["aliases"] == ["姐姐"]
    assert result["characters"]["new_aliases"] == []
    assert {item["reason"] for item in issues} == {"称谓与本名相同"}


def test_prune_filters_mention_aliases_but_keeps_mention():
    parsed = _parsed(
        mentions=[
            {"name": "韩砚清", "aliases": ["江大人", "姐姐", "阿清"], "role": "主角"}
        ]
    )
    result, issues = alias_check_service.prune(parsed, TEXT)
    mention = result["characters"]["mentions"][0]
    assert mention["name"] == "韩砚清"
    assert mention["aliases"] == ["姐姐"]
    assert [item["alias"] for item in issues] == ["江大人", "阿清"]


def test_prune_drops_multiple_indexes_without_skipping():
    parsed = _parsed(
        mentions=[{"name": "韩砚清", "aliases": ["甲", "姐姐", "乙", "丙"], "role": ""}]
    )
    result, _ = alias_check_service.prune(parsed, TEXT)
    assert result["characters"]["mentions"][0]["aliases"] == ["姐姐"]


def test_review_flags_wrong_without_named_evidence(monkeypatch):
    """只有 wrong、没有候选人名和原文依据：只标注，不删。"""
    _chat(
        monkeypatch,
        ['[{"id": 1, "verdict": "wrong", "reason": "指的是别人"}]'],
    )
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    result, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider()
    )
    assert error is None
    assert result["characters"]["mentions"][0]["aliases"] == ["姐姐"]
    assert issues[0]["action"] == "flagged"
    assert issues[0]["reason"] == "没有指出另一个人"


def test_review_default_flags_wrong_with_named_evidence(monkeypatch):
    """默认只标注：即使证据齐全也不删。"""
    _chat(monkeypatch, [_wrong_verdict()])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    result, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider()
    )
    assert error is None
    assert result["characters"]["mentions"][0]["aliases"] == ["姐姐"]
    assert issues[0]["action"] == "flagged"
    assert issues[0]["refers_to"] == "韩砚清"


def test_review_deletes_when_auto_delete_enabled(monkeypatch):
    _chat(monkeypatch, [_wrong_verdict()])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    result, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider(), auto_delete=True
    )
    assert error is None
    assert "姐姐" not in result["characters"]["mentions"][0]["aliases"]
    assert issues[0]["action"] == "dropped"
    assert issues[0]["refers_to"] == "韩砚清"


def test_review_accepts_candidate_label(monkeypatch):
    """refers_to 写 C2/[C2] 时按编号换成候选名。"""
    _chat(monkeypatch, [_wrong_verdict(refers_to="[C2]")])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    result, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider(), auto_delete=True
    )
    assert error is None
    assert "姐姐" not in result["characters"]["mentions"][0]["aliases"]
    assert issues[0]["action"] == "dropped"
    assert issues[0]["refers_to"] == "韩砚清"


def test_review_ignores_wrong_that_points_to_owner(monkeypatch):
    """模型说 wrong 却又指回本人：当作 unclear，不记噪音。"""
    _chat(monkeypatch, [_wrong_verdict(refers_to="顾青珩")])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    result, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider()
    )
    assert error is None
    assert result["characters"]["mentions"][0]["aliases"] == ["姐姐"]
    assert issues == []


def test_review_sends_candidates_and_marked_snippets(monkeypatch):
    calls = _chat(monkeypatch, ["[]"])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": "嫡女"},
        ]
    )
    alias_check_service.review(
        parsed,
        TEXT,
        _provider(),
        known_characters=[{"name": "韩砚清", "identity": "韩家嫡女"}],
        protagonists=["韩砚清"],
    )
    message = calls[0]["messages"][1]["content"]
    assert "候选：" in message
    assert "[C1] 顾青珩" in message
    assert "韩家嫡女" in message
    assert "主角" in message
    assert "【姐姐】" in message


def test_review_one_call_for_multiple_items(monkeypatch):
    calls = _chat(monkeypatch, ["[]"])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐", "侯爷", "姑娘"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    text = "姐姐、侯爷和姑娘都在院子里。"
    _, issues, error, meta = alias_check_service.review(
        parsed, text, _provider()
    )
    assert error is None
    assert issues == []
    assert meta["calls"] == 1
    assert len(calls) == 1
    message = calls[0]["messages"][1]["content"]
    assert "第1条" in message and "第3条" in message


def test_review_breaker_after_two_empty_results(monkeypatch):
    calls = _chat(monkeypatch, [_empty(), _empty(), _empty(), _empty()])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    state: dict = {}
    _, _, error, meta = alias_check_service.review(
        parsed, TEXT, _provider(), state=state
    )
    assert error
    assert state["empty_streak"] == 1
    assert meta["disabled"] is False

    _, _, error, meta = alias_check_service.review(
        parsed, TEXT, _provider(), state=state
    )
    assert error
    assert state["disabled"] is True
    assert meta["disabled"] is True
    before = len(calls)

    _, _, error, meta = alias_check_service.review(
        parsed, TEXT, _provider(), state=state
    )
    assert error is None
    assert meta["calls"] == 0
    assert meta["checked"] == 0
    assert len(calls) == before


def test_review_retries_with_larger_budget(monkeypatch):
    calls = _chat(monkeypatch, [_empty(), _wrong_verdict()])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    _, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider(), auto_delete=True
    )
    assert error is None
    assert [call["kwargs"]["max_tokens"] for call in calls] == [
        alias_check_service.CHECK_MAX_TOKENS,
        alias_check_service.CHECK_RETRY_TOKENS,
    ]
    assert issues[0]["action"] == "dropped"


def test_review_reports_budget_exhausted(monkeypatch):
    _chat(monkeypatch, [_empty(), _empty()])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    result, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider()
    )
    assert result["characters"]["mentions"][0]["aliases"] == ["姐姐"]
    assert issues == []
    assert "输出预算被推理占满" in error


def test_review_invalid_json_counts_as_failure(monkeypatch):
    _chat(monkeypatch, ["看不出"])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ]
    )
    state: dict = {}
    _, issues, error, meta = alias_check_service.review(
        parsed, TEXT, _provider(), state=state
    )
    assert issues == []
    assert "JSON" in error
    assert state["empty_streak"] == 1
    assert meta["disabled"] is False


def test_review_dedupes_mention_and_new_alias():
    """同一对在 mentions 与 new_aliases 各出现一次：只收集一条。"""
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ],
        new_aliases=[{"alias": "姐姐", "canonical": "顾青珩"}],
    )
    items = alias_check_service._collect_items(parsed, TEXT, None, None)
    assert len(items) == 1
    assert items[0].alias == "姐姐"
    assert len(items[0].extras) == 1


def test_auto_delete_removes_duplicate_holders(monkeypatch):
    _chat(monkeypatch, [_wrong_verdict()])
    parsed = _parsed(
        mentions=[
            {"name": "顾青珩", "aliases": ["姐姐"], "role": ""},
            {"name": "韩砚清", "aliases": [], "role": ""},
        ],
        new_aliases=[{"alias": "姐姐", "canonical": "顾青珩"}],
    )
    result, issues, error, _ = alias_check_service.review(
        parsed, TEXT, _provider(), auto_delete=True
    )
    assert error is None
    assert result["characters"]["mentions"][0]["aliases"] == []
    assert result["characters"]["new_aliases"] == []
    assert issues[0]["action"] == "dropped"


def test_build_report_dedupes_identical_issues():
    issue = {
        "alias": "姐姐",
        "owner": "顾青珩",
        "from": "mention",
        "stage": "rule",
        "action": "dropped",
        "reason": "本章正文里没有这个写法",
    }
    report = alias_check_service.build_report([issue, dict(issue)], [], None, {})
    assert report["dropped"] == 1
    assert len(report["issues"]) == 1


def test_run_rules_only_without_provider():
    """默认模式：只跑规则层，报告里没有任何证据/待送检字段。"""
    parsed = _parsed(new_aliases=[{"alias": "陆公子", "canonical": "陆知节"}])
    result, report = alias_check_service.run(None, parsed, TEXT)
    assert report["dropped"] == 1
    assert report["model"] is None
    assert report["issues"][0]["stage"] == "rule"
    assert report["checked"] == 0
    assert report["calls"] == 0
    assert "supported" not in report
    assert "pending" not in report
    assert "deferred" not in report


def test_run_reports_provider_error(monkeypatch):
    def broken(db, provider_id=None):
        from app.common.exceptions import BusinessException

        raise BusinessException(message="模型不存在", code=4040)

    monkeypatch.setattr(
        "app.services.llm_provider_service.get_active_provider_config", broken
    )
    parsed = _parsed(new_aliases=[{"alias": "陆公子", "canonical": "陆知节"}])
    result, report = alias_check_service.run(
        None, parsed, TEXT, check_provider_id=9
    )
    assert report["dropped"] == 1
    assert report["error"] == "模型不存在"


def test_run_report_counts_with_model(monkeypatch):
    _chat(monkeypatch, ['[{"id":1,"verdict":"unclear","reason":"看不出"}]'])
    monkeypatch.setattr(
        "app.services.llm_provider_service.get_active_provider_config",
        lambda db, provider_id=None: _provider(),
    )
    parsed = _parsed(
        mentions=[{"name": "韩砚清", "aliases": ["姐姐"], "role": ""}]
    )
    _, report = alias_check_service.run(
        None, parsed, TEXT, check_provider_id=1
    )
    assert report["checked"] == 1
    assert report["calls"] == 1
    assert report["flagged"] == 0
    assert report["error"] is None
    assert report["issues"] == []
    assert "supported" not in report


def test_extract_chapter_prunes_and_records(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(
            db, bodies=("林晚听见晚晚在门外喊她。", "第二章正文。", "第三章正文。")
        )
        payload = _payload("第一轮摘要。")
        payload["characters"]["new_aliases"].append(
            {"alias": "甄小姐", "canonical": "林晚"}
        )
        _fake_chat(monkeypatch, [json.dumps(payload, ensure_ascii=False)])

        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "success"
        assert result["alias_check"]["dropped"] == 1
        assert result["alias_check"]["checked"] == 0
        assert result["alias_check"]["model"] is None

        row = (
            db.query(ChapterExtraction)
            .filter_by(book_id=book_id, chapter_idx=0)
            .one()
        )
        aliases = [
            item["alias"]
            for item in row.parsed["characters"]["new_aliases"]
        ]
        assert aliases == ["晚晚"]
        assert row.alias_check["issues"][0]["alias"] == "甄小姐"
