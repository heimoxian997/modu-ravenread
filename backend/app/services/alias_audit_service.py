"""别名核对：先用确定性规则筛出可疑的「称谓 → 人物」映射。

四条规则都不调用模型，成本是数据库查询加字符串匹配：

A 称谓撞上别人的本名   子串或完全相等，且归属不是那个人
B 称谓本身不像称呼     单字、过长、含标点数字英文
C 观测章里搜不到       正文里根本没这个词，或者章号和证据对不上
D 证据太薄             全书 1-2 次，且在同名称谓里占比很低

规则只负责「筛出来送检」，不直接判定错误。低频不等于错，只是值得看一眼。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.character_alias_observation import CharacterAliasObservation
from app.services import llm_provider_service, llm_service, text_window_service

SEVERITY_HIGH = 4

MAX_ALIAS_LEN = 8
CHECK_CHAPTERS = 6
SAMPLE_CHARS = 60
MAX_SAMPLES = 3
THIN_TOTAL = 2
THIN_SHARE = 0.2
REVIEW_BATCH = 5
REVIEW_MAX_TOKENS = 800

REVIEW_SYSTEM = """你是中文小说的校对员。
用户给你若干条「称呼 → 人物」的映射，以及这个称呼在原文里的片段。
请逐条判断：这些片段里，这个称呼是不是在指这个人。

判断要点：
- 只看给出的原文，不要凭想象补情节
- 片段里根本没出现这个称呼，判 unclear
- 片段里出现了，但从上下文看不出指谁，也判 unclear
- 称呼像泛称（公子、姑娘），且片段里有多个人，判 unclear 而不是 ok

只输出 JSON 数组，不要代码块，不要解释。每一项：
{"id": 序号, "verdict": "ok|wrong|unclear", "reason": "20 字以内的依据"}"""

_PUNCT = re.compile(r"[，。、；：！？「」『』（）《》〈〉【】…—,.;:!?\"'()\[\]{}<>\s]")
_LATIN_OR_DIGIT = re.compile(r"[0-9A-Za-z]")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rule_a(
    alias: str, character_id: int, characters: dict[int, Character]
) -> tuple[str, int] | None:
    """称谓撞上别人的本名。完全相等是硬信号，子串只是线索。"""
    # 单字称谓本来就到处都是，撞本名没有信息量，交给规则 B
    if len(alias) <= 1:
        return None
    for other_id, other in characters.items():
        if other_id == character_id:
            continue
        name = _text(other.canonical_name)
        if not name:
            continue
        if alias == name:
            return f"称谓就是「{name}」的本名，却挂在另一个人名下", 4
        if alias in name:
            return f"称谓包含在「{name}」的本名里，可能是本名的片段", 2
    return None


def _rule_b(alias: str) -> tuple[str, int] | None:
    if len(alias) <= 1:
        return "单字称谓，正文里会和大量普通词撞上", 2
    if len(alias) > MAX_ALIAS_LEN:
        return f"称谓超过 {MAX_ALIAS_LEN} 字，不像称呼", 4
    if _LATIN_OR_DIGIT.search(alias):
        return "称谓含数字或英文字母", 4
    if _PUNCT.search(alias):
        return "称谓含标点或空白", 4
    return None


def _check_chapters(chapters: list[int]) -> list[int]:
    """观测章太多时均匀抽几章，够判断「搜不搜得到」了。"""
    if len(chapters) <= CHECK_CHAPTERS:
        return chapters
    step = len(chapters) / CHECK_CHAPTERS
    picked = {chapters[int(index * step)] for index in range(CHECK_CHAPTERS)}
    picked.add(chapters[-1])
    return sorted(picked)


def _rule_c(
    db: Session,
    book_id: int,
    alias: str,
    chapters: list[int],
    text_cache: dict[int, str],
    source: str,
) -> tuple[str, int, dict[str, Any]]:
    """正文里到底有没有这个词。

    全书都搜不到，这条映射一定用不了；只在观测章搜不到，说明章号和证据对不上，
    称谓本身可能反而没问题 —— 两种情况的处置不一样，所以分开报。
    """
    checked = _check_chapters(chapters)
    found: list[int] = []
    for chapter_idx in checked:
        if chapter_idx not in text_cache:
            try:
                content = text_window_service.get_chapter_content(
                    db, book_id, chapter_idx
                )
                text_cache[chapter_idx] = content["text"]
            except BusinessException:
                text_cache[chapter_idx] = ""
        if alias in text_cache[chapter_idx]:
            found.append(chapter_idx)
    in_book = bool(source) and alias in source
    evidence = {
        "checked": checked,
        "found": found,
        "in_book": in_book,
        "book_hits": source.count(alias) if in_book else 0,
    }
    if not in_book:
        return f"全书正文里都没有「{alias}」这个称呼", 4, evidence
    if checked and not found:
        shown = "、".join(str(item + 1) for item in checked)
        return (
            f"第 {shown} 章的正文里搜不到「{alias}」（全书别处出现 "
            f"{evidence['book_hits']} 次，章号或证据对不上）",
            3,
            evidence,
        )
    return "", 0, evidence


def _chapter_of(offset: int, spans: list[tuple[int, int, int]]) -> int | None:
    for start, end, idx in spans:
        if start <= offset < end:
            return idx
    return None


def _samples(
    alias: str,
    chapters: list[int],
    text_cache: dict[int, str],
    source: str = "",
    spans: list[tuple[int, int, int]] | None = None,
) -> list[dict[str, Any]]:
    """取几段原文，供后续人工或模型复核时看上下文。

    观测章里找不到时，退到全书里搜 —— 否则复核时手里一段证据都没有。
    """
    result: list[dict[str, Any]] = []
    for chapter_idx in _check_chapters(chapters):
        text = text_cache.get(chapter_idx) or ""
        pos = text.find(alias)
        if pos < 0:
            continue
        start = max(0, pos - SAMPLE_CHARS)
        end = min(len(text), pos + len(alias) + SAMPLE_CHARS)
        result.append(
            {
                "chapter_idx": chapter_idx,
                "chapter_number": chapter_idx + 1,
                "text": text[start:end].replace("\n", " "),
            }
        )
        if len(result) >= MAX_SAMPLES:
            return result

    if result or not source:
        return result
    cursor = 0
    while len(result) < MAX_SAMPLES:
        pos = source.find(alias, cursor)
        if pos < 0:
            break
        start = max(0, pos - SAMPLE_CHARS)
        end = min(len(source), pos + len(alias) + SAMPLE_CHARS)
        chapter_idx = _chapter_of(pos, spans or [])
        result.append(
            {
                "chapter_idx": chapter_idx,
                "chapter_number": None if chapter_idx is None else chapter_idx + 1,
                "text": source[start:end].replace("\n", " "),
            }
        )
        cursor = pos + len(alias)
    return result


def audit_aliases(db: Session, book_id: int) -> dict[str, Any]:
    """全书别名映射体检，返回可疑清单与规则统计。"""
    if not db.get(Book, book_id):
        raise BusinessException(message="书籍不存在或已被删除", code=4040)

    characters = {
        item.id: item
        for item in db.query(Character).filter(Character.book_id == book_id).all()
    }
    mappings = db.query(CharacterAlias).filter(CharacterAlias.book_id == book_id).all()
    observations = (
        db.query(CharacterAliasObservation)
        .filter(CharacterAliasObservation.book_id == book_id)
        .all()
    )

    per_chapter: dict[tuple[str, int], dict[int, int]] = {}
    alias_totals: dict[str, int] = {}
    for row in observations:
        key = (_text(row.alias), int(row.character_id))
        per_chapter.setdefault(key, {})[int(row.chapter_idx)] = int(row.count)
        alias_totals[key[0]] = alias_totals.get(key[0], 0) + int(row.count)

    alias_owners: dict[str, set[int]] = {}
    for row in mappings:
        alias_owners.setdefault(_text(row.alias), set()).add(int(row.character_id))

    text_cache: dict[int, str] = {}
    suspects: list[dict[str, Any]] = []
    rule_hits = {"A": 0, "B": 0, "C": 0, "D": 0}
    skipped_single_char = 0

    spans = [
        (int(row.char_start), int(row.char_end), int(row.idx))
        for row in db.query(Chapter)
        .filter(Chapter.book_id == book_id)
        .order_by(Chapter.idx.asc())
        .all()
    ]
    try:
        source = text_window_service.source_text(book_id)
    except BusinessException:
        source = ""

    for row in mappings:
        alias = _text(row.alias)
        character_id = int(row.character_id)
        character = characters.get(character_id)
        if not alias or not character:
            continue
        total = int(row.occurrence_count or 0)
        alias_total = alias_totals.get(alias, total) or total
        share = round(total / alias_total, 3) if alias_total else 0.0
        chapters = sorted(per_chapter.get((alias, character_id), {}))

        reasons: list[str] = []
        hit_rules: list[str] = []
        severity = 0

        reason_a = _rule_a(alias, character_id, characters)
        if reason_a:
            hit_rules.append("A")
            reasons.append(reason_a[0])
            severity += reason_a[1]

        reason_b = _rule_b(alias)
        if reason_b:
            hit_rules.append("B")
            reasons.append(reason_b[0])
            severity += reason_b[1]
            if len(alias) <= 1:
                skipped_single_char += 1

        reason_c, weight_c, evidence = _rule_c(
            db, book_id, alias, chapters, text_cache, source
        )
        if reason_c:
            hit_rules.append("C")
            reasons.append(reason_c)
            severity += weight_c

        if (
            len(alias_owners.get(alias, ())) > 1
            and total <= THIN_TOTAL
            and share < THIN_SHARE
        ):
            hit_rules.append("D")
            reasons.append(
                f"全书只出现 {total} 次，占「{alias}」的 {round(share * 100)}%，"
                f"另有 {len(alias_owners[alias]) - 1} 个角色也被这么叫"
            )
            severity += 1

        if not hit_rules:
            continue
        for rule in hit_rules:
            rule_hits[rule] += 1
        suspects.append(
            {
                "alias": alias,
                "character_id": character_id,
                "canonical_name": _text(character.canonical_name),
                "total_count": total,
                "share": share,
                "owner_count": len(alias_owners.get(alias, ())),
                "chapters": chapters,
                "rules": hit_rules,
                "severity": severity,
                "priority": "high" if severity >= SEVERITY_HIGH else "low",
                "reasons": reasons,
                "text_evidence": evidence,
                "samples": _samples(alias, chapters, text_cache, source, spans),
            }
        )

    suspects.sort(
        key=lambda item: (
            -item["severity"],
            -len(item["rules"]),
            item["total_count"],
            item["alias"],
        )
    )
    return {
        "book_id": book_id,
        "totals": {
            "mappings": len(mappings),
            "aliases": len({_text(row.alias) for row in mappings}),
            "characters": len(characters),
            "chapters_read": len(text_cache),
            "suspects": len(suspects),
            "clean": len(mappings) - len(suspects),
        },
        "rule_hits": rule_hits,
        "single_char_aliases": skipped_single_char,
        "suspects": [item for item in suspects if item["priority"] == "high"],
        "weak": [item for item in suspects if item["priority"] == "low"],
    }


def _owner_brief(db: Session, book_id: int, alias: str, limit: int = 3) -> str:
    rows = (
        db.query(Character.canonical_name, CharacterAlias.occurrence_count)
        .join(Character, Character.id == CharacterAlias.character_id)
        .filter(
            CharacterAlias.book_id == book_id,
            CharacterAlias.alias == alias,
        )
        .order_by(CharacterAlias.occurrence_count.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return "无"
    return "；".join(f"{_text(name)} {int(count or 0)} 次" for name, count in rows)


def _review_message(db: Session, book_id: int, items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for index, item in enumerate(items, start=1):
        lines.append(f"第{index}条")
        lines.append(f"称呼：{item['alias']}")
        lines.append(f"它被归给：{item['canonical_name']}")
        lines.append(f"全书分布：{_owner_brief(db, book_id, item['alias'])}")
        lines.append("原文片段：")
        for sample in item["samples"][:MAX_SAMPLES]:
            label = (
                "未定位"
                if sample["chapter_number"] is None
                else f"第{sample['chapter_number']}章"
            )
            lines.append(f"（{label}）{sample['text']}")
        lines.append("")
    lines.append("按顺序输出 JSON 数组，每项对应一条。")
    return "\n".join(lines)


def _parse_verdicts(content: str, expected: int) -> dict[int, dict[str, Any]]:
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
    except (ValueError, TypeError):
        return {}
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return result
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
            "reason": _text(row.get("reason"))[:120],
        }
    return result


def review_suspects(
    db: Session,
    book_id: int,
    report: dict[str, Any] | None = None,
    provider_id: int | None = None,
    limit: int = 10,
    client: Any = None,
) -> dict[str, Any]:
    """把规则筛出来的可疑项送模型复核。

    只做标注，不删不改。用另一个模型交叉检查（生成用 A、复核用 B），
    避免同一个模型在生成和判断时犯同一个错。
    """
    report = report or audit_aliases(db, book_id)
    provider = llm_provider_service.get_active_provider_config(db, provider_id)
    # 单点是非判断用不上推理链，显式关掉思考省钱
    provider = {**provider, "disable_thinking": True}

    pool = [item for item in report["suspects"] + report["weak"] if item["samples"]]
    pool.sort(key=lambda item: (-item["severity"], item["total_count"]))
    selected = [dict(item) for item in pool[: max(0, int(limit))]]

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for offset in range(0, len(selected), REVIEW_BATCH):
        batch = selected[offset : offset + REVIEW_BATCH]
        response = llm_service.chat_completion(
            provider,
            [
                {"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": _review_message(db, book_id, batch)},
            ],
            max_tokens=REVIEW_MAX_TOKENS,
            temperature=0,
            timeout=90,
            client=client,
        )
        if not response.get("content"):
            errors.append(
                f"第 {offset // REVIEW_BATCH + 1} 批失败：{response.get('error') or '空响应'}"
            )
            for item in batch:
                item["review"] = {"verdict": "unclear", "reason": "模型未返回"}
                results.append(item)
            continue
        verdicts = _parse_verdicts(response["content"], len(batch))
        for index, item in enumerate(batch, start=1):
            item["review"] = verdicts.get(
                index, {"verdict": "unclear", "reason": "解析不出判定"}
            )
            results.append(item)

    counts = {"ok": 0, "wrong": 0, "unclear": 0}
    for item in results:
        counts[item["review"]["verdict"]] += 1
    return {
        "book_id": book_id,
        "provider": {"id": provider["provider_id"], "model": provider["model"]},
        "reviewed": len(results),
        "counts": counts,
        "items": results,
        "errors": errors,
    }
