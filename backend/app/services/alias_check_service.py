"""单章别名自检：规则层直接删；模型层可选、默认只标注。

规则层（每次、零成本）：本章正文里没有这个写法 / 长度格式不对 / 称谓就是本名 —— 直接删。
模型层（手动开关）：把规则层剩下的称谓、本章候选和原文片段交给另一个模型，
问「这个称呼指谁」；默认只标注不删除，避免误删正确称谓。
模型层关闭时只跑规则层，报告里不会出现任何「证据不足 / 待送检」之类的字段。

判定结果只动 characters.mentions[].aliases 和 new_aliases，并把过程记进
chapter_extractions.alias_check，方便事后追。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from app.common.exceptions import BusinessException
from app.services import llm_provider_service, llm_service

MIN_ALIAS_LEN = 2
MAX_ALIAS_LEN = 8
SNIPPET_WINDOW = 200
MAX_CHECK_ITEMS = 8
MAX_SNIPPETS = 2
MAX_CANDIDATES = 8
MAX_IDENTITY_CHARS = 40
CHECK_MAX_TOKENS = 2000
CHECK_RETRY_TOKENS = 4000
CHECK_TIMEOUT = 60
EMPTY_STREAK_LIMIT = 2
# 默认只标注、不删除。书 3 预览证明：模型会把「说话人」当成「被称呼的人」，
# 自动删会误删正确称谓。需要自动删除时置 True；删除仍要求具名原文证据。
AUTO_DELETE = False

_PUNCT = re.compile(r"[，。、；：！？「」『』（）《》〈〉【】…—,.;:!?\"'()\[\]{}<>\s]")
_LATIN_OR_DIGIT = re.compile(r"[0-9A-Za-z]")

REVIEW_SYSTEM = """你是中文小说的称谓校对员。下面给出同一章里抽出的若干条「称呼 → 人物」映射，
每条附有本章候选人物，以及这个称呼在原文里最多两处片段（称呼已用【】标出）。

逐条判断：这些片段里的称呼，实际指的是谁？
规则：
- 只依据给出的片段和候选，不要补情节，不要使用后文信息。
- 片段里看不出、或只是「姑娘/公子/大人」这类泛称/临时描述：unclear。
- 如果片段明确指的就是被归给的人：ok。
- 称呼出现在对话里时，指被称呼的人，不是说话的人；不要因为说话人的名字在片段里就判 wrong。
- 只有片段明确指向另一个候选人物时，才判 wrong，并在 refers_to 写那个人物（写名字或 [C1] 这样的编号）。
- refers_to 只写名字或编号，不要带括号、身份或解释。
- 判 wrong 时，evidence 必须是片段序号，quote 必须是该片段里原样复制、包含这个称呼的一小段原文（不超过 30 字），不要改写。
- 不确定一律 unclear；宁可放过，不要猜。

只输出 JSON 数组，不要代码块，不要解释。每项：
{"id": 序号, "verdict": "ok|wrong|unclear", "refers_to": "候选人物名或 null", "evidence": 片段序号或 null, "quote": "原文片段", "reason": "20 字以内"}"""


@dataclass
class _Item:
    """一条待复核的 (称呼, 归属人)；同一对可能在 mentions/new_aliases 重复出现。"""

    kind: str
    holder: dict[str, Any]
    index: int
    alias: str
    owner: str
    snippets: list[tuple[int, int]]
    candidates: list[dict[str, Any]]
    extras: list[tuple[str, dict, int]] = field(default_factory=list)


def ensure_schema() -> None:
    """给旧库补 alias_check 列；新库由 create_all 建出，本函数是 no-op。"""
    from app.database import engine

    with engine.begin() as conn:
        rows = conn.exec_driver_sql("PRAGMA table_info(chapter_extractions)").fetchall()
        if not rows or any(row[1] == "alias_check" for row in rows):
            return
        conn.exec_driver_sql(
            "ALTER TABLE chapter_extractions ADD COLUMN alias_check JSON"
        )


def _text(value: Any) -> str:
    return str(value or "").strip()


def _squash(text: str) -> str:
    """去掉所有空白，用来兜住「x y z」这种排版空格。"""
    return re.sub(r"\s+", "", text)


def _plain(text: str) -> str:
    """比较 quote 用：去掉空白和【】标记。"""
    return re.sub(r"\s+", "", re.sub(r"[【】]", "", text or ""))


def _appears(alias: str, text: str, squashed: str) -> bool:
    return alias in text or alias in squashed


def _bad_format(alias: str) -> str | None:
    if len(alias) < MIN_ALIAS_LEN:
        return "单字称谓，检索不到"
    if len(alias) > MAX_ALIAS_LEN:
        return f"称谓超过 {MAX_ALIAS_LEN} 字，不像称呼"
    if _LATIN_OR_DIGIT.search(alias):
        return "称谓含数字或英文"
    if _PUNCT.search(alias):
        return "称谓含标点或空白"
    return None


def _each_alias(parsed: dict[str, Any]):
    """遍历可删的别名位置，产出 (holder, key, index, alias, owner)。"""
    characters = parsed.get("characters") or {}
    for entry in characters.get("mentions") or []:
        if not isinstance(entry, dict):
            continue
        owner = _text(entry.get("name"))
        aliases = entry.get("aliases") or []
        if isinstance(aliases, list):
            for index, raw in enumerate(aliases):
                alias = _text(raw)
                if alias:
                    yield ("mention", entry, index, alias, owner)
    for index, item in enumerate(characters.get("new_aliases") or []):
        if not isinstance(item, dict):
            continue
        alias = _text(item.get("alias"))
        if alias:
            yield ("new_alias", item, index, alias, _text(item.get("canonical")))


def _remove(kind: str, holder: dict[str, Any], index: int) -> None:
    if kind == "mention":
        aliases = list(holder.get("aliases") or [])
        if 0 <= index < len(aliases):
            aliases.pop(index)
        holder["aliases"] = aliases


def prune(parsed: dict[str, Any], text: str) -> tuple[dict[str, Any], list[dict]]:
    """规则层：删掉本章正文里根本没有的称谓、格式不行的称谓、自称。"""
    squashed = _squash(text)
    issues: list[dict[str, Any]] = []
    drop_mentions: list[tuple[dict, int]] = []
    drop_new: list[dict] = []

    for kind, holder, index, alias, owner in _each_alias(parsed):
        reason = _bad_format(alias)
        if not reason and alias == owner:
            reason = "称谓与本名相同"
        if not reason and not _appears(alias, text, squashed):
            reason = "本章正文里没有这个写法"
        if not reason:
            continue
        issues.append(
            {
                "alias": alias,
                "owner": owner,
                "from": kind,
                "stage": "rule",
                "action": "dropped",
                "reason": reason,
            }
        )
        if kind == "mention":
            drop_mentions.append((holder, index))
        else:
            drop_new.append(holder)

    # 倒序删，避免下标错位
    _apply(parsed, drop_mentions, drop_new)
    return parsed, issues


def _positions(text: str, alias: str) -> list[int]:
    result: list[int] = []
    cursor = 0
    while True:
        pos = text.find(alias, cursor)
        if pos < 0:
            break
        result.append(pos)
        cursor = pos + 1
    return result


def _normalize_name(value: Any) -> str:
    name = _text(value)
    return re.sub(r"[（(].*?[）)]", "", name).strip() or name


def _known_map(known_characters: list[dict[str, Any]] | None) -> dict[str, dict]:
    return {
        _text(item.get("name")): item
        for item in (known_characters or [])
        if isinstance(item, dict) and _text(item.get("name"))
    }


def _protagonist_set(protagonists: list | None) -> set[str]:
    return {
        _normalize_name(value)
        for value in (protagonists or [])
        if _normalize_name(value)
    }


def _candidates(
    parsed: dict[str, Any],
    owner: str,
    known: dict[str, dict],
    protagonists: set[str],
) -> list[dict[str, Any]]:
    """本章候选：归属人置顶，其余来自 mentions / new_aliases。"""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(name: Any, role: str = "") -> None:
        value = _text(name)
        if not value or value in seen:
            return
        seen.add(value)
        profile = known.get(value) or {}
        identity = _text(profile.get("identity"))[:MAX_IDENTITY_CHARS]
        if not identity:
            identity = _text(role)[:30]
        result.append(
            {
                "name": value,
                "identity": identity,
                "protagonist": _normalize_name(value) in protagonists,
            }
        )

    add(owner)
    characters = parsed.get("characters") or {}
    for entry in characters.get("mentions") or []:
        if not isinstance(entry, dict):
            continue
        add(entry.get("name"), _text(entry.get("role")))
    for entry in characters.get("new_aliases") or []:
        if not isinstance(entry, dict):
            continue
        add(entry.get("canonical"))
    return result[:MAX_CANDIDATES]


def _snippets(
    text: str,
    alias: str,
    positions: list[int],
    candidates: list[dict[str, Any]],
) -> list[tuple[int, int]]:
    """最多两处片段：优先候选名字多、其次靠前。"""
    names = [item["name"] for item in candidates if item.get("name")]
    scored: list[tuple[int, int, int, int]] = []
    for pos in positions:
        start = max(0, pos - SNIPPET_WINDOW)
        end = min(len(text), pos + len(alias) + SNIPPET_WINDOW)
        window = text[start:end]
        score = sum(1 for name in names if name in window)
        scored.append((score, pos, start, end))
    scored.sort(key=lambda row: (-row[0], row[1]))
    picked = sorted(scored[:MAX_SNIPPETS], key=lambda row: row[1])
    return [(row[2], row[3]) for row in picked]


def _mark(window: str, alias: str) -> str:
    return window.replace(alias, f"【{alias}】").replace("\n", " ")


def _format_candidate(index: int, candidate: dict[str, Any]) -> str:
    label = f"[C{index}] {candidate['name']}"
    detail = _text(candidate.get("identity"))
    if candidate.get("protagonist"):
        detail = f"{detail}；主角" if detail else "主角"
    return f"{label}（{detail}）" if detail else label


def _build_message(items: list[_Item], text: str, nudge: bool = False) -> str:
    lines: list[str] = []
    for number, item in enumerate(items, start=1):
        lines.append(f"第{number}条")
        lines.append(f"称呼：{item.alias}")
        lines.append(f"归给：{item.owner or '未标注'}")
        lines.append(
            "候选："
            + "；".join(
                _format_candidate(index, candidate)
                for index, candidate in enumerate(item.candidates, start=1)
            )
        )
        for index, (start, end) in enumerate(item.snippets, start=1):
            lines.append(f"片段{index}：{_mark(text[start:end], item.alias)}")
        lines.append("")
    lines.append("按顺序输出 JSON 数组，每项对应一条。")
    if nudge:
        # 思考型模型会把 max_tokens 全花在推理上，这里逼它先出结果
        lines.append("上一次的输出被推理占满了，这次直接给结论，不要写推理过程。")
    return "\n".join(lines)


def _collect_items(
    parsed: dict[str, Any],
    text: str,
    known_characters: list[dict[str, Any]] | None,
    protagonists: list | None,
) -> list[_Item]:
    """规则层剩下的称谓，按 (owner, alias) 去重，准备送模型复核。"""
    known = _known_map(known_characters)
    protagonist_names = _protagonist_set(protagonists)
    items: list[_Item] = []
    by_key: dict[tuple[str, str], _Item] = {}
    seen: set[tuple[str, str]] = set()

    for kind, holder, index, alias, owner in _each_alias(parsed):
        if not owner:
            continue
        key = (owner, alias)
        if key in seen:
            existing = by_key.get(key)
            if existing is not None:
                existing.extras.append((kind, holder, index))
            continue
        seen.add(key)
        positions = _positions(text, alias)
        if not positions:
            # prune 用 squash 兜底时可能正文里没有连续写法；没有片段就不送检。
            continue
        candidates = _candidates(parsed, owner, known, protagonist_names)
        snippets = _snippets(text, alias, positions, candidates)
        if not snippets:
            continue
        item = _Item(
            kind=kind,
            holder=holder,
            index=index,
            alias=alias,
            owner=owner,
            snippets=snippets,
            candidates=candidates,
        )
        by_key[key] = item
        items.append(item)

    # 按首次出现位置，保持与正文顺序一致
    items.sort(key=lambda item: item.snippets[0][0] if item.snippets else 0)
    return items


def _bump_empty(state: dict[str, Any] | None) -> None:
    if state is None:
        return
    state["empty_streak"] = int(state.get("empty_streak", 0)) + 1
    if state["empty_streak"] >= EMPTY_STREAK_LIMIT:
        state["disabled"] = True


def _resolve_refers_to(item: _Item, value: Any) -> str:
    """把模型写的 C3 / [C3] / 「名字（说明）」解析成候选名。"""
    text = _text(value)
    if not text:
        return ""
    label = re.match(r"^\[?[Cc]?(\d+)\]?", text)
    if label:
        index = int(label.group(1)) - 1
        if 0 <= index < len(item.candidates):
            return item.candidates[index]["name"]
    names = [candidate["name"] for candidate in item.candidates]
    if text in names:
        return text
    for name in names:
        if text.startswith(name):
            return name
    for name in names:
        if text in name or name in text:
            return name
    return text


def _validate_wrong(
    text: str, item: _Item, verdict: dict[str, Any]
) -> tuple[bool, str]:
    """模型说 wrong 时，检查它有没有给出可核对的人名和原文。"""
    refers_to = _resolve_refers_to(item, verdict.get("refers_to"))
    verdict["_refers_to"] = refers_to
    if not refers_to:
        return False, "没有指出另一个人"
    candidates = {candidate["name"] for candidate in item.candidates}
    match = None
    if refers_to in candidates:
        match = refers_to
    else:
        for name in candidates:
            if refers_to in name or name in refers_to:
                match = name
                break
    if not match:
        return False, "指向的人不在候选里"
    if match == item.owner:
        return False, "指向的还是本人"

    try:
        evidence = int(verdict.get("evidence"))
    except (TypeError, ValueError):
        return False, "缺少片段序号"
    if evidence < 1 or evidence > len(item.snippets):
        return False, "片段序号越界"

    start, end = item.snippets[evidence - 1]
    raw = _plain(text[start:end])
    quote = _plain(verdict.get("quote"))
    if not quote or quote not in raw:
        return False, "quote 不是片段原文"
    if item.alias not in quote:
        return False, "quote 不含称呼"

    parts = {match}
    for size in (2, 3, 4):
        if len(match) >= size:
            parts.add(match[-size:])
    if not any(part and part in raw for part in parts):
        return False, "片段里没有这个人的名字"
    return True, match


def review(
    parsed: dict[str, Any],
    text: str,
    provider: dict[str, Any] | None,
    client=None,
    *,
    known_characters: list[dict[str, Any]] | None = None,
    protagonists: list | None = None,
    state: dict[str, Any] | None = None,
    auto_delete: bool | None = None,
) -> tuple[dict[str, Any], list[dict], str | None, dict[str, Any]]:
    """模型层；provider 为 None 时直接返回，不收集、不调用。"""
    if provider is None:
        return (
            parsed,
            [],
            None,
            {
                "checked": 0,
                "calls": 0,
                "truncated": 0,
                "disabled": False,
            },
        )

    items = _collect_items(parsed, text, known_characters, protagonists)
    meta: dict[str, Any] = {
        "checked": 0,
        "calls": 0,
        "truncated": max(0, len(items) - MAX_CHECK_ITEMS),
        "disabled": bool(state and state.get("disabled")),
    }
    if state is not None and state.get("disabled"):
        return parsed, [], None, meta
    if not items:
        return parsed, [], None, meta

    delete_enabled = AUTO_DELETE if auto_delete is None else bool(auto_delete)
    sent = items[:MAX_CHECK_ITEMS]
    meta["checked"] = len(sent)

    response = llm_service.chat_completion(
        {**provider, "disable_thinking": True},
        [
            {"role": "system", "content": REVIEW_SYSTEM},
            {"role": "user", "content": _build_message(sent, text)},
        ],
        max_tokens=CHECK_MAX_TOKENS,
        temperature=0,
        timeout=CHECK_TIMEOUT,
        client=client,
    )
    meta["calls"] += 1
    if not response.get("content"):
        reason = response.get("finish_reason")
        retry = llm_service.chat_completion(
            {**provider, "disable_thinking": True},
            [
                {"role": "system", "content": REVIEW_SYSTEM},
                {
                    "role": "user",
                    "content": _build_message(sent, text, nudge=True),
                },
            ],
            max_tokens=CHECK_RETRY_TOKENS,
            temperature=0,
            timeout=CHECK_TIMEOUT,
            client=client,
        )
        meta["calls"] += 1
        if not retry.get("content"):
            reason = retry.get("finish_reason") or reason
            hint = "（输出预算被推理占满）" if reason == "length" else ""
            _bump_empty(state)
            meta["disabled"] = bool(state and state.get("disabled"))
            return (
                parsed,
                [],
                response.get("error")
                or retry.get("error")
                or f"校验模型没有返回内容{hint}",
                meta,
            )
        response = retry

    verdicts = _parse(response.get("content") or "", len(sent))
    if verdicts is None:
        _bump_empty(state)
        meta["disabled"] = bool(state and state.get("disabled"))
        return parsed, [], "检查模型没有返回可解析的 JSON", meta

    if state is not None:
        state["empty_streak"] = 0

    issues: list[dict[str, Any]] = []
    drop_mentions: list[tuple[dict, int]] = []
    drop_new: list[dict] = []
    for number, item in enumerate(sent, start=1):
        verdict = verdicts.get(number)
        if not verdict or verdict.get("verdict") != "wrong":
            continue
        valid, detail = _validate_wrong(text, item, verdict)
        if not valid and detail == "指向的还是本人":
            # 模型说 wrong 却又指回本人，等于 unclear，不记噪音。
            continue
        if valid and delete_enabled:
            action = "dropped"
            for kind, holder, index in [
                (item.kind, item.holder, item.index),
                *item.extras,
            ]:
                if kind == "mention":
                    drop_mentions.append((holder, index))
                else:
                    drop_new.append(holder)
            reason = verdict.get("reason") or "模型判定指错人"
        else:
            action = "flagged"
            reason = (
                (verdict.get("reason") or detail) if valid else detail
            ) or "模型判定指错人"
        issues.append(
            {
                "alias": item.alias,
                "owner": item.owner,
                "from": item.kind,
                "stage": "llm",
                "action": action,
                "refers_to": (detail if valid else verdict.get("_refers_to")) or None,
                "quote": _text(verdict.get("quote"))[:80] or None,
                "reason": reason,
            }
        )

    _apply(parsed, drop_mentions, drop_new)
    return parsed, issues, None, meta


def _apply(
    parsed: dict[str, Any],
    drop_mentions: list[tuple[dict, int]],
    drop_new: list[dict],
) -> None:
    for holder, index in sorted(drop_mentions, key=lambda pair: -pair[1]):
        _remove("mention", holder, index)
    if drop_new:
        characters = parsed.get("characters") or {}
        characters["new_aliases"] = [
            item
            for item in (characters.get("new_aliases") or [])
            if item not in drop_new
        ]


def _parse(content: str, expected: int) -> dict[int, dict[str, Any]] | None:
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    start = text.find("[")
    end = text.rfind("]")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        rows = json.loads(text)
    except (TypeError, ValueError):
        return None
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        if index < 1 or index > expected:
            continue
        verdict = _text(row.get("verdict")).lower()
        if verdict not in {"ok", "wrong", "unclear"}:
            verdict = "unclear"
        result[index] = {
            "verdict": verdict,
            "reason": _text(row.get("reason"))[:80],
            "refers_to": _text(row.get("refers_to"))[:40],
            "evidence": row.get("evidence"),
            "quote": _text(row.get("quote"))[:80],
        }
    return result


def build_report(
    rule_issues: list[dict],
    model_issues: list[dict],
    error: str | None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = meta or {}
    merged = list(rule_issues) + list(model_issues)
    issues = []
    seen = set()
    for item in merged:
        key = (
            item.get("alias"),
            item.get("owner"),
            item.get("stage"),
            item.get("action"),
            item.get("reason"),
        )
        if key in seen:
            continue
        seen.add(key)
        issues.append(item)
    return {
        "dropped": sum(1 for item in issues if item.get("action") == "dropped"),
        "flagged": sum(1 for item in issues if item.get("action") == "flagged"),
        "checked": int(meta.get("checked") or 0),
        "truncated": int(meta.get("truncated") or 0),
        "calls": int(meta.get("calls") or 0),
        "disabled": bool(meta.get("disabled")),
        "model": meta.get("model"),
        "error": error,
        "issues": issues,
    }


def run(
    db: Any,
    parsed: dict[str, Any],
    text: str,
    check_provider_id: int | None = None,
    client=None,
    *,
    known_characters: list[dict[str, Any]] | None = None,
    protagonists: list | None = None,
    state: dict[str, Any] | None = None,
    after_prune=None,
    auto_delete: bool | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """规则层总是跑；check_provider_id 为空时只跑规则层，不碰模型。"""
    parsed, rule_issues = prune(parsed, text)
    if after_prune is not None:
        parsed = after_prune(parsed) or parsed

    if check_provider_id is None:
        return parsed, build_report(rule_issues, [], None, {"model": None})

    try:
        provider = llm_provider_service.get_active_provider_config(
            db, check_provider_id
        )
    except BusinessException as exc:
        return parsed, build_report(rule_issues, [], exc.message, {"model": None})

    meta = {"model": provider.get("model")}
    try:
        parsed, model_issues, error, meta2 = review(
            parsed,
            text,
            provider,
            client=client,
            known_characters=known_characters,
            protagonists=protagonists,
            state=state,
            auto_delete=auto_delete,
        )
        meta.update(meta2)
    except Exception as exc:  # noqa: BLE001 - 复核失败不能拖垮单章
        model_issues, error = [], f"复核失败：{exc}"
    report = build_report(rule_issues, model_issues, error, meta)
    report["model"] = provider.get("model")
    return parsed, report
