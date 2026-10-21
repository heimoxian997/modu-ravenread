"""确认入库前的章节边界校验与切片。"""

from __future__ import annotations

from typing import Any

from app.common.exceptions import BusinessException


def _auto_title(index: int, kind: str) -> str:
    return f"第{index + 1}章" if kind == "main" else f"番外{index + 1}"


def validate_boundaries(
    chapters: list[Any], text_length: int
) -> list[dict[str, Any]]:
    """校验有序边界，返回可直接入库的章节字段。

    规则：start 严格递增、start < end、不越界、相邻章节首尾相接（无缺口/重叠），
    最后一章必须覆盖到源文件末尾。首个章节之前允许存在 front_matter。
    校验失败抛出字段级 BusinessException，不写库。
    """
    if not chapters:
        raise BusinessException(
            message="至少保留 1 个章节才能入库",
            code=4005,
            data={"errors": [{"field": "chapters", "message": "章节列表不能为空"}]},
        )

    errors: list[dict[str, str]] = []
    prepared: list[dict[str, Any]] = []
    previous_start: int | None = None
    previous_end: int | None = None

    for index, chapter in enumerate(chapters):
        start = chapter.start
        end = chapter.end
        title = (chapter.title or "").strip()
        kind = chapter.kind
        field_prefix = f"chapters[{index}]"

        if start < 0 or start >= text_length:
            errors.append(
                {
                    "field": f"{field_prefix}.start",
                    "message": f"起始偏移 {start} 越界，应在 0 到 {text_length - 1} 之间",
                }
            )
        if end <= 0 or end > text_length:
            errors.append(
                {
                    "field": f"{field_prefix}.end",
                    "message": f"结束偏移 {end} 越界，应在 1 到 {text_length} 之间",
                }
            )
        if start >= end:
            errors.append(
                {
                    "field": f"{field_prefix}.start",
                    "message": "起始偏移必须小于结束偏移",
                }
            )
        if title and len(title) > 255:
            errors.append(
                {
                    "field": f"{field_prefix}.title",
                    "message": "章节标题不能超过 255 个字符",
                }
            )
        if previous_start is not None and start <= previous_start:
            errors.append(
                {
                    "field": f"{field_prefix}.start",
                    "message": "章节起始偏移必须严格递增",
                }
            )
        if previous_end is not None:
            if start < previous_end:
                errors.append(
                    {
                        "field": f"{field_prefix}.start",
                        "message": "与上一章结束偏移重叠",
                    }
                )
            elif start > previous_end:
                errors.append(
                    {
                        "field": f"{field_prefix}.start",
                        "message": "与上一章之间存在未分配的文字（缺口）",
                    }
                )

        prepared.append(
            {
                "start": start,
                "end": end,
                "title": title or _auto_title(index, kind),
                "kind": kind,
                "word_count": max(0, end - start),
            }
        )
        previous_start = start
        previous_end = end

    if previous_end is not None and previous_end != text_length:
        errors.append(
            {
                "field": f"chapters[{len(chapters) - 1}].end",
                "message": (
                    f"最后一章必须结束于源文件末尾（{text_length}），"
                    f"当前为 {previous_end}，否则会丢失尾部文字"
                ),
            }
        )

    if errors:
        raise BusinessException(
            message="章节边界校验失败，请检查章节顺序与范围",
            code=4005,
            data={"errors": errors},
        )

    return prepared


def slice_chapter(text: str, start: int, end: int) -> str:
    """按 Unicode 码点偏移切片。"""
    return text[start:end]


def front_matter_of(text: str, first_start: int) -> str:
    """首个章节之前的文字。"""
    return text[:first_start] if first_start > 0 else ""
