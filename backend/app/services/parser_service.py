"""章节格式推导与全文解析。

设计要点：
- 章节样例只用于推导“前缀 + 数字 + 单位”的整行正则，不依赖标题文字；
- 只匹配整行且去空白后不超过 80 字，避免正文中的“第 X 章”被误判；
- 一行最多命中一次，番外优先于正文；
- 解析结果只返回偏移，不返回全书正文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.common.exceptions import BusinessException

NUMBER_PATTERN = r"[0-9零〇一二三四五六七八九十百千万两]+"
UNIT_CHARS = "章回节卷话集篇"
SEPARATOR_PATTERN = r"[\s、.．：:·\-—_]*"
SEPARATOR_REQUIRED_PATTERN = r"[\s、.．：:·\-—_]+"
MAX_HEADING_LENGTH = 80
FALLBACK_CHUNK_SIZE = 4000

# 章标题行首常见的装饰符号。它们不属于章节格式，推导和匹配时忽略，
# 但章节切片仍从原行第一个非空白字符开始，确保原文不丢字符。
_HEADING_DECORATION_CHARS = (
    " \t\u3000☆★◆◇●○■□▲△▼▽※§#＃*＊·•、，,。.．:：;；!！?？-—–_=+~～"
)

_NUMBER_RE = re.compile(NUMBER_PATTERN)
_KNOWN_EXTRA_PREFIX_RE = re.compile(r"^(番外(?:篇)?|extra(?:\s+chapter)?)", re.IGNORECASE)
_SUSPECT_RE = re.compile(
    r"(第\s*[0-9零〇一二三四五六七八九十百千万两]{1,12}\s*[章回节卷话集篇]"
    r"|番外|序章|楔子|尾声|后记|引子"
    r"|^序(?:$|\s|[、.．：:·\-—_])"
    r"|^章(?:$|\s)"
    r"|^回(?:$|\s))"
)

_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_SMALL_UNITS = {"十": 10, "百": 100, "千": 1000}


@dataclass
class ChapterRule:
    prefix: str
    unit: str
    pattern: str
    sample_titles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefix": self.prefix,
            "unit": self.unit,
            "pattern": self.pattern,
            "samples": self.sample_titles,
        }


@dataclass
class ExtraRule:
    prefixes: list[str]
    pattern: str
    sample_titles: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prefixes": self.prefixes,
            "pattern": self.pattern,
            "samples": self.sample_titles,
        }


def strip_heading_decoration(line: str) -> str:
    """去掉标题行首的装饰符，不改变原文字符位置。"""
    return line.lstrip(_HEADING_DECORATION_CHARS)


def chinese_number_to_int(token: str) -> int | None:
    """把阿拉伯数字 / 中文数字转为整数，无法识别时返回 None。"""
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)

    total = 0
    section = 0
    number = 0
    saw_unit = False
    for char in token:
        if char.isdigit():
            number = int(char)
        elif char in _DIGITS:
            number = _DIGITS[char]
        elif char in _SMALL_UNITS:
            saw_unit = True
            section += (number or 1) * _SMALL_UNITS[char]
            number = 0
        elif char == "万":
            saw_unit = True
            section += number
            total += (section or 1) * 10000
            section = 0
            number = 0
        else:
            return None

    if not saw_unit:
        # “二〇二三”这类逐位写法。
        if all(char in _DIGITS for char in token):
            digits = "".join(str(_DIGITS[char]) for char in token)
            return int(digits)
    return total + section + number


def _build_chapter_pattern(prefix: str, unit: str) -> str:
    return (
        "^"
        + re.escape(prefix)
        + r"\s*("
        + NUMBER_PATTERN
        + r")\s*"
        + re.escape(unit)
        + SEPARATOR_PATTERN
        + r"(.*)$"
    )


def derive_chapter_rules(samples: list[str]) -> list[ChapterRule]:
    """从 1–2 个章节样例推导规则。

    每个样例独立推导；前缀和单位相同的写法会自动合并，前缀/单位不同的
    样例则作为两套规则同时生效。行首的 ☆、●、※、- 等装饰会先剥离，
    英文前缀大小写不敏感，因此“☆、chapter 3”与“chapter 4”只算一套规则。
    """
    cleaned = [sample.strip() for sample in samples if sample and sample.strip()]
    if not cleaned:
        raise BusinessException(
            message="请至少填写 1 个章节标题样例，例如“第一章 初见”", code=4002
        )

    rules: list[ChapterRule] = []
    rule_by_signature: dict[tuple[str, str], ChapterRule] = {}
    for sample in cleaned:
        match_text = strip_heading_decoration(sample)
        match = _NUMBER_RE.search(match_text)
        if not match:
            raise BusinessException(
                message=f"样例“{sample}”中没有找到章节号，请按书里原样填写",
                code=4002,
            )
        prefix = match_text[: match.start()].strip()
        cursor = match.end()
        while cursor < len(match_text) and match_text[cursor].isspace():
            cursor += 1
        unit = (
            match_text[cursor]
            if cursor < len(match_text) and match_text[cursor] in UNIT_CHARS
            else ""
        )
        signature = (prefix.casefold(), unit.casefold())

        if signature in rule_by_signature:
            rule = rule_by_signature[signature]
            if sample not in rule.sample_titles:
                rule.sample_titles.append(sample)
            continue

        rule = ChapterRule(
            prefix=prefix,
            unit=unit,
            pattern=_build_chapter_pattern(prefix, unit),
            sample_titles=[sample],
        )
        rules.append(rule)
        rule_by_signature[signature] = rule

    return rules


def derive_chapter_rule(samples: list[str]) -> ChapterRule:
    """返回第一套章节规则，保留给只关心主规则的调用方。"""
    return derive_chapter_rules(samples)[0]


def _guess_extra_prefix(sample: str) -> str:
    known = _KNOWN_EXTRA_PREFIX_RE.match(sample)
    if known:
        return known.group(1).strip()
    number = _NUMBER_RE.search(sample)
    if number and number.start() > 0:
        return sample[: number.start()].rstrip(" \t、.．：:·-—_").strip()
    # 没有数字时，取第一个分隔符之前的内容作为固定前缀。
    separator = re.search(r"[\s、.．：:·\-—_]", sample)
    if separator and separator.start() > 0:
        return sample[: separator.start()].strip()
    return ""


def derive_extra_rule(samples: list[str]) -> ExtraRule | None:
    """从 0–2 个番外样例推导番外前缀；不同前缀会合并为候选。"""
    cleaned = [sample.strip() for sample in samples if sample and sample.strip()]
    if not cleaned:
        return None

    prefixes: list[str] = []
    for sample in cleaned:
        prefix = _guess_extra_prefix(strip_heading_decoration(sample))
        if not prefix:
            raise BusinessException(
                message=f"番外样例“{sample}”缺少固定前缀，例如“番外：标题”",
                code=4004,
            )
        if not any(item.lower() == prefix.lower() for item in prefixes):
            prefixes.append(prefix)

    # 长前缀优先，避免“番外”抢先匹配“番外篇”。
    prefixes.sort(key=len, reverse=True)
    alternatives: list[str] = []
    for prefix in prefixes:
        if prefix in {"番外", "番外篇"}:
            alternatives.append("番外(?:篇)?")
        else:
            alternatives.append(re.escape(prefix))
    prefix_alt = "|".join(dict.fromkeys(alternatives))
    # 可选数字后面必须是分隔符或行尾；前缀后也必须有分隔符或结束，
    # 避免把正文中的“番外正文……”误判成番外标题。
    pattern = (
        r"^(?:"
        + prefix_alt
        + r")\s*(?:("
        + NUMBER_PATTERN
        + r")(?=\s|"
        + r"[\s、.．：:·\-—_]|$))?(?:"
        + SEPARATOR_REQUIRED_PATTERN
        + r"(.*))?$"
    )
    return ExtraRule(prefixes=prefixes, pattern=pattern, sample_titles=cleaned)


def _safe_title(captured: str | None, full_line: str) -> str:
    title = (captured or "").strip()
    return title or full_line.strip()


def _warning(code: str, message: str, level: str = "warning", **extra) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "level": level, "message": message}
    item.update(extra)
    return item


def _looks_suspicious(line: str) -> bool:
    return len(line) <= MAX_HEADING_LENGTH and bool(_SUSPECT_RE.search(line))


def _empty_body(text: str, heading_line_end: int, chapter_end: int) -> bool:
    return not text[heading_line_end:chapter_end].strip()


def _make_stats(text: str, chapters: list[dict[str, Any]], front_matter_chars: int) -> dict[str, Any]:
    word_counts = [chapter["word_count"] for chapter in chapters]
    count = len(chapters)
    shortest = min(chapters, key=lambda item: item["word_count"]) if chapters else None
    longest = max(chapters, key=lambda item: item["word_count"]) if chapters else None
    return {
        "total_chars": len(text),
        "chapter_count": count,
        "main_chapter_count": sum(1 for item in chapters if item["kind"] == "main"),
        "extra_chapter_count": sum(1 for item in chapters if item["kind"] == "extra"),
        "avg_word_count": round(sum(word_counts) / count) if count else 0,
        "min_word_count": min(word_counts) if word_counts else 0,
        "max_word_count": max(word_counts) if word_counts else 0,
        "front_matter_chars": front_matter_chars,
        "shortest_chapter": (
            {
                "title": shortest["title"],
                "word_count": shortest["word_count"],
                "kind": shortest["kind"],
            }
            if shortest
            else None
        ),
        "longest_chapter": (
            {
                "title": longest["title"],
                "word_count": longest["word_count"],
                "kind": longest["kind"],
            }
            if longest
            else None
        ),
    }


def _fallback_chapters(text: str) -> list[dict[str, Any]]:
    """零命中时按约 4000 字切分，优先在换行处断开。"""
    chapters: list[dict[str, Any]] = []
    start = 0
    length = len(text)
    index = 1
    while start < length:
        target = min(start + FALLBACK_CHUNK_SIZE, length)
        end = target
        if target < length:
            lower = max(start + int(FALLBACK_CHUNK_SIZE * 0.8), start + 1)
            newline = text.rfind("\n", lower, target)
            if newline >= lower:
                end = newline + 1
        if end <= start:
            end = target
        chapters.append(
            {
                "start": start,
                "end": end,
                "title": f"第{index}章",
                "kind": "main",
                "word_count": end - start,
            }
        )
        start = end
        index += 1
    return chapters


def _build_rules(
    chapter_rules: list[ChapterRule],
    extra_rule: ExtraRule | None,
    fallback_used: bool,
) -> dict[str, Any]:
    chapter_dicts = [rule.to_dict() for rule in chapter_rules]
    return {
        # chapter 保留第一套规则，兼容旧前端；chapters 是同时生效的完整列表。
        "chapter": chapter_dicts[0],
        "chapters": chapter_dicts,
        "multiple_chapter_formats": len(chapter_dicts) > 1,
        "extra": extra_rule.to_dict() if extra_rule else None,
        "fallback": fallback_used,
        "confidence": "low" if fallback_used else "high",
    }


def parse_novel(
    text: str,
    chapter_samples: list[str],
    extra_samples: list[str] | None = None,
    fallback: bool = False,
) -> dict[str, Any]:
    """解析整本小说，返回章节偏移、统计、警告与推导规则。"""
    chapter_rules = derive_chapter_rules(chapter_samples)
    extra_rule = derive_extra_rule(extra_samples or [])

    chapter_matchers = [
        (rule, re.compile(rule.pattern, re.IGNORECASE)) for rule in chapter_rules
    ]
    extra_re = re.compile(extra_rule.pattern, re.IGNORECASE) if extra_rule else None

    matches: list[dict[str, Any]] = []
    suspicious: list[tuple[int, str]] = []
    line_number = 0
    position = 0

    for line in text.split("\n"):
        line_number += 1
        line_start = position
        position += len(line) + 1
        stripped = line.strip()
        if not stripped:
            continue

        if len(stripped) > MAX_HEADING_LENGTH:
            continue

        leading = len(line) - len(line.lstrip())
        start = line_start + leading
        heading_line_end = line_start + len(line)
        match_text = strip_heading_decoration(stripped)

        extra_match = extra_re.match(match_text) if extra_re else None
        if extra_match:
            matches.append(
                {
                    "start": start,
                    "title": _safe_title(extra_match.group(2), stripped),
                    "kind": "extra",
                    "number": extra_match.group(1),
                    "line_end": heading_line_end,
                    "line_number": line_number,
                    "line_text": stripped,
                }
            )
            continue

        chapter_match = None
        for _, matcher in chapter_matchers:
            candidate = matcher.match(match_text)
            if candidate:
                chapter_match = candidate
                break

        if chapter_match:
            matches.append(
                {
                    "start": start,
                    "title": _safe_title(chapter_match.group(2), stripped),
                    "kind": "main",
                    "number": chapter_match.group(1),
                    "line_end": heading_line_end,
                    "line_number": line_number,
                    "line_text": stripped,
                }
            )
            continue

        if _looks_suspicious(stripped):
            suspicious.append((line_number, stripped))

    warnings: list[dict[str, Any]] = []
    if len(chapter_rules) > 1:
        labels = "、".join(
            f"“{rule.prefix}…{rule.unit or ''}”" for rule in chapter_rules
        )
        warnings.append(
            _warning(
                "multiple_chapter_formats",
                f"检测到 {len(chapter_rules)} 种章节格式，已同时启用：{labels}。"
                "请预览确认两类标题都被正确识别。",
            )
        )

    if not matches:
        warnings.append(
            _warning(
                "no_chapters",
                "没有识别到任何章节。请核对章节样例是否与书中标题完全一致，"
                "或选择“按约 4000 字自动切分”降级方案。",
            )
        )
    elif len(matches) == 1:
        warnings.append(
            _warning(
                "single_chapter",
                "只识别到 1 个章节，可能样例格式与书中不一致，建议检查后再确认。",
            )
        )

    chapters: list[dict[str, Any]] = []
    for index, item in enumerate(matches):
        end = matches[index + 1]["start"] if index + 1 < len(matches) else len(text)
        chapters.append(
            {
                "start": item["start"],
                "end": end,
                "title": item["title"],
                "kind": item["kind"],
                "word_count": max(0, end - item["start"]),
            }
        )

    # 章节号重复 / 倒退：只警告，不阻断。番外序号单独看，不参与正文序号比较。
    seen_numbers: dict[int, int] = {}
    previous_number: int | None = None
    for index, item in enumerate(matches):
        if item["kind"] != "main" or not item["number"]:
            continue
        number = chinese_number_to_int(item["number"])
        if number is None:
            continue
        if number in seen_numbers:
            warnings.append(
                _warning(
                    "duplicate_number",
                    f"章节号重复：第 {number} 章出现在第 {seen_numbers[number] + 1} 和第 {index + 1} 个位置。",
                    chapter_index=index,
                )
            )
        elif previous_number is not None and number < previous_number:
            warnings.append(
                _warning(
                    "number_backwards",
                    f"章节号出现倒退：第 {number} 章位于第 {previous_number} 章之后。",
                    chapter_index=index,
                )
            )
        seen_numbers.setdefault(number, index)
        previous_number = number

    for index, (item, chapter) in enumerate(zip(matches, chapters)):
        if _empty_body(text, item["line_end"], chapter["end"]):
            warnings.append(
                _warning(
                    "empty_chapter",
                    f"《{chapter['title']}》正文为空，确认前可合并或删除该边界。",
                    chapter_index=index,
                )
            )

    extra_indexes = [index for index, item in enumerate(matches) if item["kind"] == "extra"]
    main_indexes = [index for index, item in enumerate(matches) if item["kind"] == "main"]
    if extra_indexes and main_indexes and max(extra_indexes) < max(main_indexes):
        # 最后一个番外之后仍有正文，说明番外被夹在正文中间或放在最前面。
        warnings.append(
            _warning(
                "extra_not_last",
                "番外没有全部排在正文末尾，已保留识别结果，可在预览中手动切换类型。",
            )
        )

    if extra_rule is None:
        extra_like = [
            (line_no, line)
            for line_no, line in suspicious
            if "番外" in line
        ]
        if extra_like:
            warnings.append(
                _warning(
                    "possible_extra",
                    "发现疑似番外标题，但未填写番外样例，因此没有标记为番外。"
                    "可在上一步补充番外样例后重新解析。",
                    lines=[
                        {"line": line_no, "text": line[:60]}
                        for line_no, line in extra_like[:5]
                    ],
                )
            )

    general_suspicious = [
        (line_no, line)
        for line_no, line in suspicious
        if "番外" not in line
    ]
    if general_suspicious:
        examples = "；".join(f"第{line_no}行“{line[:32]}”" for line_no, line in general_suspicious[:5])
        extra_count = len(general_suspicious) - 5
        suffix = f"等 {len(general_suspicious)} 处" if extra_count > 0 else ""
        warnings.append(
            _warning(
                "possible_missed",
                f"疑似漏识别：{examples}{suffix}。这些短行含有章节/序章等关键词，"
                "但没有匹配当前格式，请预览时留意。",
                lines=[
                    {"line": line_no, "text": line[:60]}
                    for line_no, line in general_suspicious[:10]
                ],
            )
        )

    fallback_used = False
    if not chapters and fallback:
        chapters = _fallback_chapters(text)
        fallback_used = True
        # 已经生成降级章节，不再保留“没有识别到任何章节”的误导性提示。
        warnings = [item for item in warnings if item.get("code") != "no_chapters"]
        warnings.append(
            _warning(
                "fallback_used",
                "已使用“按约 4000 字自动切分”降级方案，章节标题为临时生成，"
                "解析置信度较低。建议在预览中检查并手动调整。",
                level="error",
            )
        )

    if chapters:
        front_matter_chars = chapters[0]["start"]
    else:
        front_matter_chars = len(text)

    stats = _make_stats(text, chapters, front_matter_chars)
    return {
        "rules": _build_rules(chapter_rules, extra_rule, fallback_used),
        "chapters": chapters,
        "warnings": warnings,
        "stats": stats,
        "front_matter_chars": front_matter_chars,
    }
