"""Single staging boundary for all model-origin storyline writes. Author: heimoxian."""
import hashlib
import json
from pydantic import TypeAdapter
from sqlalchemy.exc import IntegrityError
from app.models.storyline_proposal import StorylineProposal as Proposal
from app.models.reading_note import ReadingNoteRevision
from app.schemas.reading_note import NoteWrite, Evidence
from app.services import reading_note_service as notes
from app.services.note_service import NoteError


def submit(db, book_id, note, safe_override=None, source="model"):
    if note.kind == "entry":
        return notes.upsert(db, book_id, note, safe_override=safe_override)
    safe = notes.gate(db, book_id, safe_override)
    payload = note.model_dump(exclude_none=True)
    fingerprint = hashlib.sha256(json.dumps([safe, payload], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    previous = db.query(Proposal).filter_by(book_id=book_id, fingerprint=fingerprint).first()
    if previous:
        return receipt(previous)
    checked = notes.upsert(db, book_id, note, safe_override=safe, preview=True)
    row = Proposal(book_id=book_id, safe_chapter=safe, fingerprint=fingerprint, source=source,
                   payload=payload, before=checked.get("before"), after=checked["data"], status="pending")
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        row = db.query(Proposal).filter_by(book_id=book_id, fingerprint=fingerprint).one()
    return receipt(row)


def receipt(row):
    return {"ok": True, "data": {"proposal_id": row.id, "status": row.status,
            "title": row.after.get("title", "剧情线建议"), "requires_confirmation": row.status == "pending"},
            "message": "剧情线建议等待用户确认，尚未改动正式记录" if row.status == "pending" else "此建议已有处理结果，不重复提交"}


def listing(db, book_id):
    safe = notes.gate(db, book_id)
    rows = db.query(Proposal).filter(Proposal.book_id == book_id, Proposal.safe_chapter <= safe,
                                    Proposal.status == "pending").order_by(Proposal.id).all()
    return {"ok": True, "data": {"items": [{"id": r.id, "chapter": r.safe_chapter, "source": r.source,
            "before": r.before, "after": r.after, "evidence": evidence(db, book_id, r, safe),
            "reason": r.payload.get("change_reason", "根据事件提出的整理建议")}
            for r in rows]}}


def evidence(db, book_id, proposal, safe):
    refs = {(ref["note_id"], ref["version"]) for node in proposal.after.get("nodes", []) for ref in node["evidence"]}
    result = []
    for note_id, version in sorted(refs):
        row = db.query(ReadingNoteRevision).filter_by(book_id=book_id, note_id=note_id, version=version).first()
        if row and row.knowledge_idx < safe:
            result.append({"title": row.content["title"], "claim": row.content.get("claim"), "quotes": row.content.get("evidence", [])})
    return result


def decide(db, book_id, proposal_id, accept):
    safe = notes.gate(db, book_id)
    row = db.query(Proposal).filter(Proposal.id == proposal_id, Proposal.book_id == book_id,
                                    Proposal.safe_chapter <= safe).first()
    if row is None:
        raise NoteError("not_found", "当前阅读范围内没有该建议")
    if row.status != "pending":
        return receipt(row)
    # Claim and canonical write share one transaction, including failures.
    claimed = db.query(Proposal).filter_by(id=row.id, status="pending").update(
        {"status": "accepted" if accept else "rejected"}, synchronize_session=False)
    if not claimed:
        db.rollback()
        raise NoteError("version_conflict", "此建议已由其他窗口处理，请刷新")
    try:
        if accept:
            for source in evidence(db, book_id, row, safe):
                notes.validate_evidence(db, book_id, [Evidence.model_validate(q) for q in source["quotes"]], safe)
            row.result = notes.upsert(db, book_id, TypeAdapter(NoteWrite).validate_python(row.payload),
                                     safe_override=safe, commit=False)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(row)
    if accept:
        from app.services import note_embedding_service
        note_embedding_service.notify()
    return receipt(row)
