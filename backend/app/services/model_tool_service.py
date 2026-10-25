"""The single model-facing adapter. Database/API indexes stay 0-based internally."""
import hashlib
import json
from pathlib import Path
from pydantic import TypeAdapter, ValidationError
from app.config import UPLOADS_DIR
from app.models.chapter import Chapter
from app.services import agent_tool_service as legacy, text_window_service, tool_token_service
from app.services import reading_note_service
from app.services.note_service import NoteError
from app.schemas.reading_note import NoteWrite, NoteQuery, StorylineList
from app.schemas.correction import CorrectionReport

from app.common.model_contract import public



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
        return f"已保存笔记版本 {data.get('version')}"
    return f"返回 {len(data.get('items') or [])} 条结果"




EXTRA_SPECS = [
    {"name":"read_chapters","description":"按目录章节区间批量读正文；每次最多 6000 字，原样传回 cursor 续读，不计算偏移。",
     "args":{"chapter_from":"integer >=1","chapter_to":"integer >=1，含两端","cursor":"string，可选"}},
    {"name":"read_passage","description":"精读搜索返回的 passage_ref；长片段原样传回 cursor 续读。",
     "args":{"passage_ref":"string，原样传回","cursor":"string，可选"}},
    {"name":"note_query","description":"回答前查相关笔记：query 可直接用用户问题，通过人物关联、关键词与后台已就绪的向量混合检索，不必与笔记原句一致。至少传 query、entities、note_id 或章节范围，默认5条。向量未就绪会退回人物/关键词；未命中可看 storyline_candidates 或用 entities 找剧情线。默认按章事件和局部图；按 note_id/node_id 展开分支，view=entry 查证据，view=edges 查连接。无笔记不代表未读。",
     "args":NoteQuery.model_json_schema()},
    {"name":"list_storylines","description":"浏览已整理的剧情线和人物关系名称、阶段、概述；默认5条，可按 query/entities 筛选。需要详细节点再用 note_query(note_id)。",
     "args":StorylineList.model_json_schema()},
    {"name":"note_upsert","description":"最终回答前评估是否值得保存：新发现、关键转折、关系变化、消歧或纠错才写；允许不写。entry 保存单一结论与逐章原文；storyline 用稳定 node_id 与 edges 表达分叉汇合，引用 entry 固定版本。局部修订优先 kind=storyline_patch 并带 note_id/expected_version/change_reason，服务端保留未改动分支。解释需 perspective。不可依据摘要冒充已读原文。",
     "args":TypeAdapter(NoteWrite).json_schema()},
    {"name":"report_correction","description":"发现摘要、人物增量、别名或世界观有错时，提交 source_ref、原因、原文证据和可选字段修改，等待用户在纠错审核页确认。不能用后文证据回写早期来源。笔记错误直接 note_upsert 修订，并核对 affected_storylines。",
     "args":CorrectionReport.model_json_schema()},
]


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
            item["description"] = "读指定章正文，chapter_number 从1开始；每次1200字，原样传回 cursor 续读。"
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
    return tool_token_service.encode({"kind":kind,"book":ctx.book.id,"stamp":_source_stamp(ctx),**data})


def _decode(ctx, token, kind):
    try:
        data = tool_token_service.decode(token)
    except ValueError as exc:
        raise legacy.ToolError("invalid_cursor",str(exc))
    if data.get("kind") != kind or data.get("book") != ctx.book.id or data.get("stamp") != _source_stamp(ctx):
        raise legacy.ToolError("invalid_cursor","引用不属于当前书或原文已变化，请重新查询")
    return data


def _read(ctx, args, passage=False, single=False):
    cursor = args.get("cursor")
    if passage:
        ref = _decode(ctx,args.get("passage_ref", ""),"passage")
        lo = hi = ref["chapter"]
        chapter = _chapter(ctx,lo)
        start,end = ref["start"],ref["end"]
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
    signature = hashlib.sha256(json.dumps({"lo":lo,"hi":hi,"ref":args.get("passage_ref")},sort_keys=True).encode()).hexdigest()
    number, position = lo, start
    if cursor:
        token = _decode(ctx,cursor,"read")
        if token["sig"] != signature:
            raise legacy.ToolError("invalid_cursor","cursor 与读取范围不匹配")
        number,position = token["chapter"],token["position"]
    segments, budget = [], 1200 if single else 6000
    source = text_window_service.source_text(ctx.book.id)
    next_cursor = None
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
        if take:
            segments.append({"chapter_number":num,"chapter_title":chapter.title,"text":source[begin:begin+take]})
            budget -= take
        if begin+take < finish:
            next_cursor = _token(ctx,"read",{"sig":signature,"chapter":num,"position":begin+take})
            break
    return {"segments":segments,"has_more":next_cursor is not None,"cursor":next_cursor}


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
                "passage_ref":_token(ctx,"passage",{"chapter":row.idx+1,"start":max(content["char_start"],pos-600),"end":min(row.char_end,pos+len(query)+600)})})
            cursor=pos+len(query)
            if len(items)>limit:
                return {"items":items[:limit],"has_more":True,"mode":"exact","refine_hint":"请缩小章节范围或细化 query"}
    return {"items":items,"has_more":False,"mode":"exact"}


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
                return reading_note_service.upsert(ctx.db,ctx.book.id,TypeAdapter(NoteWrite).validate_python(args),safe_override=ctx.safe_chapter+1)
            cls = StorylineList if tool=="list_storylines" else NoteQuery
            if args.get("as_of_chapter") is None:
                args["as_of_chapter"] = ctx.safe_chapter + 1
            if args["as_of_chapter"] > ctx.safe_chapter+1:
                raise legacy.ToolError("chapter_out_of_range","不能超过当前安全章")
            return reading_note_service.query(ctx.db,ctx.book.id,cls.model_validate(args),listing=tool=="list_storylines")
        if tool in {"read_chapter","read_chapters","read_passage"}:
            return {"ok":True,"data":_read(ctx,args,passage=tool=="read_passage",single=tool=="read_chapter")}
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
        if tool=="search_text" and (args.get("mode")=="exact" or (ctx.capabilities and not ctx.capabilities.text_index)):
            return {"ok":True,"data":_exact(ctx,args)}
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
        if tool=="search_text":
            lo,hi=args.get("chapter_from",1),args.get("chapter_to",ctx.safe_chapter+1)
            data["items"]=[h for h in data.get("items",[]) if lo<=h["chapter_idx"]+1<=hi]
            for hit in data["items"]:
                chapter=_chapter(ctx,hit["chapter_idx"]+1)
                hit["passage_ref"]=_token(ctx,"passage",{"chapter":chapter.idx+1,"start":max(chapter.char_start,hit["char_start"]),"end":min(chapter.char_end,hit["char_end"])})
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
        return {"ok":False,"error":"invalid_arguments","data":{"message":str(exc)}}
    except (legacy.ToolError,NoteError) as exc:
        return {"ok":False,"error":exc.code,"data":exc.data}
    except Exception:
        return {"ok":False,"error":"tool_error","data":{"message":"工具执行失败，请检查参数或重试"}}
