"""Reviewable source patches with optimistic hashes, atomic application and rebuild state."""
import copy
import hashlib
import json
import threading
import uuid
from datetime import datetime
from sqlalchemy import or_
from app.database import SessionLocal
from app.models.note import Issue
from app.models.chapter_summary import ChapterSummary
from app.models.chapter_extraction import ChapterExtraction
from app.models.character import Character
from app.models.character_chapter_delta import CharacterChapterDelta
from app.models.character_card_override import CharacterCardOverride
from app.models.character_alias_observation import CharacterAliasObservation
from app.models.lore_fact import LoreFact
from app.models.lore_override import LoreOverride
from app.models.entity_index_flag import EntityIndexFlag
from app.services import reading_note_service as memory, tool_token_service
from app.services.note_service import NoteError

FIELDS = {
    "summary": {"summary", "key_events", "characters", "cliffhanger"},
    "character_delta": {"identity", "traits_add", "relations_add", "known_facts_add", "voice_sample", "arc"},
    "character_override": {"identity", "traits", "relations", "known_facts", "voice_sample", "arc"},
    "lore": {"category", "aliases", "summary", "details", "entities"},
    "alias": {"remove"},
}
MODELS = {"summary":ChapterSummary,"character_delta":CharacterChapterDelta,
          "character_override":CharacterCardOverride,"lore":LoreFact,"alias":CharacterAliasObservation}
_locks_guard = threading.Lock()
_locks = {}


def _lock(book_id):
    with _locks_guard:
        return _locks.setdefault(book_id, threading.RLock())


def _hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _snapshot(db, book_id, kind, row_id, safe):
    if kind not in MODELS:
        raise NoteError("invalid_source", "不支持的纠错来源")
    model = MODELS[kind]
    row = db.query(model).filter(model.book_id==book_id,model.id==row_id).first()
    if row is None:
        raise NoteError("source_changed", "来源已变化或不存在，请重新召回")
    idx = row.from_chapter if kind=="character_override" else row.chapter_idx
    memory._chapter(db,book_id,idx+1,safe)
    if kind=="alias":
        before = {"alias":row.alias,"remove":False}
    else:
        before = {key:copy.deepcopy(getattr(row,key)) for key in sorted(FIELDS[kind])}
    # Include origin identity privately in the fingerprint so rebuilt IDs cannot bind a different source.
    origin = {key:getattr(row,key) for key in ("character_id","character_name","canonical_name") if hasattr(row,key)}
    if kind=="lore":
        override=db.query(LoreOverride).filter(LoreOverride.book_id==book_id,LoreOverride.canonical_name==row.canonical_name,LoreOverride.chapter_idx==idx).first()
        if override:
            before={key:copy.deepcopy(getattr(override,key)) for key in sorted(FIELDS[kind])}
            origin["override_id"]=override.id
    if kind in {"character_delta", "lore", "alias"}:
        extraction = db.query(ChapterExtraction).filter(ChapterExtraction.book_id==book_id,ChapterExtraction.chapter_idx==idx).first()
        origin["extraction_hash"] = _hash(extraction.parsed) if extraction else None
    fingerprint = _hash({"chapter":idx,"before":before,"origin":origin})
    return row,idx,before,fingerprint


def source_ref(db, book_id, kind, row_id, safe):
    _,idx,_,fingerprint = _snapshot(db,book_id,kind,row_id,safe)
    return tool_token_service.encode({"kind":"correction_source","book":book_id,"type":kind,
        "id":row_id,"chapter":idx+1,"hash":fingerprint})


def report(db, book_id, request, safe_override=None):
    safe = memory.gate(db,book_id,safe_override)
    try:
        target = tool_token_service.decode(request.source_ref)
    except ValueError as exc:
        raise NoteError("invalid_source", str(exc))
    if target.get("kind")!="correction_source" or target.get("book")!=book_id:
        raise NoteError("invalid_source", "纠错来源不属于当前书籍")
    _,idx,before,fingerprint = _snapshot(db,book_id,target["type"],target["id"],safe)
    if fingerprint!=target["hash"]:
        raise NoteError("source_changed", "来源已变化，请重新召回后报告")
    evidence = memory.validate_evidence(db,book_id,request.evidence,safe)
    if any(item["chapter_number"] > idx+1 for item in evidence):
        raise NoteError("later_knowledge", "后文证据不能用于回写早期来源；请保存为当前时点的解释笔记")
    replacement = request.replacement
    if replacement is not None:
        validate_patch(target["type"],before,replacement)
    layers = ["summary"] if target["type"]=="summary" else ([] if target["type"]=="character_override" else ["character","lore"])
    payload = {"protocol":1,"target":target,"before":before,"replacement":replacement,"evidence":evidence,
        "safe_chapter":safe,"source_chapter":idx+1,"rebuild_layers":layers,"index_state":"not_applied"}
    # Duplicate reports from multiple windows collapse only within the same visible scope.
    for previous in db.query(Issue).filter(Issue.book_id==book_id,Issue.fix_status=="pending",Issue.chapter_idx<safe).all():
        data=previous.proposed_fix_json or {}
        if data.get("target")==target and data.get("replacement")==replacement:
            return {"ok":True,"data":{"issue_id":previous.issue_id,"status":"pending"},"deduplicated":True}
    issue=Issue(issue_id="fix_"+uuid.uuid4().hex,book_id=book_id,target_type=target["type"],target_id=str(target["id"]),
        issue_type="source_correction",note=request.reason,evidence_quote=evidence[0]["quote"],chapter_idx=safe-1,
        proposed_fix_json=payload,rebuild_scope="summary" if layers==["summary"] else ("all" if layers else "none"),
        fix_status="pending",status="open")
    db.add(issue);db.commit()
    return {"ok":True,"data":{"issue_id":issue.issue_id,"status":"pending",
        "next_action":"已提交到书籍详情的纠错审核；等待用户确认，不要声称基础数据已改正。"}}


def validate_patch(kind, before, patch):
    if not isinstance(patch,dict) or not patch or set(patch)-FIELDS[kind]:
        raise NoteError("invalid_patch", "只能修改来源允许的字段", {"allowed_fields":sorted(FIELDS[kind])})
    if len(json.dumps(patch,ensure_ascii=False))>12000:
        raise NoteError("invalid_patch", "修改内容过大，请拆分报告")
    if kind=="alias":
        if patch!={"remove":True}:
            raise NoteError("invalid_patch", "称谓纠错目前只支持移除该章错误归属；正确归属可另报")
        return
    for key,value in patch.items():
        expected_list = key in {"key_events","characters","traits_add","relations_add","known_facts_add",
            "traits","relations","known_facts","aliases","details","entities"}
        if expected_list:
            if not isinstance(value,list) or len(value)>30 or any(not isinstance(v,str) or len(v)>500 for v in value):
                raise NoteError("invalid_patch", f"{key} 必须为至多30条短文本的列表")
        elif not isinstance(value,str) or len(value)>1000:
            raise NoteError("invalid_patch", f"{key} 必须为至多1000字的文本")
    if kind=="summary":
        from app.services.chapter_extract_prompt import validate_edit_fields, ExtractionValidationError
        try:
            validate_edit_fields({**before,**patch})
        except ExtractionValidationError as exc:
            raise NoteError("invalid_patch",str(exc))
    if kind=="lore" and "category" in patch and patch["category"] not in {"location","organization","item","technique","world_rule"}:
        raise NoteError("invalid_patch", "世界观类型无效")


def _issue(db,book_id,issue_id):
    safe=memory.gate(db,book_id)
    row=db.query(Issue).filter(Issue.book_id==book_id,Issue.issue_id==issue_id,Issue.chapter_idx<safe).first()
    if row is None or (row.proposed_fix_json or {}).get("protocol")!=1:
        raise NoteError("not_found", "当前安全范围内没有该报告")
    return row


def list_reports(db,book_id,status=None,page=1,limit=20):
    safe=memory.gate(db,book_id)
    query=db.query(Issue).filter(Issue.book_id==book_id,Issue.chapter_idx<safe,Issue.issue_type=="source_correction")
    if status=="open":query=query.filter(or_(Issue.fix_status=="pending",Issue.status=="open"))
    elif status:query=query.filter(Issue.fix_status==status)
    total=query.count()
    items=[]
    for row in query.order_by(Issue.id.desc()).offset((page-1)*limit).limit(limit):
        data=row.proposed_fix_json or {}
        if data.get("protocol")!=1:continue
        items.append({"issue_id":row.issue_id,"reason":row.note,"target_type":row.target_type,
            "source_chapter":data["source_chapter"],"known_by_chapter":data["safe_chapter"],
            "status":row.fix_status,"before":data["before"],"replacement":data["replacement"],
            "evidence":data["evidence"],"allowed_fields":sorted(FIELDS[row.target_type]),
            "rebuild_layers":data["rebuild_layers"],"index_state":data["index_state"],"error":data.get("error"),
            "downstream_notice":"修改可能影响后续抽取，系统不会自动重新调用模型抽取后文。"})
    return {"items":items,"safe_chapter":safe,"total":total,"page":page,"limit":limit}


def blocked_layers(db,book_id):
    layers=set()
    for row in db.query(Issue).filter(Issue.book_id==book_id,Issue.fix_status=="applied").all():
        data=row.proposed_fix_json or {}
        if data.get("protocol")==1 and data.get("index_state") not in {"done","not_required"}:
            layers.update(data.get("rebuild_layers",[]))
    return layers


def _dirty(db,book_id,layer):
    row=db.query(EntityIndexFlag).filter(EntityIndexFlag.book_id==book_id,EntityIndexFlag.layer==layer).first()
    if row is None:
        row=EntityIndexFlag(book_id=book_id,layer=layer,dirty=True);db.add(row)
    else:row.dirty=True


def _apply_source(db,book_id,kind,row,idx,patch):
    if kind=="summary":
        for key,value in patch.items():setattr(row,key,value)
        row.edited=True
        extraction=db.query(ChapterExtraction).filter(ChapterExtraction.book_id==book_id,ChapterExtraction.chapter_idx==idx).first()
        if extraction and extraction.parsed:
            parsed=copy.deepcopy(extraction.parsed)
            parsed.setdefault("summary",{}).update(patch)
            extraction.parsed=parsed
        return
    if kind=="character_override":
        for key,value in patch.items():setattr(row,key,value)
        return
    extraction=db.query(ChapterExtraction).filter(ChapterExtraction.book_id==book_id,ChapterExtraction.chapter_idx==idx,ChapterExtraction.status=="success").first()
    if extraction is None:
        raise NoteError("source_changed", "对应原始抽取不存在，不能只修改会被重建覆盖的派生记录")
    parsed=copy.deepcopy(extraction.parsed or {})
    from app.services import entity_layer_service
    if kind=="character_delta":
        character=db.get(Character,row.character_id)
        resolve=entity_layer_service._chapter_alias_map(parsed.get("characters",{}))
        candidates=[item for item in parsed.get("characters",{}).get("card_deltas",[]) if resolve(item.get("character"))==character.canonical_name]
        if len(candidates)!=1:
            raise NoteError("source_ambiguous", "无法唯一找到原始人物增量，请在摘要编辑页核对")
        mapping={"traits_add":"traits","relations_add":"relations","known_facts_add":"known_facts"}
        for key,value in patch.items():candidates[0][mapping.get(key,key)]=value
    elif kind=="lore":
        candidates=[item for item in parsed.get("lore",[]) if item.get("name")==row.canonical_name or row.canonical_name in item.get("aliases",[])]
        if len(candidates)!=1:
            raise NoteError("source_ambiguous", "无法唯一找到原始世界观条目，请在摘要编辑页核对")
        candidates[0].update(patch)
        override=db.query(LoreOverride).filter(LoreOverride.book_id==book_id,LoreOverride.canonical_name==row.canonical_name,LoreOverride.chapter_idx==idx).first()
        if override:
            for key,value in patch.items():setattr(override,key,value)
    elif kind=="alias":
        character=db.get(Character,row.character_id)
        chars=parsed.get("characters",{})
        removed=False
        pairs=chars.get("new_aliases",[])
        kept=[p for p in pairs if not (p.get("alias")==row.alias and p.get("canonical")==character.canonical_name)]
        removed = len(kept)!=len(pairs)
        chars["new_aliases"]=kept
        resolve=entity_layer_service._chapter_alias_map(chars)
        for mention in chars.get("mentions",[]):
            if resolve(mention.get("name"))==character.canonical_name and row.alias in mention.get("aliases",[]):
                mention["aliases"]=[a for a in mention["aliases"] if a!=row.alias];removed=True
        if not removed:raise NoteError("source_ambiguous", "无法定位该章称谓的原始抽取，请手工核对")
    extraction.parsed=parsed


def decide(db,book_id,issue_id,decision=None,reject=False):
    with _lock(book_id):
        issue=_issue(db,book_id,issue_id)
        if issue.fix_status!="pending":
            raise NoteError("already_decided", "报告已处理，不能重复应用")
        data=copy.deepcopy(issue.proposed_fix_json)
        if reject:
            issue.fix_status="rejected";issue.status="closed";db.commit()
            return {"issue_id":issue_id,"status":"rejected"}
        safe=memory.gate(db,book_id)
        target=data["target"]
        row,idx,before,fingerprint=_snapshot(db,book_id,target["type"],target["id"],safe)
        if fingerprint!=target["hash"]:
            raise NoteError("source_changed", "来源已被其他窗口修改，请重新召回并报告")
        patch=decision.replacement if decision.replacement is not None else data["replacement"]
        validate_patch(target["type"],before,patch)
        try:
            _apply_source(db,book_id,target["type"],row,idx,patch)
            for layer in data["rebuild_layers"]:_dirty(db,book_id,layer)
            data.update(replacement=patch,index_state="pending" if data["rebuild_layers"] else "not_required",error=None)
            issue.proposed_fix_json=data;issue.fix_status="applied";issue.applied_at=datetime.now().isoformat();issue.status="closed" if not data["rebuild_layers"] else "open"
            db.commit()
        except Exception:
            db.rollback();raise
        return {"issue_id":issue_id,"status":"applied","index_state":data["index_state"],"rebuild_layers":data["rebuild_layers"]}


def rebuild_issue(book_id,issue_id):
    with _lock(book_id),SessionLocal() as db:
        issue=db.query(Issue).filter(Issue.book_id==book_id,Issue.issue_id==issue_id).first()
        if issue is None or issue.fix_status!="applied":return
        data=copy.deepcopy(issue.proposed_fix_json)
        if data["index_state"] in {"done","not_required"}:return
        data.update(index_state="rebuilding",error=None);issue.proposed_fix_json=data;db.commit()
        try:
            from app.services import summary_index_service, entity_layer_service, entity_index_service
            if "summary" in data["rebuild_layers"]:
                summary_index_service.build_summary_index(db,book_id,allow_partial=True)
                flag=db.query(EntityIndexFlag).filter(EntityIndexFlag.book_id==book_id,EntityIndexFlag.layer=="summary").first()
                if flag:flag.dirty=False
            else:
                entity_layer_service.build_entity_layer(db,book_id)
                entity_index_service.build_entity_indexes(db,book_id,layer="all",allow_partial=True)
            issue=db.query(Issue).filter(Issue.issue_id==issue_id).one()
            data.update(index_state="done",error=None);issue.proposed_fix_json=data;issue.status="closed";db.commit()
        except Exception as exc:
            db.rollback()
            issue=db.query(Issue).filter(Issue.issue_id==issue_id).one()
            data.update(index_state="failed",error=str(getattr(exc,"message",exc))[:300]);issue.proposed_fix_json=data;db.commit()


def mark_interrupted(db):
    for issue in db.query(Issue).filter(Issue.fix_status=="applied").all():
        data=copy.deepcopy(issue.proposed_fix_json or {})
        if data.get("protocol")==1 and data.get("index_state")=="rebuilding":
            data.update(index_state="failed",error="服务重启中断重建，可重试；源修改已保存。")
            issue.proposed_fix_json=data
    db.commit()
