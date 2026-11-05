"""Gated evidence backlinks, revision review and duplicate candidates."""
import hashlib
import json
from app.models.reading_note import ReadingNoteRevision as Revision
from app.services import reading_note_service as memory, note_graph_service as graph, tool_token_service
from app.services.note_service import NoteError


def references(db, book_id, args):
    safe = memory.gate(db,book_id,args.as_of_chapter)
    signature = hashlib.sha256(json.dumps(args.model_dump(exclude={"cursor"}),sort_keys=True).encode()).hexdigest()
    start = 0
    watermark = db.query(Revision.id).filter(Revision.book_id==book_id,Revision.knowledge_idx<safe).order_by(Revision.id.desc()).first()
    watermark = watermark[0] if watermark else 0
    if args.cursor:
        try:token = tool_token_service.decode(args.cursor)
        except ValueError as exc:raise NoteError("invalid_cursor",str(exc))
        if any(token.get(k)!=v for k,v in {"book":book_id,"safe":safe,"sig":signature,"view":"references"}.items()):
            raise NoteError("invalid_cursor","阅读进度或查询条件已变化，请重新反查")
        start,watermark = token["start"],token["watermark"]
    latest = {row.note_id:row for row in memory._latest(db,book_id,safe,watermark).all()}
    target = latest.get(args.note_id)
    if target is None or target.kind!="entry":
        raise NoteError("not_found","当前范围内没有这条事件笔记")
    if args.version is not None:
        exists = db.query(Revision.id).filter_by(book_id=book_id,note_id=args.note_id,version=args.version).filter(
            Revision.id<=watermark,Revision.knowledge_idx<safe).first()
        if not exists:raise NoteError("not_found","当前范围内没有该证据版本")
    items = []
    for line in sorted(latest.values(),key=lambda r:r.note_id):
        if line.kind!="storyline":continue
        stale = set(memory._dependency_state(line,latest))
        for node in graph.nodes(line.content):
            if args.chapter_from and node["chapter_number"]<args.chapter_from:continue
            if args.chapter_to and node["chapter_number"]>args.chapter_to:continue
            for ref in node["evidence"]:
                if ref["note_id"]==args.note_id and (args.version is None or ref["version"]==args.version):
                    items.append({"kind":"storyline_reference","note_id":line.note_id,"version":line.version,
                        "title":line.content["title"],"node_id":node["node_id"],"chapter_number":node["chapter_number"],
                        "evidence_version":ref["version"],"needs_revision":node["node_id"] in stale})
    page = items[start:start+args.limit]
    more = start+len(page)<len(items)
    cursor = tool_token_service.encode({"book":book_id,"safe":safe,"sig":signature,"view":"references",
        "start":start+len(page),"watermark":watermark}) if more else None
    return {"ok":True,"data":{"items":page,"matched_count":len(items),"safe_chapter":safe,
        "has_more":more,"cursor":cursor,"reference_scope":"当前安全版本的引用，含待修订节点；不含已删除剧情线"}}


def duplicates(db, book_id, args, safe_override=None):
    safe = memory.gate(db,book_id,safe_override)
    lo = args.chapter_number or args.chapter_from or 1
    hi = args.chapter_number or args.chapter_to or safe
    if lo>hi or hi>safe:
        raise NoteError("chapter_out_of_range",f"查重范围必须在第1–{safe}章内",{"allowed_max":safe})
    if args.chapter_number:memory._chapter(db,book_id,args.chapter_number,safe)
    identity_cache = {}
    def identity(name):
        if name not in identity_cache:identity_cache[name] = memory._identity(db,book_id,name,safe)
        return identity_cache[name]
    candidates = []
    for row in memory._latest(db,book_id,safe).filter(Revision.kind=="entry").all():
        content = row.content
        if content.get("status","active")!="active" or not memory._entry_scope_valid(content):continue
        if not any(lo<=chapter<=hi for chapter in content["event_chapters"]):continue
        same_quote = bool(args.quote and any(args.quote in item["quote"] or item["quote"] in args.quote for item in content["evidence"]))
        names = [name for name in args.entities if any(name==n or identity(name)&identity(n) for n in content["entities"])]
        lexical = graph.search_scores(args.query,[graph.search_text(content)])[0] if args.query else 0
        if not (same_quote or names or lexical):continue
        reasons = (["same_evidence"] if same_quote else []) + (["shared_entities"] if names else []) + (["text_overlap"] if lexical else [])
        candidates.append((100*same_quote+lexical+len(names),row,reasons))
    candidates.sort(key=lambda value:(-value[0],value[1].note_id))
    items = [{"note_id":row.note_id,"version":row.version,"chapter_number":row.content.get("chapter_number"),
        "event_chapters":row.content["event_chapters"],
        "title":row.content["title"],"claim":row.content["claim"][:180],"candidate_reasons":reasons,
        "evidence_quotes":[e["quote"] for e in row.content["evidence"]][:2]} for _,row,reasons in candidates[:args.limit]]
    while len(json.dumps(items,ensure_ascii=False))>4500 and len(items)>1:items.pop()
    return {"ok":True,"data":{"items":items,"matched_count":len(candidates),"has_more":len(candidates)>len(items),
        "safe_chapter":safe,"search_scope":{"chapter_from":lo,"chapter_to":hi},
        "comparison_note":"候选不代表重复；同一证据可能支持不同结论，请比较后修订或新增，不自动合并。"}}


def review_node(db,book_id,args):
    safe = memory.gate(db,book_id,args.as_of_chapter)
    latest = {row.note_id:row for row in memory._latest(db,book_id,safe).all()}
    line = latest.get(args.note_id)
    if line is None or line.kind!="storyline":raise NoteError("not_found","当前范围内没有这条剧情线")
    node = next((n for n in graph.nodes(line.content) if n["node_id"]==args.node_id),None)
    if node is None:raise NoteError("not_found","该节点不存在")
    if (args.chapter_from and node["chapter_number"]<args.chapter_from) or (args.chapter_to and node["chapter_number"]>args.chapter_to):
        raise NoteError("not_found","该节点不在指定章节范围内")
    comparisons = []
    for ref in node["evidence"]:
        old = db.query(Revision).filter_by(book_id=book_id,note_id=ref["note_id"],version=ref["version"],kind="entry").filter(Revision.knowledge_idx<safe).first()
        current = latest.get(ref["note_id"])
        comparisons.append({"note_id":ref["note_id"],"previous_version":ref["version"],
            "previous_claim":old.content["claim"][:180] if old else None,
            "current_version":current.version if current else None,"current_claim":current.content["claim"][:180] if current else None,
            "current_status":current.content.get("status","active") if current else "unavailable",
            "changed_fields":[key for key in ("title","claim","payload","basis","perspective","evidence","status")
                if old is None or current is None or old.content.get(key)!=current.content.get(key)],
            "evidence_changed":old is None or current is None or old.content["evidence"]!=current.content["evidence"]})
    return {"ok":True,"data":{"review_only":True,"note_id":line.note_id,"version":line.version,
        "node_id":node["node_id"],"chapter_number":node["chapter_number"],"previous_conclusion":node["conclusion"],
        "evidence":comparisons,"needs_revision":node["node_id"] in memory._dependency_state(line,latest),
        "next_action":"这是旧结论审阅信息，不作回答依据。按当前事件ID/版本读取证据；结论仍成立时refresh_storyline_evidence，否则storyline_patch。"}}
