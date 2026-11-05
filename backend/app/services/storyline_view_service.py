"""Human storyline views without exposing later-written interpretations.

Use a historical safe snapshot when one exists. Otherwise show only chapter-local
source excerpts under neutral labels, never backdate an agent's later conclusions.
Model-facing notes retain their stricter revision knowledge gate.
"""
import copy
import hashlib
import json
from sqlalchemy import func
from app.models.reading_note import ReadingNoteRevision as Revision
from app.models.chapter import Chapter
from app.services import reading_note_service as memory, note_graph_service as graph, tool_token_service
from app.services import note_basis_service
from app.services.note_service import NoteError


class View:
    def __init__(self,db,book_id,safe,watermark=None):
        self.safe=safe
        self.deleted={note_id for (note_id,) in memory._deleted_ids(db,book_id)}
        self.rows=db.query(Revision).filter(Revision.book_id==book_id)
        if watermark is not None:self.rows=self.rows.filter(Revision.id<=watermark)
        self.rows=self.rows.order_by(Revision.id).all()
        self.all={r.note_id:r for r in self.rows}
        self.visible={r.note_id:r for r in self.rows if r.knowledge_idx<safe}
        self.entries={(r.note_id,r.version):r for r in self.rows if r.kind=="entry"}
        self.main={number+1 for (number,) in db.query(Chapter.idx).filter(
            Chapter.book_id==book_id,Chapter.kind=="main",Chapter.idx<safe)}

    def project(self,note_id):
        if note_id in self.deleted:return None
        latest=self.all.get(note_id)
        if latest is None or latest.kind!="storyline":return None
        historical=self.visible.get(note_id)
        row=historical or latest
        if row.content.get("deleted"):return None
        limited=historical is None
        stale=set(memory._dependency_state(row,self.visible)) if not limited else set()
        nodes=[]; notes={}; id_map={}
        for node in graph.nodes(row.content):
            chapter=node["chapter_number"]
            if chapter not in self.main:continue
            if limited:
                excerpts=[]
                for ref in node["evidence"]:
                    entry=self.entries.get((ref["note_id"],ref["version"]))
                    if entry is None or entry.content.get("status","active")!="active":continue
                    if (entry.content.get("chapter_number") or chapter)>chapter:continue
                    quotes=[copy.deepcopy(e) for e in entry.content["evidence"] if e["chapter_number"]==chapter]
                    if not quotes:continue
                    excerpts.append({"note_id":entry.note_id,"version":entry.version,"kind":"entry",
                        "title":f"第{chapter}章原文摘录","chapter_number":chapter,"known_by_chapter":chapter,
                        "event_chapters":[chapter],"entities":[],"status":"active","basis":"explicit",
                        "claim":"仅展示本章原文，后期整理的结论暂不展开。","evidence":quotes,"projection":"evidence_only",
                        "basis_label":"原文摘录","perspective":None,"basis_scope":"quotation",
                        "recorded_at_chapter":entry.knowledge_idx+1})
                if not excerpts:continue
                public_id="p_"+hashlib.sha256(f"{note_id}|{row.version}|{node['node_id']}".encode()).hexdigest()[:24]
                value={"node_id":public_id,"chapter_number":chapter,"conclusion":"原文摘录："+excerpts[0]["evidence"][0]["quote"],
                    "evidence":[{"note_id":e["note_id"],"version":e["version"]} for e in excerpts],"projection":"evidence_only",
                    "basis":"explicit","basis_label":"原文摘录","perspective":None,"basis_scope":"quotation"}
                notes[public_id]=excerpts
            else:
                value=copy.deepcopy(node)
                value.update(note_basis_service.node(node,self.visible,node["node_id"] in stale))
                if node["node_id"] in stale:
                    value["conclusion"]="证据已修订或章节范围不一致，结论暂不用于回答；请核对后更新。"
                    value["needs_revision"]=True
                excerpts=[]
                for ref in node["evidence"]:
                    entry=self.entries.get((ref["note_id"],ref["version"]))
                    if entry is not None and entry.knowledge_idx<self.safe:
                        excerpts.append(memory._public(entry,detail=True,latest=self.visible))
                notes[value["node_id"]]=excerpts
            id_map[node["node_id"]]=value["node_id"]
            nodes.append(value)
        if not nodes:return None
        edges=[]
        for edge in row.content.get("edges",[]):
            if edge["source"] not in id_map or edge["target"] not in id_map:continue
            if limited:
                # Chapter sequence is observable; late causal/revelation claims are not.
                if edge["relation"]!="follows":continue
                value={"source":id_map[edge["source"]],"target":id_map[edge["target"]],"relation":"follows","basis":"explicit"}
            else:
                if edge["source"] in stale or edge["target"] in stale:continue
                value=copy.deepcopy(edge)
            edges.append(value)
        chapters=sorted({n["chapter_number"] for n in nodes})
        if limited:
            header={"note_id":row.note_id,"version":row.version,"kind":"storyline","known_by_chapter":max(chapters),
                "title":f"第{min(chapters)}章起的线索","category":"plot","entities":[],"event_chapters":chapters,
                "overview":"完整标题与结论在后期整理；当前仅展示已读章节的原文线索。",
                "stage":"已读范围内的原文摘录","open_questions":[],"node_count":len(nodes),
                "needs_revision":False,"stale_node_count":0,"stale_node_ids":[],"limited_view":True}
            header.update(basis="unclassified",basis_label="未标注",perspective=None)
        else:
            header=memory._public(row,latest=self.visible)
            header.update(node_count=len(nodes),event_chapters=chapters,limited_view=False)
        public={"storyline":header,"nodes":nodes,"edges":edges,"safe_chapter":self.safe,
            "hidden_edge_count":0 if limited else len(row.content.get("edges",[]))-len(edges),"limited_view":limited}
        return public,notes


def detail(db,book_id,note_id,as_of=None):
    safe=memory.gate(db,book_id,as_of)
    result=View(db,book_id,safe).project(note_id)
    if result is None:raise NoteError("not_found","当前阅读进度内没有这条剧情线")
    return {"ok":True,"data":result[0]}


def evidence(db,book_id,note_id,node_id):
    safe=memory.gate(db,book_id)
    result=View(db,book_id,safe).project(note_id)
    if result is None or node_id not in result[1]:
        raise NoteError("not_found","节点或证据不在当前阅读进度内，请刷新剧情线")
    return {"ok":True,"data":{"items":result[1][node_id],"safe_chapter":safe,"limited_view":result[0]["limited_view"]}}


def listing(db,book_id,query=None,cursor=None,limit=10):
    safe=memory.gate(db,book_id)
    watermark=db.query(func.max(Revision.id)).filter(Revision.book_id==book_id).scalar() or 0
    signature=hashlib.sha256(json.dumps([query,limit],ensure_ascii=False).encode()).hexdigest()
    start=0
    if cursor:
        try:token=tool_token_service.decode(cursor)
        except ValueError as exc:raise NoteError("invalid_cursor",str(exc))
        if any(token.get(k)!=v for k,v in {"kind":"storyline_view","book":book_id,"safe":safe,"sig":signature}.items()):
            raise NoteError("invalid_cursor","阅读进度或查询条件已变化，请刷新")
        start,watermark=token["start"],token["watermark"]
    view=View(db,book_id,safe,watermark)
    items=[]
    for key,row in view.all.items():
        if row.kind!="storyline":continue
        result=view.project(key)
        if result is None:continue
        projected=result[0]
        # Search only material actually visible in the current view.
        searchable=json.dumps([projected["storyline"],projected["nodes"]],ensure_ascii=False)
        if query and query.casefold() not in searchable.casefold():continue
        items.append(projected["storyline"])
    items.sort(key=lambda item:(min(item["event_chapters"]),item["note_id"]))
    page=items[start:start+limit];next_start=start+len(page)
    more=next_start<len(items)
    next_cursor=tool_token_service.encode({"kind":"storyline_view","book":book_id,"safe":safe,"sig":signature,
        "start":next_start,"watermark":watermark}) if more else None
    return {"ok":True,"data":{"items":page,"safe_chapter":safe,"has_more":more,"cursor":next_cursor,"matched_count":len(items)}}
