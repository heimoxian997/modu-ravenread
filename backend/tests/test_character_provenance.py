"""人物卡 provenance、上限、时间线与 delta 检索测试。

数据全部为合成人物/合成事实，不引用真实书库内容。
"""

from __future__ import annotations

from app.database import SessionLocal
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_chapter_delta import CharacterChapterDelta
from app.services import (
    entity_index_service,
    entity_layer_service,
    entity_override_service,
)

FACTS_CHAPTER_1 = [f"fact{i}" for i in range(1, 26)]


def _payload(mentions, deltas, new_aliases=None):
    return {
        "characters": {
            "mentions": mentions,
            "card_deltas": deltas,
            "new_aliases": new_aliases or [],
        },
        "lore": [],
    }


def _build_book(db) -> int:
    book = Book(
        title="合成测试书",
        author="作者",
        synopsis="",
        protagonists=["甲某"],
        front_matter="",
        filename="synthetic.txt",
        encoding="UTF-8",
        char_count=0,
        chapter_count=3,
        main_chapter_count=3,
    )
    db.add(book)
    db.flush()
    for idx in range(3):
        db.add(
            Chapter(
                book_id=book.id,
                idx=idx,
                title=f"第{idx + 1}章",
                kind="main",
                char_start=0,
                char_end=10,
                word_count=10,
            )
        )
    payloads = [
        _payload(
            mentions=[{"name": "甲某", "aliases": ["小甲"], "role": "主角"}],
            deltas=[
                {
                    "character": "甲某",
                    "identity": "身份0",
                    "traits": ["trait0"],
                    "relations": ["rel0"],
                    "known_facts": ["fact0"],
                    "voice_sample": "voice0",
                    "arc": "arc0",
                    "reason": "",
                }
            ],
            new_aliases=[{"alias": "小甲", "canonical": "甲某"}],
        ),
        _payload(
            mentions=[
                {"name": "甲某", "aliases": ["小甲"], "role": ""},
                {"name": "乙某", "aliases": ["小乙"], "role": ""},
            ],
            deltas=[
                {
                    "character": "甲某",
                    "identity": "",
                    "traits": [],
                    "relations": [],
                    "known_facts": FACTS_CHAPTER_1,
                    "voice_sample": "",
                    "arc": "",
                    "reason": "",
                }
            ],
        ),
        _payload(
            mentions=[{"name": "甲某", "aliases": ["老甲"], "role": ""}],
            deltas=[
                {
                    "character": "甲某",
                    "identity": "",
                    "traits": [],
                    "relations": ["rel_late"],
                    "known_facts": ["fact_late"],
                    "voice_sample": "",
                    "arc": "",
                    "reason": "",
                }
            ],
            new_aliases=[{"alias": "老甲", "canonical": "甲某"}],
        ),
    ]
    for idx, parsed in enumerate(payloads):
        db.add(
            ChapterExtraction(
                book_id=book.id,
                chapter_idx=idx,
                title=f"第{idx + 1}章",
                status="success",
                parsed=parsed,
            )
        )
    db.commit()
    entity_layer_service.build_entity_layer(db, book.id)
    return book.id


def _character_id(db, book_id: int) -> int:
    row = (
        db.query(Character)
        .filter(Character.book_id == book_id, Character.canonical_name == "甲某")
        .one()
    )
    return row.id


def _delta_ids(db, book_id: int, character_id: int) -> list[int]:
    rows = (
        db.query(CharacterChapterDelta)
        .filter(
            CharacterChapterDelta.book_id == book_id,
            CharacterChapterDelta.character_id == character_id,
        )
        .order_by(CharacterChapterDelta.chapter_idx.asc())
        .all()
    )
    return [row.id for row in rows]


class _Point:
    def __init__(self, payload):
        self.payload = payload


def _fake_hits(payloads):
    return [
        {
            "point": _Point(payload),
            "dense_score": 0.9,
            "sparse_score": 0.8,
            "rrf": 0.5,
        }
        for payload in payloads
    ]


def test_card_totals_caps_and_provenance():
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)

        early = entity_layer_service.get_character_card(
            db, book_id, character_id, 1
        )
        assert early["totals"]["known_facts"] == 26
        assert early["capped_by_limit"]["known_facts"] is True
        assert len(early["known_facts"]) == 20
        assert early["last_changed_chapter"] == 1
        assert early["as_of_chapter"] == 1
        assert all(
            item["first_seen_chapter"] <= 1 for item in early["known_facts"]
        )

        at_two = entity_layer_service.get_character_card(
            db, book_id, character_id, 2
        )
        assert at_two["totals"]["relations"] == 2
        assert "老甲" in at_two["aliases"]


def test_card_aliases_are_gated_by_chapter():
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)
        early = entity_layer_service.get_character_card(
            db, book_id, character_id, 1
        )
        assert "小甲" in early["aliases"]
        assert "老甲" not in early["aliases"]

        detail = entity_layer_service.get_character_card(
            db, book_id, character_id, 2
        )
        assert "老甲" in detail["aliases"]


def test_detail_provenance_and_gated_character_fields():
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)
        detail = entity_override_service.get_character_detail(
            db, book_id, character_id, 1
        )
        assert detail["provenance"]["identity"]["chapter_idx"] == 0
        assert detail["totals"]["known_facts"] == 26
        assert detail["capped_by_limit"]["known_facts"] is True
        assert detail["character"]["last_seen_at_chapter"] == 1
        assert detail["character"]["last_seen_chapter"] == 1
        assert detail["character"]["mention_count_at_chapter"] == 2
        assert detail["character"]["mention_count"] == 2
        aliases = {item["alias"] for item in detail["character"]["alias_details"]}
        assert "小甲" in aliases
        assert "老甲" not in aliases
        assert detail["character"]["alias_details"][0]["last_seen_chapter"] <= 1


def test_timeline_gate_keyword_and_pagination():
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)

        page = entity_layer_service.get_character_timeline(
            db, book_id, character_id, 1, from_chapter=0, limit=1, offset=0
        )
        assert page["gate"] == 1
        assert page["total"] == 2
        assert page["truncated"] is True
        assert len(page["items"]) == 1
        assert page["items"][0]["chapter_idx"] == 0

        more = entity_layer_service.get_character_timeline(
            db, book_id, character_id, 1, limit=10, offset=1
        )
        assert more["truncated"] is False
        assert [item["chapter_idx"] for item in more["items"]] == [1]

        late = entity_layer_service.get_character_timeline(
            db, book_id, character_id, 1, keyword="fact_late"
        )
        assert late["total"] == 0

        filtered = entity_layer_service.get_character_timeline(
            db, book_id, character_id, 1, keyword="trait0"
        )
        assert filtered["total"] == 1
        assert filtered["items"][0]["chapter_idx"] == 0

        clamped = entity_layer_service.get_character_timeline(
            db, book_id, character_id, 1, to_chapter=99
        )
        assert clamped["to_chapter"] == 1
        assert all(item["chapter_idx"] <= 1 for item in clamped["items"])


def test_delta_search_can_return_multiple_rows(monkeypatch):
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)
        delta_ids = _delta_ids(db, book_id, character_id)

    payloads = [
        {
            "character_id": character_id,
            "delta_id": delta_ids[0],
            "chapter_idx": 0,
        },
        {
            "character_id": character_id,
            "delta_id": delta_ids[1],
            "chapter_idx": 1,
        },
    ]

    def fake_query(db, book_id, layer, query, recall, chapter_idx=None):
        return _fake_hits(payloads)

    monkeypatch.setattr(
        "app.services.entity_index_service._query_vectors", fake_query
    )
    with SessionLocal() as db:
        unique = entity_index_service.search_character_changes(
            db, book_id, "x", chapter_idx=1, top_k=2
        )
        assert len(unique) == 1

        all_rows = entity_index_service.search_character_changes(
            db, book_id, "x", chapter_idx=1, top_k=2, unique_characters=False
        )
        assert len(all_rows) == 2
        assert [row["chapter_idx"] for row in all_rows] == [0, 1]


def test_card_search_returns_match_and_provenance(monkeypatch):
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)
        delta_ids = _delta_ids(db, book_id, character_id)

    payload = [
        {
            "character_id": character_id,
            "delta_id": delta_ids[0],
            "chapter_idx": 0,
        }
    ]

    def fake_query(db, book_id, layer, query, recall, chapter_idx=None):
        return _fake_hits(payload)

    monkeypatch.setattr(
        "app.services.entity_index_service._query_vectors", fake_query
    )
    with SessionLocal() as db:
        hits = entity_index_service.search_character_index(
            db, book_id, "x", top_k=1, chapter_idx=1
        )
        assert hits
        hit = hits[0]
        assert hit["match_chapter_idx"] == 0
        assert hit["match"]["chapter_idx"] == 0
        assert hit["match"]["known_facts_add"] == ["fact0"]
        assert hit["provenance"]["identity"]["chapter_idx"] == 0
        assert hit["totals"]["known_facts"] == 26
        assert hit["as_of_chapter"] == 1


def test_timeline_api_gate_and_keyword(client):
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)

    response = client.get(
        f"/api/books/{book_id}/characters/{character_id}/changes",
        params={"chapter_idx": 1, "limit": 1, "keyword": "trait0"},
    ).json()
    assert response["code"] == 200
    data = response["data"]
    assert data["gate"] == 1
    assert data["items"][0]["chapter_idx"] == 0
    assert data["total"] == 1


def test_change_search_api_does_not_aggregate(client, monkeypatch):
    with SessionLocal() as db:
        book_id = _build_book(db)
        character_id = _character_id(db, book_id)
        delta_ids = _delta_ids(db, book_id, character_id)

    payloads = [
        {
            "character_id": character_id,
            "delta_id": delta_ids[0],
            "chapter_idx": 0,
        },
        {
            "character_id": character_id,
            "delta_id": delta_ids[1],
            "chapter_idx": 1,
        },
    ]

    def fake_query(db, book_id, layer, query, recall, chapter_idx=None):
        return _fake_hits(payloads)

    monkeypatch.setattr(
        "app.services.entity_index_service._query_vectors", fake_query
    )
    response = client.post(
        f"/api/books/{book_id}/character-change-search",
        json={"query": "x", "top_k": 2, "chapter_idx": 1},
    ).json()
    assert response["code"] == 200
    assert len(response["data"]) == 2
    assert [row["chapter_idx"] for row in response["data"]] == [0, 1]
