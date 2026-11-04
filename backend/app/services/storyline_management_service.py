"""User-directed moves and restoration retain immutable evidence versions."""
import copy
import uuid
from sqlalchemy.exc import IntegrityError
from app.models.reading_note import ReadingNoteRevision as Revision
from app.schemas.reading_note import StorylinePatch
from app.services import reading_note_service as notes
from app.services import note_embedding_service as embeddings
from app.services.note_service import NoteError


def deleted(db, book_id, offset=0, limit=20):
    safe = notes.gate(db, book_id)
    rows = notes._current_storylines(db, book_id).filter(
        Revision.content["deleted"].as_boolean().is_(True), Revision.knowledge_idx < safe)
    total = rows.count()
    page = rows.order_by(Revision.id.desc()).offset(offset).limit(limit).all()
    return {"ok": True, "data": {"items": [{"note_id": r.note_id, "version": r.version,
        "title": r.content["title"], "node_count": len(r.content["nodes"]),
        "deleted_at": r.create_time.isoformat()} for r in page], "total": total,
        "next_offset": offset + len(page) if offset + len(page) < total else None}}


def restore(db, book_id, note_id, expected_version):
    safe = notes.gate(db, book_id)
    current = notes._current_storylines(db, book_id).filter(Revision.note_id == note_id).first()
    if current is None or current.knowledge_idx >= safe:
        raise NoteError("not_found", "当前阅读范围内没有可恢复的剧情线")
    if current.version != expected_version:
        raise NoteError("version_conflict", "剧情线状态已变化，请刷新已删除列表")
    if not current.content.get("deleted"):
        raise NoteError("not_deleted", "该剧情线未删除，无需恢复")
    content = copy.deepcopy(current.content)
    content.pop("deleted", None)
    content.pop("status", None)
    content["change_reason"] = "用户恢复已删除剧情线"
    row = Revision(book_id=book_id, note_id=note_id, version=current.version + 1,
                   kind="storyline", knowledge_idx=safe - 1, content=content)
    db.add(row)
    try:
        embeddings.stage_revision(db, row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise NoteError("version_conflict", "剧情线状态已变化，请刷新已删除列表")
    embeddings.notify()
    return {"ok": True, "data": {"note_id": note_id, "version": row.version}}


def _editable(db, book_id, note_id, version, safe):
    row = notes._current_storylines(db, book_id).filter(Revision.note_id == note_id).first()
    if row is None or row.knowledge_idx >= safe or row.content.get("deleted"):
        raise NoteError("not_found", "当前阅读范围内无法编辑源线或目标线")
    if row.version != version:
        raise NoteError("version_conflict", "源线或目标线已更新，请刷新后重新选择")
    return row


def move(db, book_id, note_id, payload, node_id):
    safe = notes.gate(db, book_id)
    if note_id == payload.target_note_id:
        raise NoteError("invalid_arguments", "请选择另一条剧情线")
    source = _editable(db, book_id, note_id, payload.expected_version, safe)
    target = _editable(db, book_id, payload.target_note_id, payload.target_expected_version, safe)
    node = next((n for n in source.content["nodes"] if n["node_id"] == node_id), None)
    if node is None:
        raise NoteError("not_found", "待移动节点已不存在，请刷新源线")
    refs = {(r["note_id"], r["version"]) for r in node["evidence"]}
    if any(n["chapter_number"] == node["chapter_number"] and
           {(r["note_id"], r["version"]) for r in n["evidence"]} == refs for n in target.content["nodes"]):
        raise NoteError("duplicate_node", "目标线已有引用相同事件的节点，请核对后移除源线中的重复节点")
    moved = copy.deepcopy(node)
    moved["node_id"] = "moved_" + uuid.uuid4().hex
    edges = []
    if payload.connect_after:
        anchor = next((n for n in target.content["nodes"] if n["node_id"] == payload.connect_after), None)
        if anchor is None or anchor["chapter_number"] > node["chapter_number"]:
            raise NoteError("invalid_arguments", "前置节点必须在目标线内，且章号不能晚于移动节点")
        edges = [{"source": anchor["node_id"], "target": moved["node_id"], "relation": "follows"}]
    source_deleted = len(source.content["nodes"]) == 1
    try:
        # Both revisions commit together; a failed source change rolls back the target.
        result = notes.upsert(db, book_id, StorylinePatch.model_validate({
            "kind": "storyline_patch", "note_id": target.note_id, "expected_version": target.version,
            "change_reason": "用户从另一条剧情线移入节点", "nodes": [moved], "edges": edges}), commit=False)
        if source_deleted:
            notes.delete_storyline(db, book_id, source.note_id, source.version, commit=False)
        else:
            notes.upsert(db, book_id, StorylinePatch.model_validate({
                "kind": "storyline_patch", "note_id": source.note_id, "expected_version": source.version,
                "change_reason": "用户将节点移到另一条剧情线", "remove_node_ids": [node_id]}), commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    embeddings.notify()
    return {"ok": True, "data": {"target_note_id": target.note_id,
        "target_version": result["data"]["version"], "node_id": moved["node_id"], "source_deleted": source_deleted}}
