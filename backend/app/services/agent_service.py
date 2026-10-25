"""伴读 agent 的 LangGraph 状态机"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.chapter import Chapter
from app.services import (
    agent_capability_service,
    agent_context_service,
    agent_prompts,
    agent_runtime,
    agent_tool_service,
    model_tool_service,
)

MAX_MODEL_CALLS = 9
MAX_TOOL_ROUNDS = 5
MAX_TOOL_CALLS_PER_ROUND = 2
MAX_TOTAL_TOOL_CALLS = 10
AGENT_TIME_BUDGET = 110
MIN_CALL_SECONDS = 5
RECURSION_LIMIT = 18


class AgentState(TypedDict, total=False):
    book_id: int
    session_id: int | None
    question: str
    chapter_idx: int
    char_offset: int
    safe_chapter: int
    book: dict[str, Any]
    chapter: dict[str, Any]
    history: list[dict[str, Any]]
    messages: list[dict[str, str]]
    model_calls: int
    tool_rounds: int
    total_tool_calls: int
    pending_calls: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    trace: list[dict[str, Any]]
    answer: str | None
    citations: list[dict[str, Any]]
    citation_verified: bool
    model: str | None
    error: str | None
    budget_exhausted: str | None
    wrapped_up: bool
    started_at: float


_checkpointer = MemorySaver()

_locks_guard = threading.Lock()
_book_locks: dict[int, threading.Lock] = {}


def _book_lock(book_id: int) -> threading.Lock:
    with _locks_guard:
        lock = _book_locks.get(book_id)
        if lock is None:
            lock = threading.Lock()
            _book_locks[book_id] = lock
        return lock


def _plain(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _strip_fence(content: str) -> str:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return text


def _parse_action(
    content: str | None,
) -> tuple[str, Any] | None:
    if not content:
        return None
    try:
        data = json.loads(_strip_fence(content))
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    action = str(data.get("action") or "").strip().lower()
    if action == "tool":
        calls = data.get("calls")
        if not isinstance(calls, list) or not calls:
            return None
        clean = []
        for item in calls[:MAX_TOOL_CALLS_PER_ROUND]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("tool") or "").strip()
            if not name:
                continue
            args = item.get("args")
            clean.append({"tool": name, "args": args if isinstance(args, dict) else {}})
        if not clean:
            return None
        return "tool", clean
    if action == "answer":
        answer = str(data.get("answer") or "").strip()
        if not answer:
            return None
        citations = []
        raw = data.get("citations")
        if isinstance(raw, list):
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    chapter_idx = (int(item["chapter_number"]) - 1 if "chapter_number" in item else int(item.get("chapter_idx")))
                except (TypeError, ValueError):
                    continue
                citations.append(
                    {
                        "chapter_idx": chapter_idx,
                        "chapter_title": str(item.get("chapter_title") or "").strip()
                        or None,
                        "quote": str(item.get("quote") or "").strip() or None,
                    }
                )
        return "answer", {"answer": answer, "citations": citations}
    return None


def build_graph(
    db: Session,
    book: Book,
    provider_id: int | None,
    current_chapter: int,
    capabilities: agent_capability_service.AgentCapabilities | None = None,
):
    book_info = agent_context_service.book_meta(book)
    capabilities = capabilities or agent_capability_service.detect(db, book.id)
    system_prompt = agent_prompts.build_system_prompt(
        model_tool_service.specs(
            current_chapter,
            available=capabilities.available_tools(),
        ),
        book_info,
        current_chapter,
    )

    def load_context(state: AgentState) -> dict[str, Any]:
        chapter = agent_context_service.get_chapter_or_404(
            db, book.id, int(state["chapter_idx"])
        )
        safe = agent_context_service.safe_chapter(
            db, book.id, chapter.idx, int(state["char_offset"])
        )
        session = agent_context_service.get_or_create_session(
            db, book.id, state.get("session_id")
        )
        history = agent_context_service.history(db, session.id, safe)
        user_prompt = agent_prompts.build_user_prompt(
            book=agent_context_service.book_meta(book),
            chapter=agent_context_service.chapter_meta(chapter),
            safe_chapter=safe,
            char_offset=int(state["char_offset"]),
            question=str(state["question"]),
            history=history,
        )
        return {
            "book_id": book.id,
            "session_id": session.id,
            "safe_chapter": safe,
            "book": book_info,
            "chapter": agent_context_service.chapter_meta(chapter),
            "history": history,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "model_calls": 0,
            "tool_rounds": 0,
            "total_tool_calls": 0,
            "pending_calls": [],
            "observations": [],
            "trace": [],
            "answer": None,
            "citations": [],
            "citation_verified": False,
            "model": None,
            "error": None,
            "budget_exhausted": None,
            "wrapped_up": False,
        }

    def call_agent(state: AgentState) -> dict[str, Any]:
        calls = int(state.get("model_calls") or 0)
        if calls >= MAX_MODEL_CALLS:
            return {
                "answer": "信息不足：已达到模型调用上限。",
                "pending_calls": [],
                "error": "达到模型调用上限",
            }
        remaining = AGENT_TIME_BUDGET - (
            time.time() - float(state.get("started_at") or time.time())
        )
        if remaining <= MIN_CALL_SECONDS:
            return {
                "answer": "信息不足：时间预算已用完。",
                "pending_calls": [],
                "error": "时间预算用完",
            }
        messages = list(state.get("messages") or [])
        result = agent_runtime.call_model(
            db,
            provider_id,
            messages,
            timeout=int(
                max(
                    MIN_CALL_SECONDS,
                    min(agent_runtime.MODEL_TIMEOUT, remaining),
                )
            ),
        )
        calls += 1
        update: dict[str, Any] = {
            "model_calls": calls,
            "model": result.get("model"),
        }
        if result.get("error"):
            return {
                **update,
                "answer": None,
                "pending_calls": [],
                "error": str(result["error"]),
            }
        content = result.get("content") or ""
        parsed = _parse_action(content)
        if parsed is None and calls < MAX_MODEL_CALLS:
            repair_remaining = AGENT_TIME_BUDGET - (
                time.time() - float(state.get("started_at") or time.time())
            )
            if repair_remaining > MIN_CALL_SECONDS:
                repair_messages = [
                    *messages,
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": (
                            "你的输出不是可解析的 JSON。只输出一个 JSON 对象："
                            '{"action":"tool",...} 或 {"action":"answer",...}。'
                        ),
                    },
                ]
                result = agent_runtime.call_model(
                    db,
                    provider_id,
                    repair_messages,
                    timeout=int(
                        max(
                            MIN_CALL_SECONDS,
                            min(agent_runtime.MODEL_TIMEOUT, repair_remaining),
                        )
                    ),
                )
                calls += 1
                update["model_calls"] = calls
                update["model"] = result.get("model") or update.get("model")
                if result.get("error"):
                    return {
                        **update,
                        "answer": None,
                        "pending_calls": [],
                        "error": str(result["error"]),
                    }
                content = result.get("content") or ""
                parsed = _parse_action(content)
        if parsed is None:
            return {
                **update,
                "answer": content.strip() or "模型没有返回可用内容。",
                "pending_calls": [],
                "citations": [],
                "error": "模型输出不是 JSON",
            }
        action, payload = parsed
        if action == "answer":
            return {
                **update,
                "answer": payload["answer"],
                "citations": payload["citations"],
                "pending_calls": [],
            }
        if state.get("wrapped_up"):
            # 收尾轮不允许再进 tools：模型仍输出 tool 时，取原文当回答。
            return {
                **update,
                "answer": content.strip() or "预算已用完。",
                "citations": [],
                "pending_calls": [],
                "error": "wrap_up_tool",
            }
        total_calls = int(state.get("total_tool_calls") or 0)
        if int(state.get("tool_rounds") or 0) >= MAX_TOOL_ROUNDS:
            return {
                **update,
                "pending_calls": payload,
                "messages": [
                    *messages,
                    {"role": "assistant", "content": content},
                ],
                "budget_exhausted": "rounds",
            }
        if total_calls >= MAX_TOTAL_TOOL_CALLS:
            return {
                **update,
                "pending_calls": payload,
                "messages": [
                    *messages,
                    {"role": "assistant", "content": content},
                ],
                "budget_exhausted": "tools",
            }
        payload = payload[: MAX_TOTAL_TOOL_CALLS - total_calls]
        return {
            **update,
            "pending_calls": payload,
            "messages": [
                *messages,
                {"role": "assistant", "content": content},
            ],
        }

    def run_tools(state: AgentState) -> dict[str, Any]:
        chapter = agent_context_service.get_chapter_or_404(
            db, book.id, int(state["chapter_idx"])
        )
        ctx = agent_tool_service.ToolContext(
            db=db,
            book=book,
            chapter=chapter,
            safe_chapter=int(state.get("safe_chapter") or 0),
            char_offset=int(state["char_offset"]),
            disabled_tools=capabilities.disabled_tools(),
            capabilities=capabilities,
        )
        observations: list[dict[str, Any]] = []
        trace: list[dict[str, Any]] = list(state.get("trace") or [])
        for call in state.get("pending_calls") or []:
            name = str(call.get("tool") or "")
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            result = model_tool_service.execute(name, args, ctx)
            observations.append(
                {
                    "tool": name,
                    "ok": bool(result.get("ok")),
                    "data": result.get("data"),
                    **{key:result[key] for key in ("affected_storylines","affected_has_more","next_action","deduplicated") if key in result},
                    "error": None if result.get("ok") else result.get("error"),
                }
            )
            trace.append(
                {
                    "tool": name,
                    "args": args,
                    "ok": bool(result.get("ok")),
                    "summary": model_tool_service.summary(name, result),
                    "error": None if result.get("ok") else result.get("error"),
                }
            )
        messages = list(state.get("messages") or [])
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    {"observations": observations}, ensure_ascii=False
                ),
            }
        )
        return {
            "observations": [*(state.get("observations") or []), *observations],
            "trace": trace,
            "messages": messages,
            "pending_calls": [],
            "tool_rounds": int(state.get("tool_rounds") or 0) + 1,
            "total_tool_calls": int(state.get("total_tool_calls") or 0)
            + len(observations),
        }

    def verify_citations(state: AgentState) -> dict[str, Any]:
        safe = int(state.get("safe_chapter") or 0)
        observations = state.get("observations") or []
        haystack = _plain(json.dumps(observations, ensure_ascii=False))
        valid: list[dict[str, Any]] = []
        for item in state.get("citations") or []:
            chapter_idx = (int(item["chapter_number"]) - 1 if "chapter_number" in item else int(item.get("chapter_idx")))
            if chapter_idx < 0 or chapter_idx > safe:
                continue
            quote = item.get("quote")
            if not quote:
                continue
            if _plain(str(quote)) not in haystack:
                continue
            chapter = agent_context_service.get_chapter_or_404(db, book.id, chapter_idx)
            if chapter.kind != "main":
                continue
            from app.services import text_window_service
            raw = text_window_service.get_chapter_content(db, book.id, chapter_idx)["text"]
            if _plain(str(quote)) not in _plain(raw):
                continue
            valid.append(item)
        return {
            "citations": valid,
            "citation_verified": bool(valid),
        }

    def persist_turn(state: AgentState) -> dict[str, Any]:
        session_id = int(state["session_id"])
        safe = int(state.get("safe_chapter") or 0)
        elapsed_ms = int(
            (time.time() - float(state.get("started_at") or time.time())) * 1000
        )
        reply = str(state.get("answer") or "").strip()
        if not reply:
            if state.get("budget_exhausted") in {"rounds", "tools"}:
                reply = "工具预算已用完，但模型没有给出可用的收尾回答。"
            else:
                reply = str(state.get("error") or "没有生成回答。")
        meta = {
            "citations": state.get("citations") or [],
            "tool_trace": state.get("trace") or [],
            "model": state.get("model"),
            "elapsed_ms": elapsed_ms,
            "citation_verified": bool(state.get("citation_verified")),
            "budget_exhausted": state.get("budget_exhausted"),
            "error": state.get("error"),
        }
        agent_context_service.save_message(
            db,
            session_id,
            "user",
            str(state["question"]),
            int(state["chapter_idx"]),
            int(state["char_offset"]),
            safe,
        )
        agent_context_service.save_message(
            db,
            session_id,
            "assistant",
            reply,
            int(state["chapter_idx"]),
            int(state["char_offset"]),
            safe,
            meta,
        )
        return {"answer": reply, "error": state.get("error")}

    def wrap_up(state: AgentState) -> dict[str, Any]:
        messages = list(state.get("messages") or [])
        messages.append(
            {
                "role": "user",
                "content": (
                    "工具预算已用完，不要再调用工具。只依据上面已经返回的结果回答；"
                    "证据不足就明确说明缺什么，不要补全，不要编造原文。"
                ),
            }
        )
        return {
            "pending_calls": [],
            "wrapped_up": True,
            "messages": messages,
        }

    def route_after_agent(state: AgentState) -> str:
        if state.get("wrapped_up"):
            return "verify"
        remaining = AGENT_TIME_BUDGET - (
            time.time() - float(state.get("started_at") or time.time())
        )
        if state.get("budget_exhausted") in {"rounds", "tools"}:
            if (
                remaining > MIN_CALL_SECONDS
                and int(state.get("model_calls") or 0) < MAX_MODEL_CALLS
            ):
                return "wrap_up"
            return "verify"
        if (
            state.get("pending_calls")
            and int(state.get("tool_rounds") or 0) < MAX_TOOL_ROUNDS
            and int(state.get("model_calls") or 0) < MAX_MODEL_CALLS
            and int(state.get("total_tool_calls") or 0) < MAX_TOTAL_TOOL_CALLS
            and remaining > MIN_CALL_SECONDS
        ):
            return "tools"
        return "verify"

    builder = StateGraph(AgentState)
    builder.add_node("load_context", load_context)
    builder.add_node("agent", call_agent)
    builder.add_node("tools", run_tools)
    builder.add_node("wrap_up", wrap_up)
    builder.add_node("verify", verify_citations)
    builder.add_node("persist", persist_turn)
    builder.add_edge(START, "load_context")
    builder.add_edge("load_context", "agent")
    builder.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "wrap_up": "wrap_up", "verify": "verify"},
    )
    builder.add_edge("tools", "agent")
    builder.add_edge("wrap_up", "agent")
    builder.add_edge("verify", "persist")
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=_checkpointer)


def chat(
    db: Session,
    book_id: int,
    question: str,
    chapter_idx: int,
    char_offset: int,
    session_id: int | None = None,
    provider_id: int | None = None,
) -> dict[str, Any]:
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException(message="书籍不存在或已被删除", code=4040)
    agent_context_service.get_chapter_or_404(db, book_id, chapter_idx)
    capabilities = agent_capability_service.detect(db, book_id)
    if not capabilities.enabled:
        raise BusinessException(
            message="这本书还没有可用于伴读的切块、摘要或人物数据，伴读已关闭。",
            code=4000,
        )
    lock = _book_lock(book_id)
    if not lock.acquire(blocking=False):
        raise BusinessException(message="上一条还在处理，请稍后再问", code=4090)
    started_at = time.time()
    try:
        session = agent_context_service.get_or_create_session(db, book_id, session_id)
        graph = build_graph(
            db,
            book,
            provider_id,
            int(chapter_idx),
            capabilities=capabilities,
        )
        initial: AgentState = {
            "book_id": book_id,
            "session_id": session.id,
            "question": question,
            "chapter_idx": int(chapter_idx),
            "char_offset": int(char_offset),
            "started_at": started_at,
        }
        result = graph.invoke(
            initial,
            {
                "configurable": {"thread_id": f"book:{book_id}"},
                "recursion_limit": RECURSION_LIMIT,
            },
        )
        elapsed_ms = int((time.time() - started_at) * 1000)
        return {
            "session_id": int(result.get("session_id") or session.id),
            "reply": str(result.get("answer") or ""),
            "citations": result.get("citations") or [],
            "tool_trace": result.get("trace") or [],
            "model": result.get("model"),
            "elapsed_ms": elapsed_ms,
            "citation_verified": bool(result.get("citation_verified")),
            "budget_exhausted": result.get("budget_exhausted"),
            "error": result.get("error"),
        }
    except BusinessException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BusinessException(message=f"伴读执行失败：{exc}", code=5000) from exc
    finally:
        lock.release()
