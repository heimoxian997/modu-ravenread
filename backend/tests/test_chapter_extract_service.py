"""单章抽取服务测试：fake LLM，不联网、不加载嵌入模型。"""

from __future__ import annotations

import json

import pytest

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.chapter_summary import ChapterSummary
from app.models.llm_provider import LlmProvider
from app.models.summary_issue import SummaryIssue
from app.services import chapter_extract_prompt as prompt
from app.services import chapter_extract_service


def _payload(summary="第一章摘要。", with_card=True):
    return {
        "summary": {
            "summary": summary,
            "characters": ["林晚", "秦屿"],
            "cliffhanger": "门外传来脚步声。",
            "key_events": [
                "林晚在旧楼发现半张化验单，确认父亲曾参与T计划",
                "门外传来脚步声。",
            ],
        },
        "characters": {
            "mentions": [
                {"name": "林晚", "aliases": ["晚晚"], "role": "调查者"}
            ],
            "card_deltas": (
                [
                    {
                        "character": "林晚",
                        "identity": "前T计划成员",
                        "traits": ["冷静", "执着"],
                        "relations": ["与秦屿合作"],
                        "known_facts": ["失忆"],
                        "voice_sample": "我不知道。",
                        "arc": "开始追查",
                        "reason": "身份首次出现",
                    }
                ]
                if with_card
                else []
            ),
            "new_aliases": (
                [{"alias": "晚晚", "canonical": "林晚"}] if with_card else []
            ),
        },
        "lore": [
            {
                "name": "T计划",
                "category": "organization",
                "aliases": ["T"],
                "summary": "秘密研究计划。",
                "details": ["双胞胎研究"],
                "entities": ["林晚"],
            }
        ],
    }


def _create_book(
    db,
    bodies=("第一章正文。", "第二章正文。", "第三章正文。"),
    extra_body=None,
):
    titles = [f"第{index + 1}章 测试" for index in range(len(bodies))]
    entries = []
    source_parts = []
    cursor = 0
    for index, (title, body) in enumerate(zip(titles, bodies)):
        text = f"{title}\n{body}\n"
        entries.append((title, body, "main", cursor, cursor + len(text)))
        source_parts.append(text)
        cursor += len(text)
    if extra_body is not None:
        text = f"番外 测试\n{extra_body}\n"
        entries.append(("番外 测试", extra_body, "extra", cursor, cursor + len(text)))
        source_parts.append(text)
    source = "".join(source_parts)

    book = Book(
        title="摘要测试书",
        author="测试作者",
        synopsis="一个用于摘要测试的故事。",
        protagonists=["林晚"],
        front_matter="",
        filename="summary.txt",
        encoding="UTF-8",
        char_count=len(source),
        chapter_count=len(entries),
        main_chapter_count=len(bodies),
    )
    db.add(book)
    db.flush()
    for idx, (title, _body, kind, start, end) in enumerate(entries):
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=title,
                kind=kind,
                char_start=start,
                char_end=end,
                word_count=end - start,
            )
        )
    provider = LlmProvider(
        name="本地测试模型",
        preset_key="custom",
        base_url="http://127.0.0.1:11434/v1",
        model="mock-model",
        api_key_enc=None,
        is_active=True,
    )
    db.add(provider)
    db.commit()
    source_dir = UPLOADS_DIR / str(book.id)
    source_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / "source.txt").write_text(source, encoding="utf-8")
    return book.id


def _fake_chat(monkeypatch, responses):
    calls = []

    def fake(provider, messages, **kwargs):
        calls.append({"provider": provider, "messages": messages, **kwargs})
        response = responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            return response
        return {
            "content": response,
            "latency_ms": 12,
            "status_code": 200,
            "error": None,
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    return calls


def test_extract_success_and_known_character_replay(monkeypatch):
    with SessionLocal() as db:
        # 正文里要有「晚晚」，否则单章自检会把它当编造的称谓删掉
        book_id = _create_book(
            db,
            bodies=("林晚听见晚晚在门外喊她。", "第二章正文。", "第三章正文。"),
        )
        calls = _fake_chat(
            monkeypatch,
            [
                json.dumps(_payload("第一轮摘要。"), ensure_ascii=False),
                json.dumps(_payload("第二轮摘要。", with_card=False), ensure_ascii=False),
            ],
        )
        first = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert first["status"] == "success"
        assert first["summary"]["summary"] == "第一轮摘要。"
        assert first["summary"]["key_events"][0].startswith("林晚在旧楼")
        assert "门外传来脚步声。" in first["summary"]["key_events"]
        assert first["extraction"]["status"] == "success"
        assert db.query(ChapterSummary).count() == 1
        assert db.query(ChapterExtraction).count() == 1
        assert db.query(SummaryIssue).count() == 0

        second = chapter_extract_service.extract_chapter(db, book_id, 1)
        assert second["status"] == "success"
        payload = json.loads(calls[1]["messages"][1]["content"])
        assert "open_threads" not in payload
        assert "known_threads" not in payload
        known = payload["known_characters"]
        assert known
        assert known[0]["name"] == "林晚"
        assert "前T计划成员" == known[0]["identity"]
        assert "晚晚" in known[0]["aliases"]
        assert payload["previous_summary"].endswith("第一轮摘要。")
        # 第二章正文没有提到 T计划，按需筛选后不应出现在 known_lore 里
        assert payload["known_lore"] == []
        summary = db.query(ChapterSummary).order_by(ChapterSummary.chapter_idx).first()
        assert summary.characters == ["林晚", "秦屿"]


def test_parse_retry_then_success(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        calls = _fake_chat(
            monkeypatch,
            [
                "这不是 JSON",
                json.dumps(_payload("重试后的摘要。"), ensure_ascii=False),
            ],
        )
        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "success"
        assert result["summary"]["summary"] == "重试后的摘要。"
        assert len(calls) == 2
        assert calls[1]["messages"][-1]["content"] == prompt.PARSE_RETRY_HINT


def test_two_parse_failures_write_issue_and_raw(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _fake_chat(monkeypatch, ["坏输出一", "坏输出二"])
        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "parse_failed"
        assert result["raw_content"] == "坏输出二"
        assert result["summary"] is None
        assert db.query(ChapterSummary).count() == 0
        extraction = db.query(ChapterExtraction).one()
        assert extraction.status == "parse_failed"
        assert extraction.raw_content == "坏输出二"
        issue = db.query(SummaryIssue).one()
        assert issue.chapter_idx == 0
        assert issue.error


def test_reextract_failure_keeps_old_summary(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _fake_chat(
            monkeypatch, [json.dumps(_payload("旧摘要。"), ensure_ascii=False)]
        )
        first = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert first["status"] == "success"
        old_summary = db.query(ChapterSummary).one().summary

        _fake_chat(monkeypatch, ["坏一", "坏二"])
        failed = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert failed["status"] == "parse_failed"
        assert failed["summary"]["summary"] == old_summary
        assert db.query(ChapterSummary).one().summary == old_summary
        assert db.query(ChapterExtraction).one().status == "success"
        assert db.query(SummaryIssue).one().chapter_idx == 0


def test_http_failure_writes_issue(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        _fake_chat(
            monkeypatch,
            [
                {
                    "content": None,
                    "latency_ms": 8,
                    "status_code": 500,
                    "error": "模型服务返回 HTTP 500",
                }
            ],
        )
        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "failed"
        assert result["summary"] is None
        assert db.query(SummaryIssue).one().error.startswith("模型服务")
        assert db.query(ChapterSummary).count() == 0


def test_extra_empty_and_sequence(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(
            db, bodies=("正文一。", "正文二。", ""), extra_body="番外正文。"
        )
        _fake_chat(
            monkeypatch,
            [
                json.dumps(_payload("一。"), ensure_ascii=False),
                json.dumps(_payload("二。"), ensure_ascii=False),
            ],
        )
        with pytest.raises(BusinessException) as extra:
            chapter_extract_service.extract_chapter(db, book_id, 3)
        assert extra.value.code == 4000
        with pytest.raises(BusinessException) as empty:
            chapter_extract_service.extract_chapter(db, book_id, 2)
        assert "正文为空" in empty.value.message

        results = chapter_extract_service.extract_sequence(db, book_id, limit=2)
        assert [item["status"] for item in results] == ["success", "success"]
        assert db.query(ChapterSummary).count() == 2


def _timeout_result():
    return {
        "content": None,
        "latency_ms": 300000,
        "status_code": None,
        "error": "请求超时（300 秒），请检查服务地址或网络。",
        "error_code": "timeout",
    }


def test_timeout_retry_once_then_success(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        calls = _fake_chat(
            monkeypatch,
            [_timeout_result(), json.dumps(_payload("超时重试后的摘要。"), ensure_ascii=False)],
        )
        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "success"
        assert result["summary"]["summary"] == "超时重试后的摘要。"
        assert len(calls) == 2
        assert calls[0]["timeout"] == 300
        assert calls[1]["timeout"] == 120
        assert db.query(SummaryIssue).count() == 0


def test_timeout_retry_gives_up_after_two_calls(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        calls = _fake_chat(monkeypatch, [_timeout_result(), _timeout_result()])
        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "failed"
        assert "超时" in result["error"]
        assert len(calls) == 2
        issue = db.query(SummaryIssue).one()
        assert issue.chapter_idx == 0
        assert "超时" in issue.error
        assert db.query(ChapterSummary).count() == 0


def test_non_timeout_error_not_retried(monkeypatch):
    with SessionLocal() as db:
        book_id = _create_book(db)
        calls = _fake_chat(
            monkeypatch,
            [
                {
                    "content": None,
                    "latency_ms": 5,
                    "status_code": 429,
                    "error": "模型服务返回 HTTP 429",
                    "error_code": "http",
                }
            ],
        )
        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "failed"
        assert len(calls) == 1
        assert db.query(SummaryIssue).one().error.startswith("模型服务")


def test_known_lore_only_returns_entries_in_text():
    """只发正文提到的条目，且带最近一次的概述。"""
    with SessionLocal() as db:
        book_id = _create_book(db)
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=0,
                title="第1章",
                status="success",
                parsed={
                    "lore": [
                        {
                            "name": "金鱼符",
                            "category": "item",
                            "summary": "公主丢失的信物，禁卫军全力搜捕",
                        },
                        {
                            "name": "天玄宫",
                            "category": "organization",
                            "summary": "以香道立派的宗门",
                        },
                    ]
                },
            )
        )
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=1,
                title="第2章",
                status="success",
                parsed={
                    "lore": [
                        {
                            "name": "金鱼符",
                            "category": "item",
                            "summary": "可调动禁卫军的身份符信",
                        }
                    ]
                },
            )
        )
        db.commit()

        hit = chapter_extract_service._known_lore(
            db, book_id, 2, "她摸出金鱼符，递给对方。"
        )
        assert [item["name"] for item in hit] == ["金鱼符"]
        assert hit[0]["summary"] == "可调动禁卫军的身份符信"
        assert hit[0]["category"] == "item"

        miss = chapter_extract_service._known_lore(
            db, book_id, 2, "雨下了一整夜。"
        )
        assert miss == []


def test_fabricated_alias_does_not_drop_same_name_lore(monkeypatch):
    """规则层清掉编造称谓后，同名世界观条目不再被误删。"""
    with SessionLocal() as db:
        book_id = _create_book(db, bodies=("今天下雨，什么都没发生。",))
        payload = _payload("雨天。", with_card=False)
        payload["characters"]["mentions"] = []
        payload["characters"]["new_aliases"] = [
            {"alias": "小金", "canonical": "金鱼符"}
        ]
        payload["lore"] = [
            {
                "name": "金鱼符",
                "category": "item",
                "aliases": [],
                "summary": "公主丢失的信物。",
                "details": [],
                "entities": [],
            }
        ]
        _fake_chat(monkeypatch, [json.dumps(payload, ensure_ascii=False)])

        result = chapter_extract_service.extract_chapter(db, book_id, 0)
        assert result["status"] == "success"
        row = db.query(ChapterExtraction).one()
        assert [item["name"] for item in row.parsed["lore"]] == ["金鱼符"]
        assert row.parsed["characters"]["new_aliases"] == []
        assert row.alias_check["dropped"] == 1


def test_drop_character_lore():
    """人物名出现在 lore 里直接丢弃，历史人物也算。"""
    with SessionLocal() as db:
        book_id = _create_book(db)
        book = db.get(Book, book_id)
        db.add(
            ChapterExtraction(
                book_id=book_id,
                chapter_idx=0,
                title="第1章",
                status="success",
                parsed={
                    "characters": {
                        "mentions": [
                            {"name": "安平郡主", "aliases": [], "role": "宗室"}
                        ],
                        "new_aliases": [],
                    }
                },
            )
        )
        db.commit()
        parsed = {
            "characters": {
                "mentions": [],
                "new_aliases": [{"alias": "阿灼", "canonical": "韩砚清"}],
            },
            "lore": [
                {"name": "安平郡主", "category": "organization", "summary": "x"},
                {"name": "韩砚清", "category": "item", "summary": "y"},
                {"name": "金鱼符", "category": "item", "summary": "z"},
            ],
        }
        kept = chapter_extract_service._drop_character_lore(
            db, book, 1, parsed
        )
        assert [item["name"] for item in kept] == ["金鱼符"]
