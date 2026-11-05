"""Bounded spelling and quotation suggestions from already-gated source text."""
from difflib import SequenceMatcher


def closest_quote(text, quote):
    from app.services.note_service import _normalize_with_map
    normalized, offsets = _normalize_with_map(text)
    needle, _ = _normalize_with_map(quote)
    match = SequenceMatcher(None,needle,normalized,autojunk=False).find_longest_match()
    if match.size<max(2,min(5,len(needle)//3)):return None
    expected = max(0,match.b-match.a)
    best = (0.0,0,0)
    for start in range(max(0,expected-4),min(len(normalized),expected+5)):
        for size in range(max(4,len(needle)-4),min(120,len(needle)+4)+1):
            candidate = normalized[start:start+size]
            ratio = SequenceMatcher(None,needle,candidate,autojunk=False).ratio()
            if ratio>best[0]:best = (ratio,start,start+len(candidate))
    ratio,start,end = best
    if ratio<0.5 or not offsets or end<=start:return None
    a,b = offsets[start],offsets[end-1]+1
    return {"candidate_quote":text[a:b],"similarity":round(ratio,3),"start_offset":a,"end_offset":b,
        "context":text[max(0,a-20):min(len(text),b+20)],"review_only":True}


def similar_names(name, names, chapters, source, preferred_chapter=None):
    ranked = []
    for candidate in names:
        if candidate==name:continue
        ratio = SequenceMatcher(None,name,candidate,autojunk=False).ratio()
        if ratio<0.5:continue
        occurrences = [chapter for chapter in chapters if candidate in source[chapter.char_start:chapter.char_end]]
        if not occurrences:continue
        chapter = next((c for c in occurrences if c.idx+1==preferred_chapter),occurrences[0])
        body = source[chapter.char_start:chapter.char_end]
        pos = body.find(candidate)
        ranked.append((ratio,chapter.idx+1==preferred_chapter,candidate,chapter,body[max(0,pos-20):pos+len(candidate)+20]))
    ranked.sort(key=lambda item:(-item[0],-item[1],item[2]))
    return [{"name":name,"similarity":round(score,3),"chapter_number":chapter.idx+1,"context":context}
        for score,_,name,chapter,context in ranked[:3]]
