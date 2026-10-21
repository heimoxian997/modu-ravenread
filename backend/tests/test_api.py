"""API 全流程测试：上传 → 解析 → 调整 → 确认 → 读取 → 编辑 → 删除。"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import UPLOADS_DIR
from app.database import SessionLocal, engine
from app.main import app
from app.models.chapter import Chapter


def _parse(client, token, chapter_samples=None, extra_samples=None, fallback=False):
    response = client.post(
        f"/api/drafts/{token}/parse",
        json={
            "chapter_samples": chapter_samples or ["第一章 初见", "第1023章 大结局"],
            "extra_samples": extra_samples or [],
            "fallback": fallback,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == 200, payload
    return payload["data"]


def _confirm(client, token, chapters, title="测试书"):
    response = client.post(
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
            "book": {
                "title": title,
                "author": "测试作者",
                "synopsis": "一段测试简介",
                "protagonists": ["沈秋", "陆遥"],
            },
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["code"] == 200, payload
    return payload["data"]["book_id"]


def test_upload_rejects_non_txt(client):
    response = client.post(
        "/api/drafts",
        files={"file": ("bad.pdf", b"not a novel", "application/pdf")},
    )
    assert response.json()["code"] == 4000


def test_upload_path_traversal_is_sanitized(client, sample_novel):
    response = client.post(
        "/api/drafts",
        files={
            "file": (
                r"..\..\危险小说.txt",
                sample_novel.encode("utf-8"),
                "text/plain",
            )
        },
    )
    payload = response.json()
    assert payload["code"] == 200
    assert payload["data"]["filename"] == "危险小说.txt"
    assert not Path("危险小说.txt").exists()


def test_upload_gbk_and_parse(client, sample_novel):
    response = client.post(
        "/api/drafts",
        files={
            "file": (
                "gbk小说.txt",
                sample_novel.encode("gbk"),
                "text/plain",
            )
        },
    )
    payload = response.json()
    assert payload["code"] == 200
    assert payload["data"]["encoding"] == "GB18030"
    assert "初见" in payload["data"]["head"]

    token = payload["data"]["draft_token"]
    result = _parse(client, token, ["第一章 初见", "第二章 离京"], ["番外一 春日"])
    assert result["stats"]["main_chapter_count"] == 5
    assert result["stats"]["extra_chapter_count"] == 2


def test_full_flow_and_adjustments(client, sample_novel, upload_novel):
    draft = upload_novel(sample_novel)
    token = draft["draft_token"]
    result = _parse(
        client,
        token,
        ["第一章 初见", "第1023章 大结局"],
        ["番外一 春日"],
    )
    chapters = result["chapters"]
    assert len(chapters) == 7

    # 预览接口按偏移取正文
    first = chapters[0]
    preview = client.get(
        f"/api/drafts/{token}/content",
        params={"start": first["start"], "end": first["end"]},
    ).json()
    assert preview["code"] == 200
    assert "初见" in preview["data"]["text"]

    # 手工调整：改名、正文/番外切换、合并前两章、拆分第三章
    adjusted = [dict(item) for item in chapters]
    adjusted[0]["title"] = "重命名后的第一章"
    adjusted[1]["kind"] = "extra"
    adjusted = [
        {
            "start": adjusted[0]["start"],
            "end": adjusted[1]["end"],
            "title": "合并章",
            "kind": "main",
        },
        *adjusted[2:],
    ]
    target = adjusted[1]
    split_at = target["start"] + max(2, (target["end"] - target["start"]) // 2)
    adjusted = [
        adjusted[0],
        {
            "start": target["start"],
            "end": split_at,
            "title": "拆分上",
            "kind": "main",
        },
        {
            "start": split_at,
            "end": target["end"],
            "title": "拆分下",
            "kind": "main",
        },
        *adjusted[2:],
    ]

    book_id = _confirm(client, token, adjusted, title="调整测试书")

    # 草稿已清理
    gone = client.post(f"/api/drafts/{token}/parse", json={
        "chapter_samples": ["第一章 初见"],
        "extra_samples": [],
    }).json()
    assert gone["code"] == 4040

    books = client.get("/api/books").json()["data"]
    assert len(books) == 1
    assert books[0]["title"] == "调整测试书"
    assert books[0]["chapter_count"] == len(adjusted)

    detail = client.get(f"/api/books/{book_id}").json()["data"]
    assert detail["author"] == "测试作者"
    # 原第二章改为番外后与第一章合并，正文减少 1；再拆分一个正文，正文仍为 5。
    assert detail["main_chapter_count"] == 5
    assert detail["extra_chapter_count"] == 2
    assert detail["protagonists"] == ["沈秋", "陆遥"]

    chapter_list = client.get(f"/api/books/{book_id}/chapters").json()["data"]
    assert chapter_list[0]["title"] == "合并章"
    assert chapter_list[1]["title"] == "拆分上"
    assert chapter_list[2]["title"] == "拆分下"
    assert chapter_list[1]["end"] == chapter_list[2]["start"]

    chapter_content = client.get(f"/api/books/{book_id}/chapters/1").json()["data"]
    assert chapter_content["title"] == "拆分上"
    assert chapter_content["content"]

    # 元数据编辑
    patch = client.patch(
        f"/api/books/{book_id}",
        json={
            "title": "改名后的书",
            "author": "新作者",
            "synopsis": "",
            "protagonists": ["沈秋"],
        },
    ).json()
    assert patch["code"] == 200
    assert patch["data"]["title"] == "改名后的书"
    assert patch["data"]["synopsis"] is None

    source_path = UPLOADS_DIR / str(book_id) / "source.txt"
    assert source_path.exists()

    delete = client.delete(f"/api/books/{book_id}").json()
    assert delete["data"]["deleted"] is True
    assert not source_path.exists()
    assert client.get(f"/api/books/{book_id}").json()["code"] == 4040
    with SessionLocal() as db:
        assert db.query(Chapter).filter(Chapter.book_id == book_id).count() == 0


def test_invalid_boundaries_do_not_write(client, sample_novel, upload_novel):
    draft = upload_novel(sample_novel)
    result = _parse(client, draft["draft_token"])
    chapters = result["chapters"]
    bad = [
        {"start": chapters[0]["start"], "end": chapters[0]["end"], "title": "", "kind": "main"},
        {
            "start": chapters[0]["start"] + 1,
            "end": chapters[1]["end"],
            "title": "",
            "kind": "main",
        },
        *[
            {"start": item["start"], "end": item["end"], "title": item["title"], "kind": item["kind"]}
            for item in chapters[2:]
        ],
    ]
    response = client.post(
        f"/api/drafts/{draft['draft_token']}/confirm",
        json={
            "chapters": bad,
            "book": {"title": "不应入库"},
        },
    ).json()
    assert response["code"] == 4005
    assert response["data"]["errors"]
    assert client.get("/api/books").json()["data"] == []


def test_parse_zero_hit_and_fallback(client, upload_novel):
    text = "没有章节标题的文本。" * 500
    draft = upload_novel(text)
    zero = _parse(client, draft["draft_token"], fallback=False)
    assert zero["chapters"] == []
    assert any(item["code"] == "no_chapters" for item in zero["warnings"])

    fallback = _parse(client, draft["draft_token"], fallback=True)
    assert fallback["rules"]["fallback"] is True
    assert len(fallback["chapters"]) >= 2
    assert fallback["chapters"][0]["start"] == 0
    assert fallback["chapters"][-1]["end"] == len(text)


def test_persistence_after_app_reopen(client, sample_novel, upload_novel):
    draft = upload_novel(sample_novel)
    result = _parse(client, draft["draft_token"])
    book_id = _confirm(client, draft["draft_token"], result["chapters"], title="持久化测试")

    # 模拟进程重启：释放连接池，再以全新的 TestClient 生命周期读取同一 SQLite 文件。
    engine.dispose()

    with TestClient(app) as reopened:
        books = reopened.get("/api/books").json()["data"]
        assert any(item["id"] == book_id and item["title"] == "持久化测试" for item in books)
        detail = reopened.get(f"/api/books/{book_id}").json()["data"]
        assert detail["chapter_count"] == len(result["chapters"])
        chapter = reopened.get(f"/api/books/{book_id}/chapters/0").json()["data"]
        assert chapter["content"]


def test_validation_error_is_422(client):
    response = client.post(
        "/api/drafts/not-a-token/parse",
        json={"chapter_samples": []},
    )
    assert response.status_code == 422
    assert response.json()["code"] == 422


def test_parse_requires_draft(client):
    response = client.post(
        "/api/drafts/" + "0" * 32 + "/parse",
        json={"chapter_samples": ["第一章 初见"]},
    )
    payload = response.json()
    assert payload["code"] == 4040
