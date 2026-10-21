"""别名核对规则层测试。"""

from __future__ import annotations

from app.config import UPLOADS_DIR
from app.database import SessionLocal
from app.models.book import Book
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_alias_observation import CharacterAliasObservation
from app.services import alias_audit_service, text_window_service
from app.models.chapter import Chapter

TITLE_ONE = "第1章 进门"
BODY_ONE = "韩砚清走进院子。韩叔在檐下。姐姐在缝衣。"
TITLE_TWO = "第2章 夜里"
BODY_TWO = "姐姐抬头。韩砚清没有说话。"
SOURCE = f"{TITLE_ONE}\n{BODY_ONE}\n{TITLE_TWO}\n{BODY_TWO}"

SECOND_START = len(TITLE_ONE) + 1 + len(BODY_ONE) + 1


def _make_book(db) -> tuple[int, dict[str, int]]:
    text_window_service.clear_cache()
    book = Book(
        title="核对测试",
        author="作者",
        protagonists=[],
        synopsis="",
        front_matter="",
        filename="audit.txt",
        encoding="UTF-8",
        char_count=len(SOURCE),
        chapter_count=2,
        main_chapter_count=2,
    )
    db.add(book)
    db.commit()
    path = UPLOADS_DIR / str(book.id) / "source.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SOURCE, encoding="utf-8")

    db.add_all(
        [
            Chapter(
                book_id=book.id,
                idx=0,
                title=TITLE_ONE,
                kind="main",
                char_start=0,
                char_end=SECOND_START,
                word_count=SECOND_START,
            ),
            Chapter(
                book_id=book.id,
                idx=1,
                title=TITLE_TWO,
                kind="main",
                char_start=SECOND_START,
                char_end=len(SOURCE),
                word_count=len(SOURCE) - SECOND_START,
            ),
        ]
    )
    names = {"韩砚清": 0, "韩叔平": 0, "云娘": 0}
    for name in names:
        character = Character(book_id=book.id, canonical_name=name)
        db.add(character)
        db.flush()
        names[name] = character.id
    db.commit()
    return book.id, names


def _alias(db, book_id: int, alias: str, character_id: int, total: int):
    db.add(
        CharacterAlias(
            book_id=book_id,
            character_id=character_id,
            alias=alias,
            first_seen_chapter=0,
            last_seen_chapter=1,
            occurrence_count=total,
            source="mention",
        )
    )


def _observe(
    db,
    book_id: int,
    alias: str,
    character_id: int,
    chapter_idx: int,
    count: int,
):
    db.add(
        CharacterAliasObservation(
            book_id=book_id,
            character_id=character_id,
            alias=alias,
            chapter_idx=chapter_idx,
            count=count,
            source="mention",
        )
    )


def _audit(db, book_id: int) -> dict:
    return alias_audit_service.audit_aliases(db, book_id)


def _find(report: dict, alias: str, character_id: int) -> dict | None:
    for item in report["suspects"] + report["weak"]:
        if item["alias"] == alias and item["character_id"] == character_id:
            return item
    return None


def test_rule_a_flags_alias_contained_in_other_name():
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "韩叔", ids["韩砚清"], 1)
        _observe(db, book_id, "韩叔", ids["韩砚清"], 0, 1)
        db.commit()

        item = _find(_audit(db, book_id), "韩叔", ids["韩砚清"])
        assert item is not None
        assert item["rules"] == ["A"]
        # 子串只是线索，先归到待复核，不直接判错
        assert item["priority"] == "low"
        assert "韩叔平" in item["reasons"][0]
        # 原文片段给到，方便人工核
        assert item["samples"][0]["chapter_idx"] == 0
        assert "韩叔" in item["samples"][0]["text"]


def test_rule_a_exact_name_collision_is_high():
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "韩叔平", ids["韩砚清"], 3)
        _observe(db, book_id, "韩叔平", ids["韩砚清"], 0, 3)
        db.commit()

        item = _find(_audit(db, book_id), "韩叔平", ids["韩砚清"])
        assert item is not None
        assert item["rules"][0] == "A"
        assert item["priority"] == "high"
        assert "本名" in item["reasons"][0]


def test_rule_c_flags_alias_missing_from_observed_chapters():
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "阿姐", ids["韩砚清"], 2)
        _observe(db, book_id, "阿姐", ids["韩砚清"], 0, 1)
        _observe(db, book_id, "阿姐", ids["韩砚清"], 1, 1)
        db.commit()

        item = _find(_audit(db, book_id), "阿姐", ids["韩砚清"])
        assert item is not None
        assert "C" in item["rules"]
        assert item["text_evidence"]["checked"] == [0, 1]
        assert item["text_evidence"]["found"] == []
        assert item["text_evidence"]["in_book"] is False
        assert "全书正文里都没有" in item["reasons"][0]
        assert item["samples"] == []


def test_rule_c_separates_chapter_mismatch_from_book_absence():
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        # 「缝衣」只在第 1 章出现，却记在第 2 章
        _alias(db, book_id, "缝衣", ids["云娘"], 1)
        _observe(db, book_id, "缝衣", ids["云娘"], 1, 1)
        db.commit()

        item = _find(_audit(db, book_id), "缝衣", ids["云娘"])
        assert item is not None
        assert "C" in item["rules"]
        assert item["text_evidence"]["in_book"] is True
        assert item["text_evidence"]["book_hits"] == 1
        assert "章号或证据对不上" in item["reasons"][0]
        # 观测章里没有，就到全书里取样，复核时手里要有原文
        assert item["samples"]
        assert item["samples"][0]["chapter_number"] == 1


def test_rule_b_flags_single_char_and_long_alias():
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "韩", ids["韩砚清"], 5)
        _observe(db, book_id, "韩", ids["韩砚清"], 0, 5)
        long_alias = "一个明显超过八个字的称呼"
        _alias(db, book_id, long_alias, ids["云娘"], 2)
        _observe(db, book_id, long_alias, ids["云娘"], 1, 2)
        db.commit()

        report = _audit(db, book_id)
        single = _find(report, "韩", ids["韩砚清"])
        assert single is not None
        assert single["rules"] == ["B"]
        long_item = _find(report, long_alias, ids["云娘"])
        assert long_item is not None
        assert "B" in long_item["rules"]
        assert report["single_char_aliases"] == 1


def test_rule_d_flags_thin_evidence_among_shared_alias():
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "姐姐", ids["云娘"], 9)
        _observe(db, book_id, "姐姐", ids["云娘"], 0, 5)
        _observe(db, book_id, "姐姐", ids["云娘"], 1, 4)
        _alias(db, book_id, "姐姐", ids["韩砚清"], 1)
        _observe(db, book_id, "姐姐", ids["韩砚清"], 1, 1)
        db.commit()

        report = _audit(db, book_id)
        thin = _find(report, "姐姐", ids["韩砚清"])
        assert thin is not None
        assert thin["rules"] == ["D"]
        assert thin["priority"] == "low"
        assert thin["owner_count"] == 2
        # 主要归属不该被标记
        main = _find(report, "姐姐", ids["云娘"])
        assert main is None


def test_clean_mapping_is_not_flagged():
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "韩砚清", ids["韩砚清"], 4)
        _observe(db, book_id, "韩砚清", ids["韩砚清"], 0, 2)
        _observe(db, book_id, "韩砚清", ids["韩砚清"], 1, 2)
        db.commit()

        report = _audit(db, book_id)
        assert report["totals"]["suspects"] == 0
        assert report["totals"]["clean"] == 1


def test_missing_book_raises_4040():
    with SessionLocal() as db:
        try:
            alias_audit_service.audit_aliases(db, 999999)
        except Exception as exc:
            assert getattr(exc, "code", None) == 4040
        else:
            raise AssertionError("书籍不存在应当报错")


def _fake_provider(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.llm_provider_service.get_active_provider_config",
        lambda db, provider_id=None: {
            "provider_id": 9,
            "provider_name": "测试模型",
            "base_url": "http://127.0.0.1:1/v1",
            "model": "fake-model",
            "api_key": "k",
            "is_local": True,
            "disable_thinking": False,
        },
    )


def _fake_llm(monkeypatch, content: str):
    seen: dict = {}

    def fake(provider, messages, **kwargs):
        seen["provider"] = provider
        seen["messages"] = messages
        return {
            "content": content,
            "latency_ms": 5,
            "status_code": 200,
            "error": None,
        }

    monkeypatch.setattr("app.services.llm_service.chat_completion", fake)
    return seen


def test_review_attaches_verdicts(monkeypatch):
    _fake_provider(monkeypatch)
    seen = _fake_llm(
        monkeypatch,
        '[{"id": 1, "verdict": "wrong", "reason": "片段里是别人"}]',
    )
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "韩叔", ids["韩砚清"], 1)
        _observe(db, book_id, "韩叔", ids["韩砚清"], 0, 1)
        db.commit()

        result = alias_audit_service.review_suspects(db, book_id, limit=5)
        assert result["reviewed"] == 1
        assert result["counts"]["wrong"] == 1
        assert result["items"][0]["review"]["reason"] == "片段里是别人"
        assert result["errors"] == []
        # 复核必须关掉思考，否则一个是非判断要烧掉几倍 token
        assert seen["provider"]["disable_thinking"] is True
        user_message = seen["messages"][1]["content"]
        assert "韩叔" in user_message and "韩砚清" in user_message


def test_review_degrades_when_model_returns_garbage(monkeypatch):
    _fake_provider(monkeypatch)
    _fake_llm(monkeypatch, "我看不出这些片段在说什么。")
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        _alias(db, book_id, "韩叔", ids["韩砚清"], 1)
        _observe(db, book_id, "韩叔", ids["韩砚清"], 0, 1)
        db.commit()

        result = alias_audit_service.review_suspects(db, book_id, limit=5)
        assert result["counts"]["unclear"] == 1


def test_review_skips_items_without_evidence(monkeypatch):
    _fake_provider(monkeypatch)
    _fake_llm(monkeypatch, "[]")
    with SessionLocal() as db:
        book_id, ids = _make_book(db)
        # 「阿姐」全书没有，取不到原文片段，送检没有意义
        _alias(db, book_id, "阿姐", ids["韩砚清"], 2)
        _observe(db, book_id, "阿姐", ids["韩砚清"], 0, 2)
        _alias(db, book_id, "韩叔", ids["韩砚清"], 1)
        _observe(db, book_id, "韩叔", ids["韩砚清"], 0, 1)
        db.commit()

        result = alias_audit_service.review_suspects(db, book_id, limit=5)
        assert result["reviewed"] == 1
        assert result["items"][0]["alias"] == "韩叔"
