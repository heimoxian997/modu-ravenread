"""Pure chapter-number projection; safe to import in the lightweight MCP process."""
CHAPTER_KEYS = {"main_text_end_chapter_idx":"main_text_end_chapter","safe_main_chapter_idx":"safe_main_chapter",
    "chapter_idx":"chapter_number", "idx":"chapter_number", "as_of_chapter":"as_of_chapter",
    "safe_chapter":"safe_chapter", "chapter_from":"chapter_from", "chapter_to":"chapter_to",
    "from_chapter":"chapter_from", "to_chapter":"chapter_to", "next_from_chapter":"next_from_chapter",
    "first_seen_chapter":"first_seen_chapter", "last_seen_chapter":"last_seen_chapter",
    "match_chapter_idx":"match_chapter_number", "requested_chapter":"requested_chapter",
    "requested_from_chapter":"requested_from_chapter", "requested_to_chapter":"requested_to_chapter",
    "allowed_max":"allowed_max", "max_processed":"max_processed", "gate":"gate", "gate_chapter":"gate_chapter", "override_from_chapter":"override_from_chapter"}
HIDDEN = {"char_start","char_end","char_offset","offset","chapter_offset","next_offset",
          "absolute","pos","hit_char_start","hit_char_end","character_id","canonical_name"}

def public(value):
    if isinstance(value, list):
        return [public(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for k,v in value.items():
        if k in HIDDEN:
            continue
        if k in CHAPTER_KEYS and isinstance(v, int) and not isinstance(v, bool):
            result[CHAPTER_KEYS[k]] = v + 1 if v >= 0 else None
        elif k == "chapters" and isinstance(v,list):
            result[k] = [n+1 for n in v]
        else:
            result[k] = public(v)
    return result

