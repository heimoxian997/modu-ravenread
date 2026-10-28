"""Stable references into frozen role scenes; never generate replacement prose."""

import re

from app.services.role_quote_service import locate_quote, QuoteMatchError

PARAGRAPH_VERSION = "paragraph_refs_v1"
MAX_PARAGRAPH_CHARS = 1600


def source_paragraphs(scenes):
    paragraphs = []
    for scene in scenes:
        number = 0
        for line in re.finditer(r"[^\r\n]+", scene.text):
            if not line.group().strip():
                continue
            start, limit = line.start(), line.end()
            while start < limit:
                end = min(start + MAX_PARAGRAPH_CHARS, limit)
                if end < limit:
                    breaks = [m.end() for m in re.finditer(r"[。！？；]", scene.text[start + 800:end])]
                    if breaks:
                        end = start + 800 + breaks[-1]
                number += 1
                paragraphs.append({
                    "paragraph_id": f"{scene.scene_id}:p{number}", "scene_id": scene.scene_id,
                    "number": number, "chapter_number": scene.chapter_number,
                    "char_start": scene.char_start + start, "char_end": scene.char_start + end,
                    "text": scene.text[start:end],
                })
                start = end
    return paragraphs


def resolve_opening(text, opening):
    """A unique optional opening narrows a paragraph to an original sentence.

    Unusable openings retain the entire known paragraph. No extra model request
    or fuzzy recovery of omitted prose is involved.
    """
    if not opening:
        return 0, len(text), "paragraph", None
    if len(re.sub(r"[\W_]", "", opening)) < 4:
        return 0, len(text), "paragraph", "opening_too_short"
    try:
        start, end, _ = locate_quote(text, opening)
    except QuoteMatchError as exc:
        return 0, len(text), "paragraph", str(exc)
    # An opening already ending a sentence does not consume the next sentence.
    if not re.search(r"[。！？!?][’”\"』」）)]*$", text[start:end]):
        stop = re.search(r"[。！？!?]", text[end:])
        if stop is None:
            return 0, len(text), "paragraph", "sentence_end_missing"
        end += stop.end()
    while end < len(text) and text[end] in '’”"』」）)':
        end += 1
    return start, end, "opening_sentence", None
