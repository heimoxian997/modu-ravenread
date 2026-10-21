"""正文切块 API 与持久化测试。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import UPLOADS_DIR
from app.database import SessionLocal, engine
from app.main import app
from app.models.chunk import Chunk


def _create_book(
    client,
    text: str,
    chapter_samples: list[str],
    extra_samples: list[str] | None = None,
    title: str = "切块测试书",
):
    upload = client.post(
        "/api/drafts",
        files={"file": ("切块样本.txt", text.encode("utf-8"), "text/plain")},
    ).json()
    assert upload["code"] == 200, upload
    token = upload["data"]["draft_token"]

    parsed = client.post(
        f"/api/drafts/{token}/parse",
        json={
            "chapter_samples": chapter_samples,
            "extra_samples": extra_samples or [],
            "fallback": False,
        },
    ).json()
    assert parsed["code"] == 200, parsed
    chapters = parsed["data"]["chapters"]

    confirmed = client.post(
        f"/api/drafts/{token}/confirm",
        json={
            "chapters": [
                {
                    "start": item["start"],
                    "end": item["end"],
                    "title": item["title"],
                    "kind": item["kind"],
                }
                for item in chapters
            ],
            "book": {"title": title, "author": "切块测试作者"},
        },
    ).json()
    assert confirmed["code"] == 200, confirmed
    return confirmed["data"]["book_id"]


def _source(book_id: int) -> str:
    return (UPLOADS_DIR / str(book_id) / "source.txt").read_text(encoding="utf-8")


def _chunk_rows(book_id: int) -> list[Chunk]:
    with SessionLocal() as db:
        return (
            db.query(Chunk)
            .filter(Chunk.book_id == book_id)
            .order_by(Chunk.chapter_idx.asc(), Chunk.level.asc(), Chunk.seq.asc())
            .all()
        )


def _build_book_from_sample(client, sample_novel):
    return _create_book(
        client,
        sample_novel,
        ["第一章 初见", "第1023章 大结局"],
        ["番外一 春日"],
    )


def test_build_overview_list_rebuild_and_clear(client, sample_novel):
    book_id = _build_book_from_sample(client, sample_novel)

    build = client.post(f"/api/books/{book_id}/chunks").json()
    assert build["code"] == 200, build
    stats = build["data"]
    assert stats["chapter_count"] == 5
    assert stats["parent_count"] > 0
    assert stats["child_count"] >= stats["parent_count"]
    assert 0 < stats["min_child_chars"] <= stats["max_child_chars"] <= 600
    assert stats["warnings"] == []

    overview = client.get(f"/api/books/{book_id}/chunks/overview").json()["data"]
    assert overview["built"] is True
    assert overview["chapter_count"] == 5
    assert overview["parent_count"] == stats["parent_count"]
    assert overview["child_count"] == stats["child_count"]
    assert len(overview["chapters"]) == 5
    assert all("番外" not in item["title"] for item in overview["chapters"])
    assert all(item["parent_count"] > 0 for item in overview["chapters"])

    source = _source(book_id)
    chapter_list = client.get(f"/api/books/{book_id}/chapters").json()["data"]
    main_chapters = [item for item in chapter_list if item["kind"] == "main"]
    extra_indexes = {item["idx"] for item in chapter_list if item["kind"] == "extra"}

    rows = _chunk_rows(book_id)
    assert all(row.chapter_idx not in extra_indexes for row in rows)
    for row in rows:
        assert source[row.char_start : row.char_end] == row.text
        if row.level == "parent":
            assert len(row.text) <= 1500
            assert row.parent_id is None
        else:
            assert len(row.text) <= 600
            assert row.parent_id is not None

    for chapter in main_chapters:
        response = client.get(
            f"/api/books/{book_id}/chunks", params={"chapter_idx": chapter["idx"]}
        ).json()
        assert response["code"] == 200, response
        detail = response["data"]
        assert detail["title"] == chapter["title"]
        assert detail["parents"]

        parent_ids = {parent["id"] for parent in detail["parents"]}
        assert [parent["seq"] for parent in detail["parents"]] == list(
            range(len(detail["parents"]))
        )

        child_seq = []
        for parent in detail["parents"]:
            assert parent["char_start"] >= chapter["start"]
            assert parent["char_end"] <= chapter["end"]
            assert source[parent["char_start"] : parent["char_end"]] == parent["text"]
            assert parent["children"]

            previous_end = None
            for child in parent["children"]:
                assert child["seq"] >= 0
                child_seq.append(child["seq"])
                assert source[child["char_start"] : child["char_end"]] == child["text"]
                assert parent["char_start"] <= child["char_start"]
                assert child["char_end"] <= parent["char_end"]
                assert len(child["text"]) <= 600
                if previous_end is not None:
                    overlap = previous_end - child["char_start"]
                    assert 0 < overlap <= 100
                previous_end = child["char_end"]
            assert parent["id"] in parent_ids
        assert child_seq == list(range(len(child_seq)))

    before_rows = _chunk_rows(book_id)
    assert len(before_rows) == stats["parent_count"] + stats["child_count"]

    rebuild = client.post(f"/api/books/{book_id}/chunks").json()
    assert rebuild["code"] == 200
    after_rows = _chunk_rows(book_id)
    assert len(after_rows) == rebuild["data"]["parent_count"] + rebuild["data"]["child_count"]
    assert len(after_rows) == len(before_rows)
    assert len({(row.book_id, row.version, row.chapter_idx, row.level, row.seq) for row in after_rows}) == len(after_rows)

    cleared = client.delete(f"/api/books/{book_id}/chunks").json()
    assert cleared["code"] == 200
    assert cleared["data"]["deleted"] == len(after_rows)
    assert _chunk_rows(book_id) == []

    empty_overview = client.get(f"/api/books/{book_id}/chunks/overview").json()["data"]
    assert empty_overview["built"] is False
    assert empty_overview["chapter_count"] == 0
    assert empty_overview["parent_count"] == 0
    assert empty_overview["child_count"] == 0
    assert len(empty_overview["chapters"]) == 5


def test_empty_body_chapter_is_skipped_with_warning(client):
    text = "第一章 空章\n第二章 正文\n这是第二章的正文内容。\n"
    book_id = _create_book(
        client,
        text,
        ["第一章 空章", "第二章 正文"],
        title="空章测试书",
    )

    build = client.post(f"/api/books/{book_id}/chunks").json()
    assert build["code"] == 200, build
    assert build["data"]["chapter_count"] == 1
    assert build["data"]["parent_count"] > 0
    assert any(
        item["chapter_idx"] == 0 and item["code"] == "empty_body"
        for item in build["data"]["warnings"]
    )

    overview = client.get(f"/api/books/{book_id}/chunks/overview").json()["data"]
    assert overview["built"] is True
    assert overview["chapter_count"] == 1
    assert len(overview["chapters"]) == 2
    assert any(
        item["chapter_idx"] == 0 and item["code"] == "empty_body"
        for item in overview["warnings"]
    )
    assert overview["chapters"][0]["parent_count"] == 0

    chapter_zero = client.get(
        f"/api/books/{book_id}/chunks", params={"chapter_idx": 0}
    ).json()
    assert chapter_zero["code"] == 200
    assert chapter_zero["data"]["parents"] == []


def test_missing_book_chapter_and_source(client, sample_novel):
    for method, path in [
        ("post", "/api/books/99999/chunks"),
        ("get", "/api/books/99999/chunks/overview"),
        ("get", "/api/books/99999/chunks?chapter_idx=0"),
        ("delete", "/api/books/99999/chunks"),
    ]:
        response = getattr(client, method)(path).json()
        assert response["code"] == 4040, (method, path, response)

    book_id = _build_book_from_sample(client, sample_novel)
    missing_chapter = client.get(
        f"/api/books/{book_id}/chunks", params={"chapter_idx": 99999}
    ).json()
    assert missing_chapter["code"] == 4040

    source_path = UPLOADS_DIR / str(book_id) / "source.txt"
    source_path.unlink()
    no_source = client.post(f"/api/books/{book_id}/chunks").json()
    assert no_source["code"] == 4100


def test_chapter_idx_is_required(client, sample_novel):
    book_id = _build_book_from_sample(client, sample_novel)
    response = client.get(f"/api/books/{book_id}/chunks")
    assert response.status_code == 422
    assert response.json()["code"] == 422


def test_delete_book_cascades_chunks(client, sample_novel):
    book_id = _build_book_from_sample(client, sample_novel)
    build = client.post(f"/api/books/{book_id}/chunks").json()
    assert build["code"] == 200
    assert _chunk_rows(book_id)

    deleted = client.delete(f"/api/books/{book_id}").json()
    assert deleted["code"] == 200
    assert _chunk_rows(book_id) == []


def test_chunks_persist_after_app_reopen(client, sample_novel):
    book_id = _build_book_from_sample(client, sample_novel)
    build = client.post(f"/api/books/{book_id}/chunks").json()
    assert build["code"] == 200

    engine.dispose()
    with TestClient(app) as reopened:
        overview = reopened.get(
            f"/api/books/{book_id}/chunks/overview"
        ).json()["data"]
        assert overview["built"] is True
        assert overview["chapter_count"] == 5
        chapter = reopened.get(
            f"/api/books/{book_id}/chunks", params={"chapter_idx": 0}
        ).json()["data"]
        assert chapter["parents"]
