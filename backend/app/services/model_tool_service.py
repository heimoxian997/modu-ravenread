"""The single model-facing adapter. Database/API indexes stay 0-based internally."""
import hashlib
import json
from pathlib import Path
from pydantic import TypeAdapter, ValidationError
from app.config import UPLOADS_DIR
from app.models.chapter import Chapter
from app.services import agent_tool_service as legacy, text_window_service, tool_token_service
from app.services import reading_note_service, tool_reference_service
from app.services.note_service import NoteError
from app.schemas.reading_note import NoteWrite, NoteQuery, StorylineList, StorylineAction, NoteDuplicateQuery, StorylineEvidenceRefresh
from app.schemas.correction import CorrectionReport

from app.common.model_contract import public
from app.common.tool_errors import validation_error



def summary(tool, result):
    if not result.get("ok"):
        return (result.get("data") or {}).get("message") or result.get("error", "查询失败")
    data = result.get("data") or {}
    if tool in {"read_chapter", "read_chapters", "read_passage", "read_around"}:
        return f"读取 {len(data.get('segments') or [])} 段正文"
    if tool == "get_chapter_summary":
        return f"第{data.get('chapter_number')}章摘要"
    if tool == "get_character":
        return f"截至第{data.get('as_of_chapter')}章的人物卡"
    if tool == "note_upsert":
        if "proposal_id" in data:
            return "剧情线建议已提交，等待用户确认" if data.get("status") == "pending" else "剧情线建议已有审核结果"
        return f"已保存笔记版本 {data.get('version')}"
    if tool in {"delete_storyline","restore_storyline"}:
        return "已删除剧情线，可恢复" if tool=="delete_storyline" else "已恢复剧情线"
    return f"返回 {len(data.get('items') or [])} 条结果"




EXTRA_SPECS = [
    {"name":"read_chapters","description":"批量读正文，默认6000字，可设max_chars至12000；按段落收尾，返回总量/剩余量，原样传cursor续读。",
     "args":{"chapter_from":"integer >=1","chapter_to":"integer >=1，含两端","cursor":"string，可选","max_chars":"integer 200-12000，默认6000"}},
    {"name":"read_passage","description":"精读搜索返回的短 passage_ref；可调radius，默认每页2400字。to_chapter_end 读至章尾，或用chapter_cursor交给read_chapter续读。短引用在服务重启或缓存过期后须重新搜索。",
     "args":{"passage_ref":"string，原样传回","cursor":"string，可选","radius":"integer 0-6000，可选，命中前后字符数","max_chars":"integer 200-6000，默认2400","to_chapter_end":"boolean，默认false"}},
    {"name":"note_query","description":"查笔记，推荐compact=true。先看basis/perspective，节点另有evidence_basis/evidence_perspectives；unclassified不视为事实。time_mode默认strict按当时版本回看；retrospective用当前已读知识回看as_of_chapter内事件，可能含后来认识；严格回看补录原文用view=excerpts（只读原句，无后期结论）。默认focused把人物导航单列。directory默认/最多40，其他视图默认5/最多10；entry查证据、references反查、review审阅，目录cursor固定快照。",
     "args":NoteQuery.model_json_schema()},
    {"name":"list_storylines","description":"浏览已整理的剧情线和人物关系名称、阶段、概述；默认5条，可按 query/entities 筛选。需要详细节点再用 note_query(note_id)。",
     "args":StorylineList.model_json_schema()},
    {"name":"note_upsert","description":"最终回答前评估是否值得保存：新发现、关键转折、关系变化、消歧或纠错才写；允许不写。entry 保存单一结论与逐章原文；剧情线经证据与版本校验后直接保存，用户可在剧情线页编辑。节点章必须与引用事件及引文章完全相同；跨章认识另建节点。storyline 用稳定 node_id 与 edges 表达分叉汇合，引用 entry 固定版本。局部修订优先 kind=storyline_patch 并带 note_id/expected_version/change_reason，服务端保留未改动分支。解释需 perspective。不可依据摘要冒充已读原文。",
     "args":TypeAdapter(NoteWrite).json_schema()},
    {"name":"report_correction","description":"发现摘要、人物增量、别名或世界观有错时，提交 source_ref、原因、原文证据和可选字段修改，等待用户在纠错审核页确认。不能用后文证据回写早期来源。笔记错误直接 note_upsert 修订，并核对 affected_storylines。",
     "args":CorrectionReport.model_json_schema()},
    {"name":"find_note_duplicates","description":"写前按人物、拟写结论或原文查重复候选；可限定chapter_number单章或chapter_from/to区间，均省略则查已读范围。候选不代表重复，不自动合并。",
     "args":NoteDuplicateQuery.model_json_schema()},
    {"name":"delete_storyline","description":"仅在用户要求删除或合并剧情线时调用。软删除整条线，保留事件，可恢复；必须使用当前expected_version。",
     "args":StorylineAction.model_json_schema()},
    {"name":"restore_storyline","description":"用户要求恢复已删除剧情线时调用；保留原证据版本，旧证据可能需核对。expected_version从删除结果或已删除列表取得。",
     "args":StorylineAction.model_json_schema()},
    {"name":"list_deleted_storylines","description":"浏览当前安全范围内已删除的剧情线及删除版本，供按用户要求恢复。变更后重新查询，不沿用旧cursor。",
     "args":{"limit":"integer 1-20，默认10","cursor":"string，可选"}},
    {"name":"refresh_storyline_evidence","description":"核对新事件证据后，确认原节点结论仍成立时，仅更新引用版本，保留节点结论及线级元信息。先note_query(view=review,note_id,node_id)审阅，再读取当前事件证据；每个节点需明确conclusion_unchanged=true。结论有变化时用storyline_patch。",
     "args":StorylineEvidenceRefresh.model_json_schema()},
]

WRITE_TOOLS = {"note_upsert","report_correction","delete_storyline","restore_storyline","refresh_storyline_evidence"}


def _attach_sources(tool, data, ctx, args):
    """References bind the exact source snapshot, not model-supplied IDs or paths."""
    from app.services import correction_service as fixes
    from app.models.chapter_summary import ChapterSummary
    from app.models.character_chapter_delta import CharacterChapterDelta
    from app.models.character_card_override import CharacterCardOverride
    from app.models.character_alias_observation import CharacterAliasObservation
    safe = ctx.safe_chapter + 1
    def add(item, kind, row_id):
        item["source_ref"] = fixes.source_ref(ctx.db,ctx.book.id,kind,row_id,safe)
        item["correction_fields"] = sorted(fixes.FIELDS[kind])
    if tool in {"get_chapter_summary","search_summaries"}:
        for item in ([data] if tool=="get_chapter_summary" else data.get("items",[])):
            row=ctx.db.query(ChapterSummary).filter(ChapterSummary.book_id==ctx.book.id,ChapterSummary.chapter_idx==item.get("chapter_idx")).first()
            if row:add(item,"summary",row.id)
    elif tool in {"get_character_timeline","search_character_changes"}:
        for item in data.get("items",[]):
            if item.get("delta_id"):add(item,"character_delta",item["delta_id"])
    elif tool=="search_lore":
        for item in data.get("items",[]):
            if item.get("lore_id"):add(item,"lore",item["lore_id"])
    elif tool=="get_character":
        as_of=data["as_of_chapter"]
        actor,_=legacy._resolve_character(ctx,{"name":args["name"],"_as_of_chapter":as_of})
        rows=ctx.db.query(CharacterChapterDelta).filter(CharacterChapterDelta.book_id==ctx.book.id,
            CharacterChapterDelta.character_id==actor.id,CharacterChapterDelta.chapter_idx<=as_of).order_by(CharacterChapterDelta.chapter_idx.desc()).limit(6).all()
        refs=[]
        for row in rows:
            item={"chapter_idx":row.chapter_idx,"target_type":"character_delta"};add(item,"character_delta",row.id);refs.append(item)
        override=ctx.db.query(CharacterCardOverride).filter(CharacterCardOverride.book_id==ctx.book.id,
            CharacterCardOverride.character_name==actor.canonical_name,CharacterCardOverride.from_chapter<=as_of).first()
        if override:
            item={"chapter_idx":override.from_chapter,"target_type":"character_override"};add(item,"character_override",override.id);refs.insert(0,item)
        data["source_refs"]=refs
    elif tool in {"resolve_alias","list_alias_candidates"}:
        as_of=data["gate_chapter"]
        candidates,_=legacy._alias_candidates(ctx,args["alias"],as_of)
        by_name={item["display_name"]:item["character_id"] for item in candidates}
        for item in data.get("candidates",data.get("items",[])):
            actor_id=by_name.get(item["display_name"])
            if actor_id is None:continue
            rows=ctx.db.query(CharacterAliasObservation).filter(CharacterAliasObservation.book_id==ctx.book.id,
                CharacterAliasObservation.alias==args["alias"],CharacterAliasObservation.character_id==actor_id,
                CharacterAliasObservation.chapter_idx<=as_of).order_by(CharacterAliasObservation.chapter_idx.desc()).limit(2).all()
            item["source_refs"]=[]
            for row in rows:
                ref={"chapter_idx":row.chapter_idx};add(ref,"alias",row.id);item["source_refs"].append(ref)


def specs(max_chapter=None, available=None):
    result = []
    for item in legacy.specs(max_chapter, available):
        name = item["name"]
        if name == "read_span":
            continue
        item = dict(item)
        args = dict(item["args"])
        if name in {"read_chapter","get_chapter_summary"}:
            args = {"chapter_number":"integer，从1开始；第125章填125"}
            if name == "read_chapter":
                args["cursor"] = "string，可选，原样传回续读"
                args["max_chars"] = "integer 200-6000，默认2400"
        elif name == "get_toc":
            args = {"chapter_from":"integer >=1，默认1", "chapter_to":"integer >=1，默认安全章", "limit":"integer 1-100，默认20", "cursor":"string，可选"}
        elif name == "list_alias_candidates":
            args.pop("offset",None)
            args["cursor"] = "string，可选，原样传回翻页"
        elif name == "get_character_timeline":
            args["chapter_from"] = args.pop("from_chapter")
            args["chapter_to"] = args.pop("to_chapter")
        item["args"] = {k:str(v).replace("0–{max}","1–N").replace("0–"+str(max_chapter),"1–"+str((max_chapter or 0)+1)).replace("默认 0", "默认 1") for k,v in args.items()}
        item["description"] = item["description"].replace("0–"+str(max_chapter), "1–"+str((max_chapter or 0)+1))
        if name == "read_chapter":
            item["description"] = "读指定章正文，章号从1开始；默认2400字、最多6000，按段落收尾；返回总量/已读/剩余量，原样传cursor续读。"
        if name == "search_text":
            item["description"] = "查正文原句或场景；exact精确扫描，hybrid混合检索，auto先精确/已知人物别名再语义。返回检索来源与降级原因，命中附短passage_ref精读；语义命中仍需核对原文。"
            item["args"]["mode"] = "string，auto|hybrid|exact，默认hybrid"
            item["args"]["limit"] = "integer，可选，1-8；hybrid默认2，exact/auto精确阶段默认5"
        result.append(item)
    if available is None or "read_span" in available:
        result.extend(EXTRA_SPECS[:2])
    result.extend(EXTRA_SPECS[2:])
    return result


def _integer(value, name, low=1, high=None):
    if type(value) is not int or value < low or (high is not None and value > high):
        raise legacy.ToolError("invalid_arguments", f"{name} 必须是 {low}–{high or 'N'} 的整数")
    return value


def _chapter(ctx, number):
    _integer(number,"chapter_number",1)
    if number > ctx.safe_chapter + 1:
        raise legacy.ToolError("chapter_out_of_range", f"只允许第1–{ctx.safe_chapter+1}章", {"allowed_max":ctx.safe_chapter+1})
    row = ctx.db.query(Chapter).filter(Chapter.book_id==ctx.book.id,Chapter.idx==number-1).first()
    if row is None or row.kind != "main":
        raise legacy.ToolError("chapter_unavailable","章节不存在或为不参与伴读的番外")
    return row


def _source_stamp(ctx):
    stat = (Path(UPLOADS_DIR)/str(ctx.book.id)/"source.txt").stat()
    return [stat.st_mtime_ns, stat.st_size]


def _token(ctx, kind, data):
    payload = {"kind":kind,"book":ctx.book.id,"stamp":_source_stamp(ctx),**data}
    return tool_reference_service.create(payload) if kind == "passage" else tool_token_service.encode(payload)


def _decode(ctx, token, kind):
    try:
        if not isinstance(token,str):raise ValueError("引用或 cursor 必须是字符串")
        data = tool_reference_service.resolve(token) if token.startswith("p_") else tool_token_service.decode(token)
    except ValueError as exc:
        raise legacy.ToolError("invalid_cursor",str(exc),{"message":str(exc),"retry":"重新搜索或从头读取，勿修改 cursor"})
    if data.get("kind") != kind or data.get("book") != ctx.book.id or data.get("stamp") != _source_stamp(ctx):
        raise legacy.ToolError("invalid_cursor","引用不属于当前书或原文已变化，请重新查询")
    return data


def _read(ctx, args, passage=False, single=False):
    cursor = args.get("cursor")
    if passage:
        if "to_chapter_end" in args and type(args["to_chapter_end"]) is not bool:
            raise legacy.ToolError("invalid_arguments","to_chapter_end 必须是布尔值")
        ref = _decode(ctx,args.get("passage_ref", ""),"passage")
        lo = hi = ref["chapter"]
        chapter = _chapter(ctx,lo)
        start,end = ref["start"],ref["end"]
        content = text_window_service.get_chapter_content(ctx.db,ctx.book.id,lo-1)
        if args.get("radius") is not None:
            radius = _integer(args["radius"],"radius",0,6000)
            anchor_start,anchor_end = ref.get("hit_start",start),ref.get("hit_end",end)
            start,end = max(content["char_start"],anchor_start-radius),min(chapter.char_end,anchor_end+radius)
        if args.get("to_chapter_end"):
            end = chapter.char_end
        if not chapter.char_start <= start < end <= chapter.char_end:
            raise legacy.ToolError("invalid_cursor","片段范围无效")
    else:
        lo = args.get("chapter_number") if single else args.get("chapter_from")
        hi = lo if single else args.get("chapter_to")
        _chapter(ctx,lo)
        _chapter(ctx,hi)
        if lo > hi:
            raise legacy.ToolError("invalid_arguments","chapter_from 不能大于 chapter_to")
        start = end = None
    default_budget, max_budget = (2400,6000) if single or passage else (6000,12000)
    budget = _integer(args.get("max_chars",default_budget),"max_chars",200,max_budget)
    signature = hashlib.sha256(json.dumps({"lo":lo,"hi":hi,"ref":args.get("passage_ref"),
        "start":start,"end":end,"max_chars":budget},sort_keys=True).encode()).hexdigest()
    number, position = lo, start
    segment_index = 0
    if cursor:
        token = _decode(ctx,cursor,"read")
        if token["sig"] != signature:
            raise legacy.ToolError("invalid_cursor","cursor 与读取范围不匹配")
        number,position = token["chapter"],token["position"]
        segment_index = token.get("segment_index",0)
    segments = []
    source = text_window_service.source_text(ctx.book.id)
    next_cursor = None
    rows = ctx.db.query(Chapter).filter(Chapter.book_id==ctx.book.id,Chapter.kind=="main",Chapter.idx>=lo-1,Chapter.idx<hi).order_by(Chapter.idx).all()
    total_chars, consumed = 0, 0
    for row in rows:
        body = text_window_service.get_chapter_content(ctx.db,ctx.book.id,row.idx)
        a = max(body["char_start"],start) if start is not None else body["char_start"]
        b = min(row.char_end,end) if end is not None else row.char_end
        total_chars += max(0,b-a)
        if row.idx+1 < number:
            consumed += max(0,b-a)
        elif row.idx+1 == number and position is not None:
            consumed += max(0,min(position,b)-a)
    for num in range(number,hi+1):
        chapter = ctx.db.query(Chapter).filter(Chapter.book_id==ctx.book.id,Chapter.idx==num-1).first()
        if chapter is None or chapter.kind != "main":
            continue
        _chapter(ctx,num)
        content = text_window_service.get_chapter_content(ctx.db,ctx.book.id,num-1)
        begin = max(content["char_start"],position if num==number and position is not None else content["char_start"])
        finish = min(chapter.char_end,end) if end is not None else chapter.char_end
        if begin >= finish:
            continue
        take = min(budget,finish-begin)
        if 0 < take < finish-begin:
            boundary = source.rfind("\n",begin+take//2,begin+take)
            if boundary >= 0:
                take = boundary+1-begin
        if take:
            segment = {"chapter_number":num,"chapter_title":chapter.title,"text":source[begin:begin+take],
                "segment_index":segment_index,"total_chars":chapter.char_end-content["char_start"],
                "start_offset":begin-content["char_start"],"end_offset":begin+take-content["char_start"]}
            if passage:
                hit_start,hit_end = ref.get("hit_start",ref["start"]),ref.get("hit_end",ref["end"])
                segment["hit_offset"] = hit_start-begin if begin <= hit_start < begin+take else None
                segment["hit_length"] = hit_end-hit_start
            segments.append(segment)
            segment_index += 1
            budget -= take
        if begin+take < finish:
            next_cursor = _token(ctx,"read",{"sig":signature,"chapter":num,"position":begin+take,"segment_index":segment_index})
            break
    returned = sum(len(s["text"]) for s in segments)
    result = {"segments":segments,"has_more":next_cursor is not None,"cursor":next_cursor,
        "total_chars":total_chars,"returned_chars":returned,"consumed_chars":consumed+returned,
        "remaining_chars":max(0,total_chars-consumed-returned)}
    if passage:
        # This cursor is accepted by read_chapter with its default 2400-char page.
        last = segments[-1] if segments else None
        chapter_position = content["char_start"] + last["end_offset"] if last else start
        chapter_sig = hashlib.sha256(json.dumps({"lo":lo,"hi":hi,"ref":None,"start":None,"end":None,"max_chars":2400},sort_keys=True).encode()).hexdigest()
        result["chapter_cursor"] = _token(ctx,"read",{"sig":chapter_sig,"chapter":lo,"position":chapter_position,"segment_index":segment_index}) if chapter_position < chapter.char_end else None
    return result


def _exact(ctx,args):
    query = str(args.get("query") or "").strip()
    if not query or len(query)>200:
        raise legacy.ToolError("invalid_arguments","query 长度须为1–200字")
    lo,hi = args.get("chapter_from",1),args.get("chapter_to",ctx.safe_chapter+1)
    _integer(lo,"chapter_from",1,ctx.safe_chapter+1)
    _integer(hi,"chapter_to",lo,ctx.safe_chapter+1)
    limit = _integer(args.get("limit") or 5,"limit",1,8)
    source = text_window_service.source_text(ctx.book.id)
    rows = ctx.db.query(Chapter).filter(Chapter.book_id==ctx.book.id,Chapter.kind=="main",Chapter.idx>=lo-1,Chapter.idx<hi).order_by(Chapter.idx).all()
    items=[]
    for row in rows:
        content = text_window_service.get_chapter_content(ctx.db,ctx.book.id,row.idx)
        cursor=content["char_start"]
        for _ in range(2):
            pos=source.find(query,cursor,row.char_end)
            if pos<0:
                break
            a,b=max(content["char_start"],pos-40),min(row.char_end,pos+len(query)+40)
            items.append({"chapter_number":row.idx+1,"chapter_title":row.title,"snippet":source[a:b],
                "matched_by":"exact","matched_query":query,"hit_offset":pos-a,"hit_length":len(query),
                "passage_ref":_token(ctx,"passage",{"chapter":row.idx+1,"start":max(content["char_start"],pos-600),"end":min(row.char_end,pos+len(query)+600),"hit_start":pos,"hit_end":pos+len(query)})})
            cursor=pos+len(query)
            if len(items)>limit:
                return {"items":items[:limit],"has_more":True,"mode":"exact","refine_hint":"请缩小章节范围或细化 query"}
    return {"items":items,"has_more":False,"mode":"exact"}


def _search(ctx, args, *, allow_alias=True):
    requested = args.get("mode") or "hybrid"
    if not isinstance(requested,str) or requested not in {"auto","hybrid","exact"}:
        raise legacy.ToolError("invalid_arguments","mode 必须是 auto、hybrid 或 exact")
    query = str(args.get("query") or "").strip()
    if not query or len(query)>200:
        raise legacy.ToolError("invalid_arguments","query 长度须为1–200字")
    lo,hi = args.get("chapter_from",1),args.get("chapter_to",ctx.safe_chapter+1)
    _integer(lo,"chapter_from",1,ctx.safe_chapter+1)
    _integer(hi,"chapter_to",lo,ctx.safe_chapter+1)
    if args.get("limit") is not None:
        _integer(args["limit"],"limit",1,8)
    ready = bool(ctx.capabilities.text_index) if ctx.capabilities is not None else legacy._has_text_index(ctx)
    retrieval = {"requested_mode":requested,"index_status":"ready" if ready else "unavailable","steps":[]}
    def finish(data):
        data["retrieval"] = retrieval
        if not data.get("items"):
            data["next_action"] = "可缩小章节范围，使用正文原词或已知人物称呼重试。" if ready else "当前未进行语义检索；建立正文切块索引后可检索同义改写，现可使用正文原词或按章阅读。"
        return {"ok":True,"data":data}
    exact = None
    if requested in {"exact","auto"} or not ready:
        exact = _exact(ctx,args)
        retrieval["steps"].append("exact")
        if requested == "exact" or (requested == "auto" and exact["items"]):
            return finish(exact)
    if requested == "auto" and not allow_alias:
        retrieval["alias_status"] = "index_updating"
    if requested == "auto" and allow_alias:
        # Expand only unambiguous, observed character aliases; never invent synonyms.
        candidates,_ = legacy._alias_candidates(ctx,query,ctx.safe_chapter)
        if len(candidates) == 1:
            from app.models.character_alias_observation import CharacterAliasObservation
            from app.models.character import Character
            actor = ctx.db.get(Character,candidates[0]["character_id"])
            aliases = [name for (name,) in ctx.db.query(CharacterAliasObservation.alias).filter(
                CharacterAliasObservation.book_id==ctx.book.id,
                CharacterAliasObservation.character_id==actor.id,
                CharacterAliasObservation.chapter_idx<=ctx.safe_chapter).distinct().all()]
            if actor.first_seen_chapter is not None and actor.first_seen_chapter<=ctx.safe_chapter:
                aliases.insert(0,actor.canonical_name)
            for alias in list(dict.fromkeys(aliases))[:8]:
                if alias == query:continue
                expanded = _exact(ctx,{**args,"query":alias})
                if expanded["items"]:
                    for item in expanded["items"]:item["matched_by"]="alias"
                    retrieval["steps"].append("alias")
                    retrieval["expanded_query"] = alias
                    return finish(expanded)
    if not ready:
        retrieval["fallback_reason"] = "text_index_unavailable"
        retrieval["fallback_message"] = "正文向量索引未就绪，本次仅进行了精确匹配。"
        return finish(exact or _exact(ctx,args))
    low_args = {**args,"mode":"hybrid","chapter_from":lo-1,"chapter_to":hi-1}
    result = legacy.execute("search_text",low_args,ctx)
    retrieval["steps"].append("hybrid")
    if not result.get("ok"):
        code = result.get("error")
        if code in {"invalid_arguments","chapter_out_of_range"}:return result
        retrieval.update(index_status="unavailable",fallback_reason=code or "semantic_search_failed",
            fallback_message="正文混合检索失败，本次仅进行了精确匹配；请检查正文索引和本地模型。")
        return finish(exact or _exact(ctx,args))
    data = result["data"]
    if data.get("mode") != "hybrid":
        retrieval.update(index_status="unavailable",fallback_reason="text_index_unavailable")
        return finish(exact or _exact(ctx,args))
    for hit in data.get("items",[]):
        chapter = _chapter(ctx,hit["chapter_idx"]+1)
        hit.update(matched_by="semantic",hit_offset=max(0,hit["hit_char_start"]-hit["char_start"]),
            hit_length=hit["hit_char_end"]-hit["hit_char_start"])
        hit["passage_ref"] = _token(ctx,"passage",{"chapter":chapter.idx+1,
            "start":max(chapter.char_start,hit["char_start"]),"end":min(chapter.char_end,hit["char_end"]),
            "hit_start":hit["hit_char_start"],"hit_end":hit["hit_char_end"]})
    return finish(public(data))


def execute(tool,args,ctx):
    args = dict(args or {})
    try:
        from app.services import correction_service as fixes
        if tool == "report_correction":
            return fixes.report(ctx.db,ctx.book.id,CorrectionReport.model_validate(args),safe_override=ctx.safe_chapter+1)
        affected = fixes.blocked_layers(ctx.db,ctx.book.id)
        if ((tool=="search_summaries" and "summary" in affected) or
            (tool in {"get_character","get_character_timeline","search_character_changes","resolve_alias","list_alias_candidates"} and "character" in affected) or
            (tool=="search_lore" and "lore" in affected)):
            raise legacy.ToolError("index_updating","来源已修正，对应实体层/索引待重建；请读原文或等待审核页重建完成。")
        if any(k in args for k in ("chapter_idx","offset","char_start","char_end","from_chapter","to_chapter")):
            raise legacy.ToolError("invalid_arguments","工具使用从1开始的章节号与 cursor，不接受内部索引或 offset")
        if tool in {"note_query","note_upsert","list_storylines"}:
            if tool=="note_upsert":
                from app.services.reading_note_service import upsert as submit
                return submit(ctx.db,ctx.book.id,TypeAdapter(NoteWrite).validate_python(args),safe_override=ctx.safe_chapter+1)
            cls = StorylineList if tool=="list_storylines" else NoteQuery
            directory = tool=="note_query" and args.get("view")=="directory"
            if args.get("as_of_chapter") is None and not directory:
                args["as_of_chapter"] = ctx.safe_chapter + 1
            if args.get("as_of_chapter") is not None and args["as_of_chapter"] > ctx.safe_chapter+1:
                raise legacy.ToolError("chapter_out_of_range","不能超过当前安全章")
            return reading_note_service.query(ctx.db,ctx.book.id,cls.model_validate(args),listing=tool=="list_storylines",safe_override=ctx.safe_chapter+1)
        if tool=="find_note_duplicates":
            from app.services import note_navigation_service
            return note_navigation_service.duplicates(ctx.db,ctx.book.id,NoteDuplicateQuery.model_validate(args),ctx.safe_chapter+1)
        if tool=="refresh_storyline_evidence":
            from app.services import storyline_management_service
            return storyline_management_service.refresh_evidence(ctx.db,ctx.book.id,
                StorylineEvidenceRefresh.model_validate(args),ctx.safe_chapter+1)
        if tool in {"delete_storyline","restore_storyline"}:
            from app.services import storyline_management_service
            action = StorylineAction.model_validate(args)
            fn = reading_note_service.delete_storyline if tool=="delete_storyline" else storyline_management_service.restore
            return fn(ctx.db,ctx.book.id,action.note_id,action.expected_version,safe_override=ctx.safe_chapter+1)
        if tool=="list_deleted_storylines":
            from app.services import storyline_management_service
            from app.models.reading_note import ReadingNoteRevision
            limit = _integer(args.get("limit",10),"limit",1,20)
            safe = reading_note_service.gate(ctx.db,ctx.book.id,ctx.safe_chapter+1)
            newest = ctx.db.query(ReadingNoteRevision.id).filter(ReadingNoteRevision.book_id==ctx.book.id).order_by(ReadingNoteRevision.id.desc()).first()
            watermark = newest[0] if newest else 0
            offset = 0
            if args.get("cursor"):
                token = _decode(ctx,args["cursor"],"deleted_storylines")
                if token["safe"]!=safe or token["watermark"]!=watermark or token["limit"]!=limit:
                    raise legacy.ToolError("invalid_cursor","阅读进度或笔记已变化，请重新查询已删除列表")
                offset = token["offset"]
            data = storyline_management_service.deleted(ctx.db,ctx.book.id,offset,limit,safe_override=safe)["data"]
            after = data.pop("next_offset")
            data.update(has_more=after is not None,cursor=_token(ctx,"deleted_storylines",{"safe":safe,"watermark":watermark,"limit":limit,"offset":after}) if after is not None else None)
            return {"ok":True,"data":data}
        if tool in {"read_chapter","read_chapters","read_passage"}:
            return {"ok":True,"data":_read(ctx,args,passage=tool=="read_passage",single=tool=="read_chapter")}
        if tool=="search_text":
            return _search(ctx,args,allow_alias="character" not in affected)
        if tool=="get_toc":
            lo,hi=args.get("chapter_from",1),args.get("chapter_to") or ctx.safe_chapter+1
            _integer(lo,"chapter_from",1,ctx.safe_chapter+1);_integer(hi,"chapter_to",lo,ctx.safe_chapter+1)
            limit=_integer(args.get("limit",20),"limit",1,100)
            signature = json.dumps([lo,hi,limit])
            after=lo
            if args.get("cursor"):
                token=_decode(ctx,args["cursor"],"toc")
                if token["sig"]!=signature: raise legacy.ToolError("invalid_cursor","目录范围已变化")
                after=token["after"]
            rows=ctx.db.query(Chapter).filter(Chapter.book_id==ctx.book.id,Chapter.kind=="main",Chapter.idx>=after-1,Chapter.idx<hi).order_by(Chapter.idx).limit(limit+1).all()
            return {"ok":True,"data":{"items":[{"chapter_number":r.idx+1,"chapter_title":r.title} for r in rows[:limit]],
                "has_more":len(rows)>limit,"cursor":_token(ctx,"toc",{"sig":signature,"after":rows[limit].idx+1}) if len(rows)>limit else None}}
        low_args=dict(args)
        if tool=="get_chapter_summary":
            low_args["chapter_idx"]=_chapter(ctx,args.get("chapter_number")).idx
        for key in ("as_of_chapter","chapter_from","chapter_to"):
            if low_args.get(key) is not None:
                _chapter(ctx,low_args[key]);low_args[key]-=1
        if tool=="get_character_timeline":
            if "chapter_from" in low_args:low_args["from_chapter"]=low_args.pop("chapter_from")
            if "chapter_to" in low_args:low_args["to_chapter"]=low_args.pop("chapter_to")
        if tool=="read_around":
            _chapter(ctx,ctx.chapter.idx+1)
        if tool=="list_alias_candidates" and args.get("cursor"):
            token=_decode(ctx,args["cursor"],"aliases")
            if token["alias"]!=args.get("alias") or token["as_of"]!=low_args.get("as_of_chapter",ctx.safe_chapter):
                raise legacy.ToolError("invalid_cursor","称呼或查询时点已变化")
            low_args["offset"]=token["offset"]
        result=legacy.execute(tool,low_args,ctx)
        if not result.get("ok"):
            # Never expose legacy 0-based prose or ranges in model errors.
            data=public(result.get("data") or {})
            data["message"]="查询失败："+str(result.get("error"))+f"；章节从1开始，当前最多第{ctx.safe_chapter+1}章。"
            return {"ok":False,"error":result.get("error"),"data":data}
        data=result["data"]
        if tool=="list_alias_candidates":
            offset=low_args.get("offset",0)+len(data.get("items",[]))
            if offset<int(data.get("total",0)):
                data["cursor"]=_token(ctx,"aliases",{"alias":args.get("alias"),"as_of":low_args.get("as_of_chapter",ctx.safe_chapter),"offset":offset})
        _attach_sources(tool,data,ctx,args)
        output=public(data)
        if tool=="get_character":
            related=reading_note_service.query(ctx.db,ctx.book.id,NoteQuery(entities=[args["name"]],as_of_chapter=args.get("as_of_chapter") or ctx.safe_chapter+1,limit=2))
            output["related_notes"]=related["data"]["items"]
        return {"ok":True,"data":output}
    except ValidationError as exc:
        return validation_error(exc,tool)
    except (legacy.ToolError,NoteError) as exc:
        return {"ok":False,"error":exc.code,"data":exc.data}
    except Exception:
        return {"ok":False,"error":"tool_error","data":{"message":"工具执行失败，请检查参数或重试"}}
