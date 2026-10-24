"""RavenRead MCP server（stdio）"""

from __future__ import annotations

import argparse
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
TIMEOUT = httpx.Timeout(120.0, connect=5.0)

mcp = FastMCP(
    "ravenread",
    instructions=(
        "本地小说伴读工具。先调用 get_reading_context 取得当前书和章节，"
        "再使用其他工具。所有结果只覆盖读者当前章及以前。"
    ),
)


def _base_url() -> str:
    return os.environ.get("RAVENREAD_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        with httpx.Client(base_url=_base_url(), timeout=TIMEOUT) as client:
            response = client.request(method, path, **kwargs)
        body = response.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": "backend_unavailable",
            "message": f"无法连接后端：{exc}",
        }
    if not isinstance(body, dict):
        return {
            "ok": False,
            "error": "backend_invalid_response",
            "message": "后端返回格式不正确。",
        }
    if body.get("code") != 200:
        return {
            "ok": False,
            "error": "backend_error",
            "message": body.get("message") or "后端请求失败。",
            "data": body.get("data"),
        }
    return {"ok": True, "data": body.get("data")}


def _context(book_id: int | None) -> dict[str, Any]:
    if book_id is None:
        result = _request("GET", "/api/reading-context")
    else:
        result = _request("GET", f"/api/books/{book_id}/progress")
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    if not data.get("available"):
        return {
            "ok": False,
            "error": "no_reading_context",
            "message": "请先在阅读器打开一本书，再让外部助手查询。",
        }
    return {"ok": True, "data": data}


def _tool(book_id: int | None, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    context = _context(book_id)
    if not context.get("ok"):
        return context
    progress = context["data"]
    resolved_book = int(progress["book_id"])
    result = _request(
        "POST",
        f"/api/books/{resolved_book}/agent/tool",
        json={"tool": tool, "args": args},
    )
    if not result.get("ok"):
        return result
    return result.get("data") or {
        "ok": False,
        "error": "empty_response",
        "message": "后端没有返回工具结果。",
    }


@mcp.tool()
def list_books() -> dict[str, Any]:
    """列出本地书库。"""
    return _request("GET", "/api/books")


@mcp.tool()
def get_reading_context(book_id: int | None = None) -> dict[str, Any]:
    """返回当前阅读的书、章节、位置和安全章。建议先调用它。"""
    return _context(book_id)


@mcp.tool()
def get_capabilities(book_id: int | None = None) -> dict[str, Any]:
    """返回当前书可用的伴读工具。"""
    context = _context(book_id)
    if not context.get("ok"):
        return context
    data = context["data"]
    return _request("GET", f"/api/books/{int(data['book_id'])}/agent/capabilities")


@mcp.tool()
def read_around(
    anchor: str | None = None,
    radius: int = 1,
    book_id: int | None = None,
) -> dict[str, Any]:
    """读当前章光标附近的原文；anchor 可选，radius 0-2。"""
    return _tool(
        book_id,
        "read_around",
        {"anchor": anchor, "radius": radius},
    )


@mcp.tool()
def read_chapter(
    chapter_idx: int,
    offset: int = 0,
    book_id: int | None = None,
) -> dict[str, Any]:
    """按章号读取某一章正文；offset 是章内偏移，单次最多 1200 字。"""
    return _tool(
        book_id,
        "read_chapter",
        {"chapter_idx": chapter_idx, "offset": offset},
    )


@mcp.tool()
def search_text(query: str, book_id: int | None = None) -> dict[str, Any]:
    """在正文里找原句/场景，返回 2 个完整父块。"""
    return _tool(book_id, "search_text", {"query": query})


@mcp.tool()
def search_summaries(query: str, book_id: int | None = None) -> dict[str, Any]:
    """在章摘要里做语义检索，返回 3 条。"""
    return _tool(book_id, "search_summaries", {"query": query})


@mcp.tool()
def get_chapter_summary(chapter_idx: int, book_id: int | None = None) -> dict[str, Any]:
    """精确取某一章的摘要。"""
    return _tool(
        book_id,
        "get_chapter_summary",
        {"chapter_idx": chapter_idx},
    )


@mcp.tool()
def get_character(
    name: str,
    as_of_chapter: int | None = None,
    book_id: int | None = None,
) -> dict[str, Any]:
    """取人物在某一章时点的带章号卡。"""
    return _tool(
        book_id,
        "get_character",
        {"name": name, "as_of_chapter": as_of_chapter},
    )


@mcp.tool()
def get_character_timeline(
    name: str,
    from_chapter: int | None = None,
    to_chapter: int | None = None,
    keyword: str | None = None,
    limit: int = 10,
    book_id: int | None = None,
) -> dict[str, Any]:
    """按章区间看一个人物的变化。"""
    return _tool(
        book_id,
        "get_character_timeline",
        {
            "name": name,
            "from_chapter": from_chapter,
            "to_chapter": to_chapter,
            "keyword": keyword,
            "limit": limit,
        },
    )


@mcp.tool()
def search_character_changes(query: str, book_id: int | None = None) -> dict[str, Any]:
    """按事件检索人物变化。"""
    return _tool(book_id, "search_character_changes", {"query": query})


@mcp.tool()
def resolve_alias(
    alias: str,
    as_of_chapter: int | None = None,
    book_id: int | None = None,
) -> dict[str, Any]:
    """把称呼解析成最可能的 3 个候选。"""
    return _tool(
        book_id,
        "resolve_alias",
        {"alias": alias, "as_of_chapter": as_of_chapter},
    )


@mcp.tool()
def list_alias_candidates(
    alias: str,
    as_of_chapter: int | None = None,
    offset: int = 0,
    limit: int = 10,
    book_id: int | None = None,
) -> dict[str, Any]:
    """列出用这个称呼的全部候选。"""
    return _tool(
        book_id,
        "list_alias_candidates",
        {
            "alias": alias,
            "as_of_chapter": as_of_chapter,
            "offset": offset,
            "limit": limit,
        },
    )


@mcp.tool()
def search_lore(query: str, book_id: int | None = None) -> dict[str, Any]:
    """检索地点、门派、物品、设定。"""
    return _tool(book_id, "search_lore", {"query": query})


def main() -> None:
    parser = argparse.ArgumentParser(description="RavenRead MCP stdio server")
    parser.add_argument("--base-url", default=None)
    args = parser.parse_args()
    if args.base_url:
        os.environ["RAVENREAD_BASE_URL"] = args.base_url
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
