"""Decode complete JSON responses with optional Markdown fencing. Author: heimoxian."""
import json
import re


def decode_json(content):
    if not isinstance(content, str):
        raise ValueError("missing JSON response")
    text = content.strip().lstrip("\ufeff")
    fenced = re.fullmatch(r"```(?:json)?\s*\n?([\s\S]*?)\n?```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    return json.loads(text)
