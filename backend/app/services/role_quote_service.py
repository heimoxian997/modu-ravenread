"""Locate a continuous source quote, tolerating formatting but never omitted words."""

import re
import unicodedata


class QuoteMatchError(Exception):
    pass


def _normalized(text):
    characters, positions = [], []
    for index, character in enumerate(text):
        if character.isspace():
            continue
        if unicodedata.category(character).startswith("P"):
            adjacent = index > 0 and index + 1 < len(text) and (
                (text[index - 1].isascii() and text[index - 1].isalnum() and
                 text[index + 1].isascii() and text[index + 1].isalnum()) or
                (text[index - 1].isdigit() and text[index + 1].isdigit()))
            numeric_sign = character in "-－﹣" and index + 1 < len(text) and text[index + 1].isdigit()
            if not adjacent and not numeric_sign and character not in "%％":
                continue
            mapped = unicodedata.normalize("NFKC", character)
            character = mapped if len(mapped) == 1 else character
        characters.append(character)
        positions.append(index)
    return "".join(characters), positions


def locate_quote(text, quote):
    start = text.find(quote)
    if start >= 0:
        if text.find(quote, start + 1) >= 0:
            raise QuoteMatchError("quote_ambiguous")
        return start, start + len(quote), "exact"
    if re.search(r"…|\.{3,}|。{3,}|[（(\[]\s*(?:省略|略)\s*[）)\]]", quote):
        raise QuoteMatchError("quote_omitted")
    wanted, _ = _normalized(quote)
    source, positions = _normalized(text)
    if len(wanted) < 4:
        raise QuoteMatchError("quote_not_found")
    found = source.find(wanted)
    if found < 0:
        raise QuoteMatchError("quote_not_found")
    if source.find(wanted, found + 1) >= 0:
        raise QuoteMatchError("quote_ambiguous")
    start, end = positions[found], positions[found + len(wanted) - 1] + 1
    if end - start > 2000:
        raise QuoteMatchError("quote_not_found")
    return start, end, "format_normalized"
