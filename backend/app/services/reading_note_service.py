"""Evidence and storyline revisions, all public chapters are 1-based.

Free-form conclusions are conservatively visible only from the writing position.
Older snapshots are immutable; a later rename or correction never rewrites history.
"""
import hashlib
import copy
import json
import re
import uuid
from sqlalchemy import func, and_
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from app.models.reading_note import ReadingNoteRevision as Revision
from app.models.chapter import Chapter
from app.schemas.reading_note import EntryWrite, NoteQuery, StorylineList, StorylinePatch, StorylineWrite
from app.services import reader_progress_service, text_window_service, tool_token_service
from app.services import note_graph_service as graph
from app.services import note_embedding_service as embeddings
from app.services import note_basis_service as basis_service
from app.services.note_service import NoteError, locate, _chapter_at

MAX_CHARS = 6000


def gate(db, book_id, as_of=None):
    progress = reader_progress_service.get(db, book_id)
    if not progress.get("available"):
        raise NoteError("no_reading_context", "请先在阅读器打开这本书")
    safe = int(progress["chapter_idx"]) + 1
    if as_of is not None:
        if not 1 <= as_of <= safe:
            raise NoteError("chapter_out_of_range", f"只允许第 1–{safe} 章", {"allowed_max": safe})
        safe = as_of
    return safe


def _chapter(db, book_id, number, safe):
    if not 1 <= number <= safe:
        raise NoteError("chapter_out_of_range", f"只允许第 1–{safe} 章", {"allowed_max": safe})
    row = db.query(Chapter).filter(Chapter.book_id == book_id, Chapter.idx == number - 1).first()
    if row is None or row.kind != "main":
        raise NoteError("chapter_unavailable", "章节不存在或为不参与伴读的番外")
    return row


def _names(payload):
    if payload["type"] == "event":
        return payload["participants"]
    if payload["type"] == "relation":
        return [payload["a"], payload["b"]]
    if payload["type"] == "address":
        return [payload["speaker"], payload["target"]]
    return [payload["subject"]]


def _check_names(db, book_id, names, safe, preferred_chapter=None):
    source = text_window_service.source_text(book_id)
    chapters = db.query(Chapter).filter(Chapter.book_id == book_id, Chapter.kind == "main", Chapter.idx < safe).all()
    visible = "\n".join(source[c.char_start:c.char_end] for c in chapters)
    for name in names:
        if name not in visible:
            from app.models.character import Character
            from app.models.character_alias_observation import CharacterAliasObservation
            from app.services import evidence_diagnostic_service as diagnostics
            known = {value for (value,) in db.query(Character.canonical_name).filter(
                Character.book_id==book_id,Character.first_seen_chapter<safe).all()}
            known.update(value for (value,) in db.query(CharacterAliasObservation.alias).filter(
                CharacterAliasObservation.book_id==book_id,CharacterAliasObservation.chapter_idx<safe).distinct().all())
            for row in _latest(db,book_id,safe).all():known.update(row.content.get("entities",[]))
            candidates = diagnostics.similar_names(name,known,chapters,source,preferred_chapter)
            raise NoteError("entity_not_observed", "实体名称未在安全范围正文出现；请核对已出现的称呼",
                {"name":name,"candidates":candidates,"candidate_method":"spelling_similarity",
                 "hint":"候选仅供核对，不自动替换；若没有候选，请读取发生章确认姓名。"})


def _current_storylines(db, book_id):
    latest = db.query(Revision.note_id, func.max(Revision.version).label("v")).filter(
        Revision.book_id == book_id, Revision.kind == "storyline").group_by(Revision.note_id).subquery()
    return db.query(Revision).join(latest, and_(Revision.note_id == latest.c.note_id,
        Revision.version == latest.c.v)).filter(Revision.book_id == book_id)


def _deleted_ids(db, book_id):
    return _current_storylines(db, book_id).with_entities(Revision.note_id).filter(
        Revision.content["deleted"].as_boolean().is_(True))


def _is_deleted(db, book_id, note_id):
    return _deleted_ids(db, book_id).filter(Revision.note_id == note_id).first() is not None


def delete_storyline(db, book_id, note_id, expected_version, *, commit=True, safe_override=None):
    safe = gate(db, book_id,safe_override)
    current = db.query(Revision).filter_by(book_id=book_id, note_id=note_id, kind="storyline").order_by(Revision.version.desc()).first()
    if current is None or current.knowledge_idx >= safe or current.content.get("deleted"):
        raise NoteError("not_found", "当前阅读范围内没有可删除的剧情线")
    if current.version != expected_version:
        raise NoteError("version_conflict", "剧情线已更新，请刷新后再删除")
    # A tombstone hides every historical view while retaining the event evidence.
    content = copy.deepcopy(current.content)
    content.update(deleted=True, status="retracted", change_reason="用户删除剧情线")
    db.add(Revision(book_id=book_id, note_id=note_id, version=current.version + 1,
                    kind="storyline", knowledge_idx=safe - 1, content=content))
    try:
        db.commit() if commit else db.flush()
    except IntegrityError:
        db.rollback()
        raise NoteError("version_conflict", "剧情线已更新，请刷新后再删除")
    return {"ok": True, "data": {"note_id": note_id, "version": current.version + 1, "deleted": True}}


def _latest(db, book_id, safe, watermark=None):
    scope = [Revision.book_id == book_id, Revision.knowledge_idx < safe]
    if watermark is not None:
        scope.append(Revision.id <= watermark)
    latest = db.query(Revision.note_id, func.max(Revision.version).label("v")).filter(*scope).group_by(Revision.note_id).subquery()
    return db.query(Revision).join(latest, and_(Revision.note_id == latest.c.note_id, Revision.version == latest.c.v)).filter(Revision.book_id == book_id, Revision.note_id.not_in(_deleted_ids(db, book_id)), func.coalesce(Revision.content["deleted"].as_boolean(), False).is_(False))


def _entry_scope_valid(content):
    chapter = content.get("chapter_number")
    return chapter is None or all(e["chapter_number"] == chapter for e in content.get("evidence", []))


def _dependency_state(row, latest):
    stale = []
    for node in graph.nodes(row.content):
        for ref in node["evidence"]:
            current = latest.get(ref["note_id"])
            if (current is None or current.version != ref["version"] or current.content.get("status", "active") != "active"
                    or not _entry_scope_valid(current.content)
                    or {e["chapter_number"] for e in current.content.get("evidence", [])} != {node["chapter_number"]}):
                stale.append(node["node_id"])
                break
    return sorted(set(stale))


def _public(row, detail=False, latest=None):
    data = row.content
    result = {"note_id": row.note_id, "version": row.version, "kind": row.kind,
              "known_by_chapter": row.knowledge_idx + 1, "recorded_at_chapter":row.knowledge_idx+1,"title": data["title"],
              "entities": data["entities"], "event_chapters": data["event_chapters"]}
    if row.kind == "entry":
        result.update({k: v for k, v in data.items() if k not in ("question", "event_chapters", "entities")})
        result["status"] = data.get("status", "active")
        result.update(basis_service.entry(data))
        if not _entry_scope_valid(data):
            result["needs_revision"] = True
            result["scope_error"] = "事件章与证据章不一致，请拆分为各章事件"
        result["chapter_label"] = "、".join(f"第{n}章" for n in data["event_chapters"])
        if latest and row.note_id in latest and latest[row.note_id].version > row.version:
            result["superseded_by_version"] = latest[row.note_id].version
    else:
        result.update(basis="unclassified",basis_label="未标注",perspective=None)
        result.update({k: data[k] for k in ("category", "overview", "stage", "open_questions")})
        stale = _dependency_state(row, latest) if latest is not None else []
        result["node_count"] = len(data["nodes"])
        result["needs_revision"] = bool(stale)
        result["stale_node_ids"] = stale[:8]
        result["stale_node_count"] = len(stale)
        if stale:
            result["overview"] = "关联证据已修订，请核对节点后更新本线。"
            result["stage"] = "待核对关联证据"
            result["open_questions"] = []
        if detail:
            result["nodes"] = [{**node,**basis_service.node(node,latest,node["node_id"] in stale)} for node in graph.nodes(data)]
            result["edges"] = data.get("edges", [])
    return result


def validate_evidence(db, book_id, evidence, safe):
    source = text_window_service.source_text(book_id)
    result = []
    errors = []
    from app.services.note_service import _normalize_with_map
    for index,item in enumerate(evidence):
        chapter = _chapter(db, book_id, item.chapter_number, safe)
        raw = source[chapter.char_start:chapter.char_end]
        match = locate(raw, item.quote)
        if match is None:
            from app.services.evidence_diagnostic_service import closest_quote
            body = text_window_service.get_chapter_content(db,book_id,chapter.idx)["text"]
            errors.append({"error":"quote_not_found","field":f"evidence.{index}.quote","evidence_index":index,
                "chapter_number":item.chapter_number,"quote":item.quote,"closest_passage":closest_quote(body,item.quote),
                "hint":"近似片段仅供重新摘录，不代表校验通过；请复制实际原文后重试。"})
            continue
        start, end = match
        normalized, _ = _normalize_with_map(raw)
        normalized_quote, _ = _normalize_with_map(item.quote)
        if normalized.find(normalized_quote, normalized.find(normalized_quote) + 1) >= 0:
            errors.append({"error":"quote_ambiguous","field":f"evidence.{index}.quote","evidence_index":index,
                "chapter_number":item.chapter_number,"quote":item.quote,"hint":"引文重复，请增加上下文使其在本章唯一。"})
            continue
        _chapter_at(db, book_id, source, chapter.char_start + start, chapter.char_start + end)
        result.append({"chapter_number": item.chapter_number,
                       "quote": source[chapter.char_start + start:chapter.char_start + end]})
    if errors:
        raise NoteError(errors[0]["error"],f"{len(errors)}条引文校验失败，本次未保存；请按errors中的位置修正",{"errors":errors})
    return result



def _comparable(content):
    value = copy.deepcopy(content)
    value.pop("question", None)
    value.pop("change_reason", None)
    if value.get("kind") == "entry":
        value.setdefault("status", "active")
    else:
        value["nodes"] = graph.nodes(value)
        value.setdefault("edges", [])
    return value


def upsert(db, book_id, note, safe_override=None, *, preview=False, commit=True):
    safe = gate(db, book_id, safe_override)
    if note.note_id and _is_deleted(db, book_id, note.note_id):
        raise NoteError("not_found", "该剧情线已删除，请重新创建")
    preserved_stale = set()
    if isinstance(note,StorylinePatch):
        original=db.query(Revision).filter(Revision.book_id==book_id,Revision.note_id==note.note_id).order_by(Revision.version.desc()).first()
        if original is None or original.kind!="storyline" or original.knowledge_idx>=safe:
            raise NoteError("not_found","当前安全范围内没有这条剧情线")
        if original.version!=note.expected_version:
            raise NoteError("version_conflict","另一窗口已更新，请重新查询后合并",{"latest_version":original.version})
        current=copy.deepcopy(original.content)
        node_map={n["node_id"]:n for n in graph.nodes(current)}
        if set(note.remove_node_ids)-node_map.keys():
            raise NoteError("invalid_arguments","待移除的节点不存在")
        changed={n.node_id for n in note.nodes}
        preserved_stale=set(node_map)-changed-set(note.remove_node_ids)
        for key in note.remove_node_ids:node_map.pop(key)
        for node in note.nodes:node_map[node.node_id]=node.model_dump(exclude_none=True)
        edge_map={(e["source"],e["target"],e["relation"]):e for e in current.get("edges",[])
                  if e["source"] in node_map and e["target"] in node_map}
        for edge in note.remove_edges:
            key=(edge.source,edge.target,edge.relation)
            if key not in edge_map:raise NoteError("invalid_arguments","待移除的连线不存在")
            edge_map.pop(key)
        for edge in note.edges:edge_map[(edge.source,edge.target,edge.relation)]=edge.model_dump(exclude_none=True)
        current.update(nodes=list(node_map.values()),edges=list(edge_map.values()))
        for key in ("title","overview","stage","open_questions"):
            value=getattr(note,key)
            if value is not None:current[key]=value
        current.update(note_id=note.note_id,expected_version=note.expected_version,change_reason=note.change_reason)
        try:
            note=StorylineWrite.model_validate({k:v for k,v in current.items() if k in StorylineWrite.model_fields})
        except ValidationError as exc:
            raise NoteError("invalid_arguments",str(exc))
    data = note.model_dump(exclude_none=True)
    note_id = note.note_id
    if bool(note_id) != bool(note.expected_version):
        raise NoteError("invalid_arguments", "更新需同时提供 note_id 和 expected_version")
    if isinstance(note, EntryWrite):
        if note.basis == "interpretation" and not note.perspective:
            raise NoteError("invalid_arguments", "解释性笔记必须注明 perspective（谁的判断或叙述视角）")
        evidence = validate_evidence(db, book_id, note.evidence, safe)
        names = list(dict.fromkeys(_names(data["payload"])))
        data["evidence"] = evidence
        if note.chapter_number is not None:
            _chapter(db, book_id, note.chapter_number, safe)
            data["event_chapters"] = [note.chapter_number]
        else:
            data["event_chapters"] = sorted({e["chapter_number"] for e in evidence})
        data["entities"] = names
    else:
        names = list(dict.fromkeys(note.entities))
        chapters = []
        normalized_nodes = []
        latest_entries = {r.note_id:r for r in _latest(db, book_id, safe).filter(Revision.kind == "entry").all()}
        for node in note.nodes:
            _chapter(db, book_id, node.chapter_number, safe)
            for ref in node.evidence:
                entry = db.query(Revision).filter(Revision.book_id == book_id, Revision.note_id == ref.note_id,
                    Revision.version == ref.version, Revision.kind == "entry", Revision.knowledge_idx < safe).first()
                if entry is None:
                    raise NoteError("evidence_unavailable", "引用的证据不存在或超出安全范围")
                latest_entry = latest_entries.get(ref.note_id)
                if (latest_entry is None or latest_entry.version != ref.version or latest_entry.content.get("status", "active") != "active") and node.node_id not in preserved_stale:
                    raise NoteError("stale_evidence", "引用已被修订、撤回或存在争议，请重新查证据", {"note_id":ref.note_id})
                if node.chapter_number < max(entry.content["event_chapters"]):
                    raise NoteError("invalid_arguments", "节点章不能早于其证据中最晚的章节")
                actual_chapters = [item["chapter_number"] for item in entry.content["evidence"]]
                if (not _entry_scope_valid(entry.content) or set(actual_chapters) != {node.chapter_number} or set(entry.content["event_chapters"]) != {node.chapter_number}) and node.node_id not in preserved_stale:
                    raise NoteError("evidence_chapter_mismatch", "引用笔记的实际证据超出节点章或混录多章，请先拆分、修订证据笔记",
                        {"note_id":entry.note_id,"node_chapter":node.chapter_number,"evidence_chapters":sorted(set(actual_chapters))})
            chapters.append(node.chapter_number)
            value = node.model_dump(exclude_none=True)
            value["node_id"] = graph.node_id(value)
            normalized_nodes.append(value)
        ids = [n["node_id"] for n in normalized_nodes]
        if len(ids) != len(set(ids)):
            raise NoteError("invalid_arguments", "节点 ID 重复；同章多个节点请分别指定稳定 node_id")
        positions = {n["node_id"]:n["chapter_number"] for n in normalized_nodes}
        connections = set()
        for edge in note.edges:
            if edge.source not in positions or edge.target not in positions:
                raise NoteError("invalid_arguments", "连线的两个端点必须在本剧情线 nodes 中")
            if edge.relation == "follows" and positions[edge.source] > positions[edge.target]:
                raise NoteError("invalid_arguments", "follows 按章节阅读顺序连接；后文揭示前文请用 reveals")
            key = (edge.source, edge.target, edge.relation)
            if key in connections:
                raise NoteError("invalid_arguments", "同一连接重复")
            connections.add(key)
        # Narrative order and causation cannot loop individually. Revelation and
        # clue links may legitimately point back to earlier chapters.
        for relation in ("follows","causes"):
            adjacency={key:set() for key in ids}
            for source,target,kind in connections:
                if kind==relation:adjacency[source].add(target)
            indegree={key:0 for key in ids}
            for targets in adjacency.values():
                for target in targets:indegree[target]+=1
            ready=[key for key in ids if not indegree[key]]
            visited=0
            while ready:
                source=ready.pop();visited+=1
                for target in adjacency[source]:
                    indegree[target]-=1
                    if not indegree[target]:ready.append(target)
            if visited!=len(ids):
                raise NoteError("invalid_arguments",f"{relation} 连接形成循环，请将后续新事件记为新节点")
        data["nodes"] = sorted(normalized_nodes, key=lambda n:n["chapter_number"])
        data["event_chapters"] = sorted(set(chapters))
        data["entities"] = names
    _check_names(db, book_id, names, safe,getattr(note,"chapter_number",None))
    data.pop("note_id", None)
    data.pop("expected_version", None)
    current = None
    if note_id:
        current = db.query(Revision).filter(Revision.book_id == book_id, Revision.note_id == note_id).order_by(Revision.version.desc()).first()
        if current is None or current.knowledge_idx >= safe:
            raise NoteError("not_found", "该笔记在当前安全范围不存在")
        if current.kind != note.kind:
            raise NoteError("invalid_arguments", "不能改变笔记类型")
        if current.version != note.expected_version:
            raise NoteError("version_conflict", "另一窗口已更新，请重新查询后合并", {"latest_version": current.version})
        if _comparable(current.content) == _comparable(data):
            if preview:
                return {"ok": True, "data": data, "before": current.content}
            return {"ok": True, "data": _public(current)}
    else:
        # Only deduplicate visible identical entries; never reveal future matches.
        same = _latest(db, book_id, safe).filter(Revision.kind == note.kind).all()
        for previous in same:
            if _comparable(previous.content) == _comparable(data):
                if preview:
                    return {"ok": True, "data": data, "before": previous.content}
                return {"ok": True, "data": _public(previous), "deduplicated": True}
        fingerprint = json.dumps({k:v for k,v in data.items() if k != "question"}, ensure_ascii=False, sort_keys=True)
        note_id = ("line_" if note.kind == "storyline" else "note_") + hashlib.sha256(
            f"{book_id}|{safe}|{fingerprint}".encode()).hexdigest()[:32]
        if _is_deleted(db, book_id, note_id):
            note_id = "line_" + uuid.uuid4().hex
    if preview:
        return {"ok": True, "data": data, "before": current.content if current else None}
    row = Revision(book_id=book_id, note_id=note_id, version=(current.version + 1 if current else 1),
                   kind=note.kind, knowledge_idx=safe-1, content=data)
    db.add(row)
    try:
        embeddings.stage_revision(db, row)
        db.commit() if commit else db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.query(Revision).filter(Revision.book_id==book_id, Revision.note_id==note_id,
            Revision.knowledge_idx<safe).order_by(Revision.version.desc()).first()
        if not current and existing and _comparable(existing.content) == _comparable(data):
            return {"ok":True,"data":_public(existing),"deduplicated":True}
        raise NoteError("version_conflict", "另一窗口已更新，请重新查询后合并")
    if commit:
        embeddings.notify()
    visible_latest={r.note_id:r for r in _latest(db,book_id,safe).all()} if row.kind=="storyline" else None
    result = {"ok": True, "data": _public(row,latest=visible_latest)}
    if current and row.kind == "entry":
        affected = []
        latest = {r.note_id:r for r in _latest(db, book_id, safe).all()}
        for line in latest.values():
            if line.kind == "storyline" and any(ref["note_id"] == row.note_id for n in line.content["nodes"] for ref in n["evidence"]):
                stale=_dependency_state(line,latest)
                affected.append({"note_id":line.note_id,"version":line.version,
                                 "stale_node_ids":stale[:8],"stale_node_count":len(stale)})
        result["affected_storylines"] = affected[:5]
        result["affected_has_more"] = len(affected) > 5
        result["next_action"] = "按返回 ID 查询剧情线，核对并更新结论及证据版本；不要机械替换引用"
    return result



def _identity(db, book_id, name, safe):
    from app.services import entity_layer_service
    from app.models.character import Character
    result = entity_layer_service.get_alias_candidates(db, book_id, name, chapter_idx=safe-1, limit=0)
    ids = {c["character_id"] for c in result.get("candidates", [])}
    # A canonical name is usable only when it has actually appeared in visible text.
    source = text_window_service.source_text(book_id)
    chapters = db.query(Chapter).filter(Chapter.book_id == book_id, Chapter.kind == "main", Chapter.idx < safe).all()
    if any(name in source[c.char_start:c.char_end] for c in chapters):
        ids.update(c.id for c in db.query(Character).filter(Character.book_id == book_id, Character.canonical_name == name))
    return ids if len(ids) == 1 else set()


def import_reviewed(db, book_id, request):
    """Explicitly reviewed copies only. Original rows are never rewritten."""
    from app.models.note import Note
    from app.models.reading_note import LegacyNoteImport
    safe = gate(db, book_id)
    original = db.query(Note).filter(Note.book_id==book_id, Note.id==request.legacy_id).first()
    if original is None:
        raise NoteError("not_found", "旧笔记不存在")
    raw = json.dumps({"subject_key":original.subject_key,"quote":original.quote,"payload":original.payload_json},ensure_ascii=False,sort_keys=True)
    if hashlib.sha256(raw.encode()).hexdigest()!=request.source_hash:
        raise NoteError("source_changed", "原笔记已变化，请重新生成审核报告")
    existing = db.query(LegacyNoteImport).filter(LegacyNoteImport.book_id==book_id,LegacyNoteImport.legacy_id==request.legacy_id).first()
    if existing:
        row = _latest(db,book_id,safe).filter(Revision.note_id==existing.note_id).first()
        if row is None:
            raise NoteError("not_found", "已导入记录在当前安全范围不可见")
        return {"ok":True,"data":_public(row),"already_imported":True}
    if request.note.note_id or request.note.expected_version:
        raise NoteError("invalid_arguments", "迁移只能创建新条目，修订请使用 note_upsert")
    saved = upsert(db,book_id,request.note)
    db.add(LegacyNoteImport(book_id=book_id,legacy_id=request.legacy_id,note_id=saved["data"]["note_id"]))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise NoteError("version_conflict", "另一窗口已处理该条旧笔记，请重新查询")
    return saved


def storyline_detail(db, book_id, note_id, as_of=None):
    from app.services import storyline_view_service
    return storyline_view_service.detail(db,book_id,note_id,as_of)


def query(db, book_id, args, listing=False, safe_override=None):
    token = None
    if args.cursor:
        try:token = tool_token_service.decode(args.cursor)
        except ValueError as exc:
            raise NoteError("invalid_cursor",str(exc),{"reason":"invalid_token","retry":"重新查询第一页"})
    maximum = gate(db,book_id,safe_override)
    event_scope = args.as_of_chapter if args.as_of_chapter is not None else maximum
    if not listing and args.view=="directory":
        snapshot = token.get("safe") if token else (event_scope if args.time_mode=="strict" else maximum)
        if type(snapshot) is not int or not 1<=snapshot<=maximum:
            raise NoteError("invalid_cursor" if token else "chapter_out_of_range","目录快照超出当前安全范围，请重新查询",
                {"reason":"reading_scope_shrank","allowed_max":maximum})
        if args.as_of_chapter is None:event_scope = token.get("event_scope",snapshot) if token else event_scope
        args = args.model_copy(update={"as_of_chapter":event_scope})
        safe = snapshot
    else:
        safe = event_scope if args.time_mode=="strict" else maximum
    if not 1<=event_scope<=maximum:
        raise NoteError("chapter_out_of_range",f"回看范围必须在第1–{maximum}章内",{"allowed_max":maximum})
    if not listing and args.view=="excerpts":
        from app.services import note_excerpt_service
        return note_excerpt_service.query(db,book_id,args,event_scope,token)
    if not listing and args.view=="review":
        from app.services import note_navigation_service
        return note_navigation_service.review_node(db,book_id,args)
    if not listing and args.view == "references":
        if args.chapter_to and args.chapter_to>safe:
            raise NoteError("chapter_out_of_range","不能超过当前安全章")
        from app.services import note_navigation_service
        return note_navigation_service.references(db,book_id,args)
    if not listing and args.note_id and _is_deleted(db, book_id, args.note_id):
        raise NoteError("not_found", "该剧情线已删除")
    lo = getattr(args, "chapter_from", None) or 1
    hi = getattr(args, "chapter_to", None) or event_scope
    if hi > event_scope or lo > event_scope:
        raise NoteError("chapter_out_of_range", f"事件范围只允许第 1–{event_scope} 章", {"allowed_max":event_scope})
    signature = hashlib.sha256(json.dumps(args.model_dump(exclude={"cursor"}), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    start = 0
    semantic_cutoff, semantic_model, semantic_enabled = None, None, None
    watermark = db.query(func.max(Revision.id)).filter(Revision.book_id == book_id, Revision.knowledge_idx < safe).scalar() or 0
    if token:
        if any(token.get(k) != v for k,v in {"book":book_id,"safe":safe,"sig":signature,"listing":listing}.items()):
            current = args.model_dump(exclude={"cursor"},exclude_defaults=True)
            expected = token.get("filters",{})
            changed = sorted(key for key in current.keys()|expected.keys() if current.get(key)!=expected.get(key)) if "filters" in token else []
            reason = "wrong_book" if token.get("book")!=book_id else ("reading_scope_changed" if token.get("safe")!=safe else "filters_changed")
            raise NoteError("invalid_cursor", "查询条件或阅读范围已变化，请重新查询",
                {"reason":reason,"changed_fields":changed,"retry":"保持筛选条件不变，或重新查询第一页"})
        start, watermark = token["start"], token["watermark"]
        semantic_cutoff = token.get("semantic_cutoff")
        semantic_model = token.get("semantic_model")
        semantic_enabled = token.get("semantic_enabled")
    def make_cursor(after):
        return tool_token_service.encode({"book":book_id,"safe":safe,"sig":signature,"listing":listing,
            **({"event_scope":event_scope} if args.time_mode=="retrospective" else {}),
            "filters":args.model_dump(exclude={"cursor"},exclude_defaults=True),"start":after,"watermark":watermark,
            "semantic_cutoff":semantic_cutoff,"semantic_model":retrieval.get("model_key"),
            "semantic_enabled":retrieval["query_ready"]})
    latest = {r.note_id:r for r in _latest(db, book_id, safe, watermark).all()}
    if not listing and args.note_id and args.version:
        rows = db.query(Revision).filter(Revision.book_id==book_id, Revision.note_id==args.note_id,
            Revision.version==args.version, Revision.knowledge_idx<safe, Revision.id<=watermark,
            func.coalesce(Revision.content["deleted"].as_boolean(), False).is_(False)).all()
    else:
        rows = list(latest.values())
    if listing:
        rows = [r for r in rows if r.kind == "storyline"]
    elif args.view in {"entry", "events"}:
        rows = [r for r in rows if r.kind == "entry"]
    elif args.view in {"storyline","edges","directory"}:
        rows = [r for r in rows if r.kind == "storyline"]
    if not listing and args.note_id:
        rows = [r for r in rows if r.note_id == args.note_id]
    if listing or not args.note_id:
        rows = [r for r in rows if r.content.get("status", "active") == "active" and (r.kind!="entry" or _entry_scope_valid(r.content))]
    identity_cache = {}
    def identity(name):
        if name not in identity_cache:
            identity_cache[name] = _identity(db, book_id, name, safe)
        return identity_cache[name]
    def name_match(a,b):
        return a == b or bool(identity(a) & identity(b))
    def participants(row):
        # Derive missing storyline participants from its valid referenced events.
        names = list(row.content["entities"])
        if row.kind == "storyline":
            for node in row.content["nodes"]:
                for ref in node["evidence"]:
                    entry = latest.get(ref["note_id"])
                    if entry and entry.version == ref["version"] and entry.content.get("status", "active") == "active":
                        names.extend(entry.content["entities"])
        return list(dict.fromkeys(names))
    participant_names = {r.id:participants(r) for r in rows}
    requested_names = list(args.entities)
    explicit_entities = bool(requested_names)
    # Derive entity hints only from the already-gated set, never from full-book names.
    if args.query and not requested_names:
        visible_names = {n for names in participant_names.values() for n in names}
        requested_names = sorted(n for n in visible_names if n in args.query)
        from app.models.character import Character
        known_names = db.query(Character.canonical_name).filter(Character.book_id==book_id).all()
        requested_names.extend(n for (n,) in known_names if n in args.query and n not in requested_names and identity(n))
        if not requested_names:
            from app.models.character_alias_observation import CharacterAliasObservation
            aliases=db.query(CharacterAliasObservation.alias).filter(
                CharacterAliasObservation.book_id==book_id,CharacterAliasObservation.chapter_idx<safe).distinct().all()
            requested_names=[alias for (alias,) in aliases if len(alias)>=2 and alias in args.query and identity(alias)]
    candidates = []
    for row in rows:
        data = row.content
        if requested_names:
            matching=[any(name_match(req,n) for n in participant_names[row.id]) for req in requested_names]
            if not (all(matching) if explicit_entities and args.entity_match=="all" else any(matching)):continue
        if not any(lo <= ch <= hi for ch in data["event_chapters"]):
            continue
        candidates.append(row)
    # Build the semantic allowlist only after revision, chapter, entity and status gates.
    allowed = {}
    for row in candidates:
        if row.content.get("status", "active") != "active":
            continue
        stale = set(_dependency_state(row, latest)) if row.kind == "storyline" else set()
        for segment,text in embeddings.segments(row).items():
            if row.kind == "storyline":
                if segment == "overview" and (stale or not all(lo <= ch <= hi for ch in row.content["event_chapters"])):
                    continue
                if segment != "overview":
                    node = next(n for n in graph.nodes(row.content) if "node:"+n["node_id"] == segment)
                    if node["node_id"] in stale or not lo <= node["chapter_number"] <= hi:
                        continue
            allowed[(row.id,segment)] = text
    retrieval = {"semantic":"not_needed", "query_ready":False}
    semantic = {}
    topical_query = graph.topic_query(args.query,requested_names)
    name_only = bool(args.query and args.query.strip() in requested_names)
    focused = args.relevance=="focused" and not (not listing and args.note_id)
    if args.query and (listing or not args.note_id) and (topical_query or name_only or not focused):
        semantic,retrieval,semantic_cutoff = embeddings.semantic_scores(db,args.query,allowed,
            cutoff=semantic_cutoff,expected_model=semantic_model,enabled=semantic_enabled)
        semantic = {key:score for key,score in semantic.items() if score>=args.min_similarity}
    documents = []
    for row in candidates:
        text = _public(row,latest=latest)
        text["entities"] = participant_names[row.id]
        if row.kind == "storyline":
            # Safe node conclusions participate in lexical retrieval too.
            text["search_nodes"] = [value for (revision,segment),value in allowed.items() if revision==row.id and segment!="overview"]
        documents.append(graph.search_text(text))
    scores = graph.search_scores(args.query, documents)
    topical_scores = graph.search_scores(topical_query,documents) if topical_query else [0.0]*len(documents)
    vector_scores = [max((score for (revision,_),score in semantic.items() if revision==r.id),default=0) for r in candidates]
    person_scores = [sum(any(name_match(req,n) for n in participant_names[r.id]) for req in requested_names) for r in candidates]
    fused = graph.fuse_scores(topical_scores if focused and topical_query and requested_names else scores,vector_scores,person_scores)
    ranked = []
    navigation = {}
    def navigate(row,reason):
        navigation[row.note_id] = {"kind":row.kind,"note_id":row.note_id,"version":row.version,
            "title":row.content["title"],"reason":reason,"match_scope":"navigation_only"}
    for row,score,lex,vec,person,topic in zip(candidates,fused,scores,vector_scores,person_scores,topical_scores):
        relevant = not args.query or (not listing and args.note_id) or (lex>0 or vec>0 or person>0)
        if relevant and args.query and focused and not name_only and not (topic>0 or vec>0):
            if person>0:navigate(row,"entity_only")
            continue
        if relevant:ranked.append((row,score))
    ranked.sort(key=lambda pair:(-pair[1], pair[0].content["event_chapters"][0], pair[0].note_id))
    items, edge_sources = [], []
    item_scores = {}
    match_details = {}
    for row,document,lex,vec,person,topic in zip(candidates,documents,scores,vector_scores,person_scores,topical_scores):
        match_details[row.note_id] = {"matched_by":(["keyword"] if lex>0 else [])+(["semantic"] if vec>0 else [])+(["entity"] if person>0 else []),
            "matched_terms":graph.matched_terms(args.query,document),
            "semantic_similarity":round(vec,4) if vec else None,
            "match_scope":"entity_only" if person>0 and not topic and not vec and not name_only else "content"}
    ranked.sort(key=lambda pair:(match_details[pair[0].note_id]["match_scope"]=="entity_only",-pair[1],
        pair[0].content["event_chapters"][0],pair[0].note_id))
    graph_view = not listing and args.view == "graph" and not (args.note_id and ranked and ranked[0][0].kind == "entry")
    for row,document_score in ranked:
        item = _public(row, latest=latest)
        item["entities"] = participant_names[row.id][:8]
        if len(participant_names[row.id]) > 8:
            item["entities_has_more"] = True
        if not listing and args.view=="edges":
            stale=set(_dependency_state(row,latest))
            positions={n["node_id"]:n["chapter_number"] for n in graph.nodes(row.content)}
            for edge in row.content.get("edges",[]):
                if edge["source"] in stale or edge["target"] in stale:continue
                if not (lo<=positions[edge["source"]]<=hi and lo<=positions[edge["target"]]<=hi):continue
                items.append({"kind":"edge","note_id":row.note_id,"version":row.version,
                    "known_by_chapter":row.knowledge_idx+1,**edge})
            continue
        expand = not listing and row.kind == "storyline" and (args.note_id or graph_view)
        if expand:
            line_nodes = graph.nodes(row.content)
            stale = set(_dependency_state(row,latest))
            selected_id = getattr(args, "node_id", None)
            neighborhood = None
            if selected_id:
                neighborhood = {selected_id}
                for edge in row.content.get("edges", []):
                    if selected_id in (edge["source"],edge["target"]):
                        neighborhood.update((edge["source"],edge["target"]))
            focus = None
            local_scores = graph.search_scores(topical_query if focused and topical_query and requested_names else args.query, [n["conclusion"] for n in line_nodes])
            local_vectors = [semantic.get((row.id,"node:"+n["node_id"]),0) for n in line_nodes]
            # Invalidated nodes cannot become search anchors through their old wording.
            local_scores = [s if n["node_id"] not in stale and lo<=n["chapter_number"]<=hi else 0 for n,s in zip(line_nodes,local_scores)]
            local_scores = graph.fuse_scores(local_scores,local_vectors,[0]*len(line_nodes))
            if args.query and not args.note_id:
                best = sorted((i for i in range(len(line_nodes)) if local_scores[i]>0 or not focused or name_only), key=lambda i:-local_scores[i])[:2]
                focus = {line_nodes[i]["node_id"] for i in best}
                if not focus:navigate(row,"line_metadata_only")
                if not focused:
                    anchors = set(focus)
                    for edge in row.content.get("edges", []):
                        if edge["source"] in anchors or edge["target"] in anchors:
                            focus.update((edge["source"], edge["target"]))
            for i,node in enumerate(line_nodes):
                if not lo <= node["chapter_number"] <= hi or (neighborhood is not None and node["node_id"] not in neighborhood):
                    continue
                if focus is not None and node["node_id"] not in focus:
                    continue
                if args.view == "directory":
                    items.append({"kind":"storyline_node","note_id":row.note_id,"version":row.version,
                        "node_id":node["node_id"],"chapter_number":node["chapter_number"],
                        "title":"待核对关联证据" if node["node_id"] in stale else node["conclusion"][:40],
                        **basis_service.node(node,latest,node["node_id"] in stale,labels=False),
                        "needs_revision":node["node_id"] in stale})
                    continue
                item_scores[(row.note_id,node["node_id"])] = document_score + local_scores[i]
                public_node = dict(node)
                public_node.update(basis_service.node(node,latest,node["node_id"] in stale,labels=False))
                if node["node_id"] in stale:
                    public_node["conclusion"] = "关联证据已修订，结论暂不用于回答；请核对后更新。"
                    public_node["needs_revision"] = True
                if graph_view:
                    items.append({"kind":"storyline_node", "note_id":row.note_id,"version":row.version,
                        "graph_id":f"{row.note_id}:{node['node_id']}", "title":item["title"],
                        "known_by_chapter":row.knowledge_idx+1, **public_node})
                else:
                    items.append({"kind":"storyline_node","note_id":row.note_id,"version":row.version,"node_number":i+1,"node":public_node,
                        "adjacent_edges":[e for e in row.content.get("edges",[]) if node["node_id"] in (e["source"],e["target"])][:4],
                        "expand_hint":"指定本线 note_id、node_id 与 view=graph 可查看相邻分支"})
            edge_sources.extend((row.note_id,e) for e in row.content.get("edges",[]) if e["source"] not in stale and e["target"] not in stale)
        elif not listing and args.view in {"graph", "events"} and not args.note_id:
            items.append(graph.event_projection(item))
            item_scores[(row.note_id,row.note_id)] = document_score
        else:
            items.append(item)
    if args.query and not listing and not args.note_id:
        # A small local neighborhood is selected; callers can target a line/node to expand a branch.
        items.sort(key=lambda item:(match_details.get(item["note_id"],{}).get("match_scope")=="entity_only",-item_scores.get((item["note_id"],item.get("node_id",item["note_id"])),0),item.get("chapter_number") or min(item.get("event_chapters",[safe])),item["note_id"]))
    header = _public(ranked[0][0],latest=latest) if not listing and args.note_id and ranked and ranked[0][0].kind=="storyline" else None
    compact = graph_view and args.compact
    page = []
    by_note = {row.note_id:row for row in candidates}
    def annotate_time(item):
        row = by_note.get(item["note_id"])
        if row is not None and args.time_mode=="retrospective":
            item["recorded_at_chapter"] = row.knowledge_idx+1
            item["written_after_as_of"] = row.knowledge_idx+1>event_scope
        return item
    for item in items:annotate_time(item)
    if header:annotate_time(header)
    if args.query:
        revision_ids = {row.note_id:row.id for row in candidates}
        for item in items:
            item.update(match_details.get(item["note_id"],{}))
            if item["kind"]=="storyline_node":
                node = item.get("node",item)
                similarity = semantic.get((revision_ids[item["note_id"]],"node:"+node["node_id"]))
                item["semantic_similarity"] = round(similarity,4) if similarity is not None else None
                item["matched_terms"] = graph.matched_terms(args.query,node.get("conclusion",""))
                local_match = graph.search_scores(topical_query if focused and topical_query and requested_names else args.query,
                    [node.get("conclusion","")])[0]
                item["matched_by"] = [method for method in item["matched_by"]
                    if (method!="semantic" or similarity is not None) and (method!="keyword" or local_match>0)]
                if not local_match and similarity is None:item["match_scope"] = "context"
    for item in items[start:start+args.limit]:
        trial = page+[item]
        content = {} if compact else {"items":trial}
        if header:content["storyline"]=header
        if graph_view:
            content["graph"] = graph.graph_page(trial,edge_sources)
        if len(json.dumps(content,ensure_ascii=False)) > MAX_CHARS-1000:
            break
        page = trial
    next_start = start+len(page)
    has_more = next_start < len(items)
    cursor = make_cursor(next_start) if has_more else None
    data = {"items":page,"safe_chapter":safe,"has_more":has_more,"cursor":cursor,"matched_count":len(items),
        "time_mode":args.time_mode,
        "knowledge_boundary_policy":"write_scope" if args.time_mode=="strict" else "current_reading_knowledge",
        "matched_counts":{"entries":sum(r.kind=="entry" for r,_ in ranked),"storylines":sum(r.kind=="storyline" for r,_ in ranked),
            "nodes":sum(i["kind"]=="storyline_node" for i in items),"returned_units":len(items)},
        "coverage_note":"无笔记不代表未读；有笔记不代表该章已完整整理"}
    if header:
        data["storyline"]=header
    if graph_view:
        data["graph"] = graph.graph_page(page,edge_sources)
    if compact:data.pop("items")
    if navigation:
        data["navigation_candidates"] = list(navigation.values())[:3]
        data["navigation_count"] = len(navigation)
        if not page:data["next_action"] = "当前仅有导航线索，请补充要确认的具体事实，或按候选ID/章节范围展开；不要据人物相关性直接回答。"
    if not page and args.query and not listing:
        lines = [r for r in latest.values() if r.kind=="storyline" and
            any(lo<=ch<=hi for ch in r.content["event_chapters"]) and
            (not requested_names or any(any(name_match(req,n) for n in participants(r)) for req in requested_names))]
        data["storyline_candidates"] = [{"note_id":r.note_id,"title":r.content["title"],"version":r.version} for r in lines[:3]]
        if not navigation:data["next_action"] = "可按候选 note_id 展开；没有候选时用人物/事件关键词检索原文。不要只反复改写整句。"
    data["search_scope"] = {"book_id":book_id,"as_of_chapter":event_scope,"chapter_from":lo,"chapter_to":hi,
        "entity_hints":requested_names,"entity_match":args.entity_match if explicit_entities else "any"}
    data["retrieval"] = {k:v for k,v in retrieval.items() if k not in {"model_key","query_ready"}}
    data["retrieval"]["method"] = "人物关联＋关键词＋可用的向量相似度"
    data["retrieval"].update(min_similarity=args.min_similarity,relevance=args.relevance,
        similarity_note="semantic_similarity是余弦相似度，不代表置信概率。")
    if args.time_mode=="retrospective":
        data["time_scope"] = {"event_as_of_chapter":event_scope,"knowledge_as_of_chapter":safe}
        data["time_note"] = "按当前已读知识事后整理；标题和结论可能包含目标章之后的认识，不代表当时已知。"
    if page and all(item.get("match_scope")=="entity_only" for item in page):
        data["relevance_note"] = "本页仅人物相关，尚未匹配问题中的具体事件；请作为导航线索，勿直接据此回答。"
    # Include the actual cursor and diagnostics in the final budget, rather than
    # guessing their overhead from the page content alone.
    while len(json.dumps(data,ensure_ascii=False)) > MAX_CHARS and len(page)>1:
        page.pop()
        if not compact:data["items"] = page
        if graph_view:
            data["graph"] = graph.graph_page(page,edge_sources)
        data["has_more"] = True
        data["cursor"] = make_cursor(start+len(page))
    return {"ok":True,"data":data}
