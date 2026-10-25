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


def _check_names(db, book_id, names, safe):
    source = text_window_service.source_text(book_id)
    chapters = db.query(Chapter).filter(Chapter.book_id == book_id, Chapter.kind == "main", Chapter.idx < safe).all()
    visible = "\n".join(source[c.char_start:c.char_end] for c in chapters)
    for name in names:
        if name not in visible:
            raise NoteError("entity_not_observed", "实体名称未在安全范围正文出现；请使用已出现的称呼", {"name": name})


def _latest(db, book_id, safe, watermark=None):
    scope = [Revision.book_id == book_id, Revision.knowledge_idx < safe]
    if watermark is not None:
        scope.append(Revision.id <= watermark)
    latest = db.query(Revision.note_id, func.max(Revision.version).label("v")).filter(*scope).group_by(Revision.note_id).subquery()
    return db.query(Revision).join(latest, and_(Revision.note_id == latest.c.note_id, Revision.version == latest.c.v)).filter(Revision.book_id == book_id)


def _dependency_state(row, latest):
    stale = []
    for node in graph.nodes(row.content):
        for ref in node["evidence"]:
            current = latest.get(ref["note_id"])
            if current is None or current.version != ref["version"] or current.content.get("status", "active") != "active":
                stale.append(node["node_id"])
                break
    return sorted(set(stale))


def _public(row, detail=False, latest=None):
    data = row.content
    result = {"note_id": row.note_id, "version": row.version, "kind": row.kind,
              "known_by_chapter": row.knowledge_idx + 1, "title": data["title"],
              "entities": data["entities"], "event_chapters": data["event_chapters"]}
    if row.kind == "entry":
        result.update({k: v for k, v in data.items() if k not in ("question", "event_chapters", "entities")})
        result["status"] = data.get("status", "active")
        result["chapter_label"] = "、".join(f"第{n}章" for n in data["event_chapters"])
        if latest and row.note_id in latest and latest[row.note_id].version > row.version:
            result["superseded_by_version"] = latest[row.note_id].version
    else:
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
            result["nodes"] = graph.nodes(data)
            result["edges"] = data.get("edges", [])
    return result


def validate_evidence(db, book_id, evidence, safe):
    source = text_window_service.source_text(book_id)
    result = []
    from app.services.note_service import _normalize_with_map
    for item in evidence:
        chapter = _chapter(db, book_id, item.chapter_number, safe)
        raw = source[chapter.char_start:chapter.char_end]
        match = locate(raw, item.quote)
        if match is None:
            raise NoteError("quote_not_found", "引文不在指定章中，请原样摘录")
        start, end = match
        normalized, _ = _normalize_with_map(raw)
        normalized_quote, _ = _normalize_with_map(item.quote)
        if normalized.find(normalized_quote, normalized.find(normalized_quote) + 1) >= 0:
            raise NoteError("quote_ambiguous", "引文在指定章中重复，请提供更长的原文")
        _chapter_at(db, book_id, source, chapter.char_start + start, chapter.char_start + end)
        result.append({"chapter_number": item.chapter_number,
                       "quote": source[chapter.char_start + start:chapter.char_start + end]})
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


def upsert(db, book_id, note, safe_override=None):
    safe = gate(db, book_id, safe_override)
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
    _check_names(db, book_id, names, safe)
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
            return {"ok": True, "data": _public(current)}
    else:
        # Only deduplicate visible identical entries; never reveal future matches.
        same = _latest(db, book_id, safe).filter(Revision.kind == note.kind).all()
        for previous in same:
            if _comparable(previous.content) == _comparable(data):
                return {"ok": True, "data": _public(previous), "deduplicated": True}
        fingerprint = json.dumps({k:v for k,v in data.items() if k != "question"}, ensure_ascii=False, sort_keys=True)
        note_id = ("line_" if note.kind == "storyline" else "note_") + hashlib.sha256(
            f"{book_id}|{safe}|{fingerprint}".encode()).hexdigest()[:32]
    row = Revision(book_id=book_id, note_id=note_id, version=(current.version + 1 if current else 1),
                   kind=note.kind, knowledge_idx=safe-1, content=data)
    db.add(row)
    try:
        embeddings.stage_revision(db, row)
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(Revision).filter(Revision.book_id==book_id, Revision.note_id==note_id,
            Revision.knowledge_idx<safe).order_by(Revision.version.desc()).first()
        if not current and existing and _comparable(existing.content) == _comparable(data):
            return {"ok":True,"data":_public(existing),"deduplicated":True}
        raise NoteError("version_conflict", "另一窗口已更新，请重新查询后合并")
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


def query(db, book_id, args, listing=False):
    safe = gate(db, book_id, args.as_of_chapter)
    lo = getattr(args, "chapter_from", None) or 1
    hi = getattr(args, "chapter_to", None) or safe
    if hi > safe or lo > safe:
        raise NoteError("chapter_out_of_range", f"只允许第 1–{safe} 章", {"allowed_max":safe})
    signature = hashlib.sha256(json.dumps(args.model_dump(exclude={"cursor"}), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    start = 0
    semantic_cutoff, semantic_model, semantic_enabled = None, None, None
    watermark = db.query(func.max(Revision.id)).filter(Revision.book_id == book_id, Revision.knowledge_idx < safe).scalar() or 0
    if args.cursor:
        try:
            token = tool_token_service.decode(args.cursor)
        except ValueError as exc:
            raise NoteError("invalid_cursor", str(exc))
        if any(token.get(k) != v for k,v in {"book":book_id,"safe":safe,"sig":signature,"listing":listing}.items()):
            raise NoteError("invalid_cursor", "查询条件或阅读进度已变化，请重新查询")
        start, watermark = token["start"], token["watermark"]
        semantic_cutoff = token.get("semantic_cutoff")
        semantic_model = token.get("semantic_model")
        semantic_enabled = token.get("semantic_enabled")
    latest = {r.note_id:r for r in _latest(db, book_id, safe, watermark).all()}
    if not listing and args.note_id and args.version:
        rows = db.query(Revision).filter(Revision.book_id==book_id, Revision.note_id==args.note_id,
            Revision.version==args.version, Revision.knowledge_idx<safe, Revision.id<=watermark).all()
    else:
        rows = list(latest.values())
    if listing:
        rows = [r for r in rows if r.kind == "storyline"]
    elif args.view in {"entry", "events"}:
        rows = [r for r in rows if r.kind == "entry"]
    elif args.view in {"storyline","edges"}:
        rows = [r for r in rows if r.kind == "storyline"]
    if not listing and args.note_id:
        rows = [r for r in rows if r.note_id == args.note_id]
    if listing or not args.note_id:
        rows = [r for r in rows if r.content.get("status", "active") == "active"]
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
            if not (all(matching) if explicit_entities else any(matching)):continue
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
    if args.query and (listing or not args.note_id):
        semantic,retrieval,semantic_cutoff = embeddings.semantic_scores(db,args.query,allowed,
            cutoff=semantic_cutoff,expected_model=semantic_model,enabled=semantic_enabled)
    documents = []
    for row in candidates:
        text = _public(row,latest=latest)
        text["entities"] = participant_names[row.id]
        if row.kind == "storyline":
            # Safe node conclusions participate in lexical retrieval too.
            text["search_nodes"] = [value for (revision,segment),value in allowed.items() if revision==row.id and segment!="overview"]
        documents.append(json.dumps(text,ensure_ascii=False))
    scores = graph.search_scores(args.query, documents)
    vector_scores = [max((score for (revision,_),score in semantic.items() if revision==r.id),default=0) for r in candidates]
    person_scores = [sum(any(name_match(req,n) for n in participant_names[r.id]) for req in requested_names) for r in candidates]
    fused = graph.fuse_scores(scores,vector_scores,person_scores)
    ranked = [(r,s) for r,s,lex,vec,person in zip(candidates,fused,scores,vector_scores,person_scores)
              if not args.query or lex > 0 or vec > 0 or person > 0 or (not listing and args.note_id)]
    ranked.sort(key=lambda pair:(-pair[1], pair[0].content["event_chapters"][0], pair[0].note_id))
    items, edge_sources = [], []
    item_scores = {}
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
            local_scores = graph.search_scores(args.query, [n["conclusion"] for n in line_nodes])
            local_vectors = [semantic.get((row.id,"node:"+n["node_id"]),0) for n in line_nodes]
            # Invalidated nodes cannot become search anchors through their old wording.
            local_scores = [s if n["node_id"] not in stale and lo<=n["chapter_number"]<=hi else 0 for n,s in zip(line_nodes,local_scores)]
            local_scores = graph.fuse_scores(local_scores,local_vectors,[0]*len(line_nodes))
            if args.query and not args.note_id:
                best = sorted(range(len(line_nodes)), key=lambda i:-local_scores[i])[:2]
                focus = {line_nodes[i]["node_id"] for i in best}
                anchors = set(focus)
                for edge in row.content.get("edges", []):
                    if edge["source"] in anchors or edge["target"] in anchors:
                        focus.update((edge["source"], edge["target"]))
            for i,node in enumerate(line_nodes):
                if not lo <= node["chapter_number"] <= hi or (neighborhood is not None and node["node_id"] not in neighborhood):
                    continue
                if focus is not None and node["node_id"] not in focus:
                    continue
                item_scores[(row.note_id,node["node_id"])] = document_score + local_scores[i]
                public_node = dict(node)
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
        items.sort(key=lambda item:(-item_scores.get((item["note_id"],item.get("node_id",item["note_id"])),0),item.get("chapter_number") or min(item.get("event_chapters",[safe])),item["note_id"]))
    header = _public(ranked[0][0],latest=latest) if not listing and args.note_id and ranked and ranked[0][0].kind=="storyline" else None
    page = []
    for item in items[start:start+args.limit]:
        trial = page+[item]
        content = {"items":trial}
        if header:content["storyline"]=header
        if graph_view:
            content["graph"] = graph.graph_page(trial,edge_sources)
        if len(json.dumps(content,ensure_ascii=False)) > MAX_CHARS-1000:
            break
        page = trial
    next_start = start+len(page)
    has_more = next_start < len(items)
    cursor = tool_token_service.encode({"book":book_id,"safe":safe,"sig":signature,"listing":listing,
        "start":next_start,"watermark":watermark,"semantic_cutoff":semantic_cutoff,
        "semantic_model":retrieval.get("model_key"),"semantic_enabled":retrieval["query_ready"]}) if has_more else None
    data = {"items":page,"safe_chapter":safe,"has_more":has_more,"cursor":cursor,"matched_count":len(items),
        "coverage_note":"无笔记不代表未读；有笔记不代表该章已完整整理"}
    if header:
        data["storyline"]=header
    if graph_view:
        data["graph"] = graph.graph_page(page,edge_sources)
    if not page and args.query and not listing:
        lines = [r for r in latest.values() if r.kind=="storyline" and
            any(lo<=ch<=hi for ch in r.content["event_chapters"]) and
            (not requested_names or any(any(name_match(req,n) for n in participants(r)) for req in requested_names))]
        data["storyline_candidates"] = [{"note_id":r.note_id,"title":r.content["title"],"version":r.version} for r in lines[:3]]
        data["next_action"] = "可按候选 note_id 展开；没有候选时用人物/事件关键词检索原文。不要只反复改写整句。"
    data["search_scope"] = {"book_id":book_id,"as_of_chapter":safe,"chapter_from":lo,"chapter_to":hi,
        "entity_hints":requested_names,"entity_match":"all" if explicit_entities else "any"}
    data["retrieval"] = {k:v for k,v in retrieval.items() if k not in {"model_key","query_ready"}}
    data["retrieval"]["method"] = "人物关联＋关键词＋可用的向量相似度"
    # Include the actual cursor and diagnostics in the final budget, rather than
    # guessing their overhead from the page content alone.
    while len(json.dumps(data,ensure_ascii=False)) > MAX_CHARS and len(page)>1:
        page.pop()
        data["items"] = page
        if graph_view:
            data["graph"] = graph.graph_page(page,edge_sources)
        data["has_more"] = True
        data["cursor"] = tool_token_service.encode({"book":book_id,"safe":safe,"sig":signature,"listing":listing,
            "start":start+len(page),"watermark":watermark,"semantic_cutoff":semantic_cutoff,
            "semantic_model":retrieval.get("model_key"),"semantic_enabled":retrieval["query_ready"]})
    return {"ok":True,"data":data}
