"""Chapter event projections and bounded graph neighborhoods; no additional LLM calls."""
import hashlib
import json
import re
from collections import Counter


TOPICS = (
    ("身份", "是谁", "真名", "真身", "化名", "隐瞒", "揭穿", "认出", "认出来", "得知", "知情"),
    ("信任", "相信", "怀疑", "背叛", "欺骗", "骗", "不信"),
    ("关系", "感情", "喜欢", "爱", "亲近", "疏远", "决裂", "和解"),
    ("动机", "目的", "为什么", "为何", "原因", "为了"),
    ("调查", "线索", "发现", "证据", "确认", "查明"),
)
STOP_GRAMS = {"什么", "怎么", "时候", "这个", "那个", "一下", "哪些", "是否", "后来", "之后", "之前", "事情"}


def node_id(node):
    if node.get("node_id"):
        return node["node_id"]
    # Versions deliberately excluded: correcting evidence preserves node identity.
    key = [node["chapter_number"], sorted(ref["note_id"] for ref in node["evidence"])]
    return "n_" + hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:20]


def nodes(content):
    return [{**n, "node_id": node_id(n)} for n in content.get("nodes", [])]


def search_scores(query, documents):
    """IDF-weighted literal terms plus topic expansion, using visible documents only."""
    if not query:
        return [0.0] * len(documents)
    phrases = [p.lower() for p in re.split(r"[\s,，、？?。！!]+", query) if p]
    grams = {p[i:i+2] for p in phrases for i in range(len(p)-1)} - STOP_GRAMS
    expansions = {term for group in TOPICS if any(term in query for term in group) for term in group}
    terms = grams | expansions
    texts = [text.lower() for text in documents]
    frequency = Counter(term for term in terms for text in texts if term in text)
    scores = []
    for text in texts:
        score = sum(6 for phrase in phrases if phrase in text)
        score += sum((2 if term in expansions else 1) / max(1, frequency[term]) for term in terms if term in text)
        scores.append(score)
    return scores


def fuse_scores(lexical, semantic, people):
    """Weighted reciprocal ranks avoid comparing arbitrary lexical/cosine scales."""
    result = [0.0] * len(lexical)
    for values,weight in ((lexical,1.0),(semantic,1.2),(people,0.8)):
        positive = sorted({value for value in values if value > 0}, reverse=True)
        ranks = {value:i+1 for i,value in enumerate(positive)}
        for i,value in enumerate(values):
            if value > 0:
                result[i] += weight / (20+ranks[value])
    return result


def event_projection(item):
    """Compact search result; the full evidence is read explicitly by ID/version."""
    if item["kind"] == "storyline":
        return item
    chapter = item.get("chapter_number")
    return {
        "kind": "entry", "note_id": item["note_id"], "version": item["version"],
        "node_id": item["note_id"], "chapter_number": chapter,
        "known_by_chapter": item["known_by_chapter"], "title": item["title"],
        "conclusion": item["claim"][:180], "entities": item["entities"],
        "conclusion_truncated": len(item["claim"])>180,
        "event_chapters": item["event_chapters"],
        "basis": item["basis"], "status": item.get("status", "active"),
        "evidence_chapters": sorted({e["chapter_number"] for e in item["evidence"]}),
        "detail_ref": {"note_id": item["note_id"], "version": item["version"]},
        "scope": "chapter_event" if item.get("chapter_number") else "legacy_multi_chapter",
    }


def graph_page(items, edge_sources):
    """Include edges only when both endpoints appear on this page. No dangling IDs."""
    visible = {item.get("graph_id") or item.get("node_id") for item in items}
    edges = []
    for line_id, edge in edge_sources:
        source, target = f"{line_id}:{edge['source']}", f"{line_id}:{edge['target']}"
        if source in visible and target in visible:
            edges.append({**edge, "source": source, "target": target, "storyline_id": line_id})
    return {"nodes": items, "edges": edges, "edge_scope": "仅当前页节点间的显式连接；按剧情线或节点展开查看其他分支"}
