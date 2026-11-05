"""Historical source-only access to backfilled notes, with no later interpretations."""
import hashlib
import json
from sqlalchemy import func
from app.models.reading_note import ReadingNoteRevision as Revision
from app.models.chapter import Chapter
from app.services import reading_note_service as notes, note_graph_service as graph, text_window_service, tool_token_service
from app.services.note_service import NoteError, locate


def query(db,book_id,args,safe,token=None):
    signature = hashlib.sha256(json.dumps(args.model_dump(exclude={"cursor"}),sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    start = 0
    watermark = db.query(func.max(Revision.id)).filter(Revision.book_id==book_id).scalar() or 0
    if token:
        if any(token.get(key)!=value for key,value in {"kind":"note_excerpts","book":book_id,"safe":safe,"sig":signature}.items()):
            raise NoteError("invalid_cursor","摘录查询条件或阅读范围变化，请重新查询",{"reason":"excerpt_scope_changed"})
        start,watermark = token["start"],token["watermark"]
    lo,hi = args.chapter_from or 1,args.chapter_to or safe
    if lo>hi or hi>safe:raise NoteError("chapter_out_of_range","摘录范围不能超过回看章",{"allowed_max":safe})
    historical = {row.note_id:row for row in notes._latest(db,book_id,safe,watermark).filter(Revision.kind=="entry").all()}
    revisions = db.query(Revision).filter(Revision.book_id==book_id,Revision.kind=="entry",Revision.id<=watermark).order_by(Revision.id).all()
    current = {row.note_id:row for row in revisions}
    chapters = {row.idx+1:row for row in db.query(Chapter).filter(Chapter.book_id==book_id,Chapter.kind=="main",Chapter.idx>=lo-1,Chapter.idx<hi)}
    source = text_window_service.source_text(book_id)
    excerpts = {}
    for note_id,latest in current.items():
        if args.note_id and note_id!=args.note_id:continue
        row = historical.get(note_id,latest)
        if row.content.get("status","active")!="active":continue
        for evidence in row.content.get("evidence",[]):
            chapter = chapters.get(evidence["chapter_number"])
            if chapter is None:continue
            body = text_window_service.get_chapter_content(db,book_id,chapter.idx)
            match = locate(body["text"],evidence["quote"])
            if match is None:continue
            begin,end = match
            quote = source[body["char_start"]+begin:body["char_start"]+end]
            matching = [name in quote for name in args.entities]
            if matching and not (all(matching) if args.entity_match=="all" else any(matching)):continue
            key = (chapter.idx+1,begin,end)
            item = {"kind":"source_excerpt","excerpt_id":"excerpt_"+hashlib.sha256(f"{book_id}|{key}|{quote}".encode()).hexdigest()[:20],
                "chapter_number":chapter.idx+1,"text":quote,"evidence":[{"chapter_number":chapter.idx+1,"quote":quote}],
                "basis":"explicit","basis_label":"原文摘录","perspective":None,"basis_scope":"quotation",
                "projection":"evidence_only","read_only":True,"recorded_later":row.knowledge_idx>=safe}
            if key not in excerpts or not item["recorded_later"]:excerpts[key] = item
    keys = sorted(excerpts)
    scores = graph.search_scores(args.query,[excerpts[key]["text"] for key in keys])
    selected = [excerpts[key] for key,score in sorted(zip(keys,scores),key=lambda pair:(-pair[1],pair[0])) if not args.query or score>0]
    page = selected[start:start+args.limit]
    def result():
        more = start+len(page)<len(selected)
        cursor = tool_token_service.encode({"kind":"note_excerpts","book":book_id,"safe":safe,"sig":signature,
            "watermark":watermark,"start":start+len(page)}) if more else None
        return {"items":page,"matched_count":len(selected),"has_more":more,"cursor":cursor,"safe_chapter":safe,
            "time_mode":"strict","knowledge_boundary_policy":"source_only",
            "time_note":"仅返回目标范围正文原句，不含补录笔记的标题、人物身份、结论或关系；不能用作可写事件引用。"}
    data = result()
    while len(json.dumps(data,ensure_ascii=False))>notes.MAX_CHARS and len(page)>1:
        page.pop();data = result()
    return {"ok":True,"data":data}
