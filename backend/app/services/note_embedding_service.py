"""Incremental, restartable note embeddings. Retrieval only scores caller-gated texts.

SQLite stores small dense vectors alongside immutable revision segments, avoiding
another Qdrant client and allowing the spoiler gate to run before similarity search.
"""
import hashlib
import importlib.util
import json
import logging
import math
import queue
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import exists
from app.config import settings
from app.database import SessionLocal
from app.models.reading_note import ReadingNoteRevision as Revision
from app.models.note_embedding import NoteSearchDocument as Document, NoteEmbedding as Embedding
from app.services import embedding_service, note_graph_service as graph

logger = logging.getLogger(__name__)
MIN_SIMILARITY = 0.45
QUERY_WAIT_SECONDS = 1.0
_wake = threading.Event()
_stop = threading.Event()
_thread = None
_lifecycle_lock = threading.Lock()
_tickets_lock = threading.Lock()
_tickets = {}
_queries = queue.Queue(maxsize=64)


def text_hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def model_key():
    """Model/config/weight changes invalidate the cache without loading torch."""
    path = embedding_service.get_model_dir()
    files = []
    for name in (*embedding_service.REQUIRED_MODEL_FILES, *embedding_service.MODEL_WEIGHT_FILES):
        try:
            stat = (path / name).stat()
            files.append((name, stat.st_size, stat.st_mtime_ns))
        except OSError:
            pass
    return text_hash(json.dumps(["note-dense-v1", settings.EMBEDDING_MODEL, str(path.resolve()),
        settings.EMBEDDING_MAX_LENGTH, settings.EMBEDDING_USE_FP16, files], sort_keys=True))


def available():
    # Never download weights or initialize CUDA as a side effect of writing notes.
    return embedding_service.is_model_complete() and all(
        importlib.util.find_spec(module) is not None for module in ("torch", "FlagEmbedding"))


def segments(row):
    """Only retrieval text affects hashes; evidence versions, edges and reasons don't."""
    content = row.content
    title = content["title"]
    if row.kind == "entry":
        return {"event": "\n".join([title, "、".join(content["entities"]), content["claim"],
            content["basis"], content.get("perspective", ""),
            json.dumps(content["payload"], ensure_ascii=False, sort_keys=True)])}
    result = {"overview": "\n".join([title, content["overview"], content["stage"]])}
    for node in graph.nodes(content):
        result["node:"+node["node_id"]] = "\n".join([title, node.get("branch", ""), node["conclusion"]])
    return result


def stage_revision(db, row):
    """Enqueue durable documents in the same transaction as the new revision."""
    db.flush()
    for segment, text in segments(row).items():
        db.add(Document(revision_id=row.id, book_id=row.book_id, segment=segment,
                        text_hash=text_hash(text), text=text))


def backfill(db, limit=64):
    rows = db.query(Revision).filter(~exists().where(Document.revision_id == Revision.id)).order_by(Revision.id).limit(limit).all()
    for row in rows:
        stage_revision(db, row)
    db.commit()
    return len(rows)


def _normalize(vector):
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(v*v for v in values))
    if not values or not math.isfinite(norm) or norm == 0:
        raise ValueError("invalid embedding vector")
    return [v/norm for v in values]


def _cache_rows(db, key, hashes):
    result = {}
    hashes = list(set(hashes))
    for offset in range(0, len(hashes), 400):
        result.update({r.text_hash:r for r in db.query(Embedding).filter(
            Embedding.model_key == key, Embedding.text_hash.in_(hashes[offset:offset+400])).all()})
    return result


def _encode(db, key, texts):
    """Inference outside a write transaction; errors remain retryable after restart."""
    hashes = [text_hash(text) for text in texts]
    existing = _cache_rows(db, key, hashes)
    now = time.time()
    todo = {digest:text for digest,text in zip(hashes,texts) if digest not in existing or (
        existing[digest].vector is None and existing[digest].retry_at <= now)}
    if not todo:
        return
    db.rollback()
    try:
        docs = embedding_service.encode_documents(list(todo.values()), batch_size=8)
        if len(docs) != len(todo):
            raise ValueError("embedding count mismatch")
        vectors = [_normalize(doc.dense) for doc in docs]
        if len({len(v) for v in vectors}) != 1:
            raise ValueError("embedding dimension mismatch")
        failure = None
    except Exception as exc:
        # Avoid putting provider messages or text fragments into logs/status.
        failure = type(exc).__name__
        vectors = [None] * len(todo)
        logger.warning("note embedding batch failed: %s", failure)
    if model_key() != key:
        return
    for digest, vector in zip(todo, vectors):
        row = db.query(Embedding).filter_by(model_key=key, text_hash=digest).first()
        if row is None:
            row = Embedding(model_key=key, text_hash=digest, attempts=0)
            db.add(row)
        row.attempts += 1
        row.vector = vector
        row.completed_at = time.time() if vector else None
        row.error = failure
        row.retry_at = time.time() + min(300, 5 * 2**min(row.attempts, 6)) if failure else 0
    db.commit()


def index_batch(db, batch_size=8):
    key = model_key()
    ready_or_backoff = exists().where(Embedding.model_key == key,
        Embedding.text_hash == Document.text_hash,
        (Embedding.vector.is_not(None)) | (Embedding.retry_at > time.time()))
    documents = db.query(Document).filter(~ready_or_backoff).order_by(Document.id).limit(batch_size).all()
    if documents:
        _encode(db, key, [d.text for d in documents])
    return len(documents)


@dataclass
class QueryTicket:
    key: str
    text: str
    done: threading.Event = field(default_factory=threading.Event)


def notify():
    _wake.set()


def _prune_queries(db):
    # Query vectors contain only a hash and floats. Bound even this derived cache.
    unreferenced = ~exists().where(Document.text_hash == Embedding.text_hash)
    old = db.query(Embedding.id).filter(unreferenced).order_by(Embedding.id.desc()).offset(512).all()
    ids = [row_id for (row_id,) in old]
    for offset in range(0,len(ids),400):
        db.query(Embedding).filter(Embedding.id.in_(ids[offset:offset+400])).delete(synchronize_session=False)
    db.commit()


def _worker():
    while not _stop.is_set():
        did_work = False
        try:
            with SessionLocal() as db:
                # Queries take priority between small document batches.
                try:
                    ticket = _queries.get_nowait()
                except queue.Empty:
                    ticket = None
                if ticket:
                    try:
                        if available() and ticket.key == model_key():
                            _encode(db, ticket.key, [ticket.text])
                            _prune_queries(db)
                    finally:
                        ticket.done.set()
                        with _tickets_lock:
                            _tickets.pop((ticket.key, text_hash(ticket.text)), None)
                    did_work = True
                if _stop.is_set():
                    break
                did_work = bool(backfill(db)) or did_work
                if available():
                    did_work = bool(index_batch(db)) or did_work
        except Exception as exc:
            logger.warning("note index worker failed: %s", type(exc).__name__)
        if not did_work:
            _wake.wait(5)
        _wake.clear()


def start():
    global _thread
    with _lifecycle_lock:
        if _thread and _thread.is_alive():
            _stop.clear()
            notify()
            return
        _stop.clear()
        _thread = threading.Thread(target=_worker, name="note-index-worker", daemon=True)
        _thread.start()


def stop():
    _stop.set()
    notify()
    if _thread:
        _thread.join(timeout=5)


def _request_query(key, query):
    identity = (key, text_hash(query))
    with _tickets_lock:
        ticket = _tickets.get(identity)
        if ticket is None:
            ticket = QueryTicket(key, query)
            try:
                _queries.put_nowait(ticket)
            except queue.Full:
                return
            _tickets[identity] = ticket
    notify()
    ticket.done.wait(QUERY_WAIT_SECONDS)


def semantic_scores(db, query, allowed, cutoff=None, expected_model=None, enabled=None):
    """Allowed contains only safe active revision segments, already filtered by caller.

    Freeze completed_at across cursor pages: concurrent indexing cannot reorder them.
    No full-book vector search or future-version similarity ranking is performed.
    """
    key = model_key()
    metadata = {"semantic": "unavailable", "model_key": key, "query_ready": False}
    if expected_model and key != expected_model:
        from app.services.note_service import NoteError
        raise NoteError("invalid_cursor", "嵌入模型已变化，请重新查询")
    if not query or not allowed:
        metadata["semantic"] = "not_needed"
        return {}, metadata, cutoff or time.time()
    cutoff = cutoff if cutoff is not None else time.time()
    digests = {identity:text_hash(text) for identity,text in allowed.items()}
    cached = _cache_rows(db, key, digests.values())
    ready = {digest:r for digest,r in cached.items() if r.vector and r.completed_at <= cutoff}
    metadata["indexed_segments"] = sum(d in ready for d in digests.values())
    metadata["pending_segments"] = len(digests) - metadata["indexed_segments"]
    query_hash = text_hash(query)
    query_row = _cache_rows(db, key, [query_hash]).get(query_hash)
    if enabled is True and (query_row is None or not query_row.vector):
        from app.services.note_service import NoteError
        raise NoteError("invalid_cursor", "查询向量缓存已失效，请重新查询")
    if ready and enabled is not False and (query_row is None or not query_row.vector) and available():
        _request_query(key, query)
        db.expire_all()
        query_row = _cache_rows(db, key, [query_hash]).get(query_hash)
    if enabled is False or query_row is None or not query_row.vector:
        metadata["semantic"] = "warming" if ready and available() else ("building" if available() else "unavailable")
        return {}, metadata, cutoff
    vector = query_row.vector
    metadata["query_ready"] = True
    scores = {}
    for identity,digest in digests.items():
        row = ready.get(digest)
        if row and len(row.vector) == len(vector):
            score = sum(a*b for a,b in zip(vector,row.vector))
            if score >= MIN_SIMILARITY:
                scores[identity] = score
    metadata["semantic"] = "ready" if not metadata["pending_segments"] else "partial"
    return scores, metadata, cutoff


def status(db, book_id):
    """Local diagnostics; does not load the embedding model."""
    key = model_key()
    hashes = {h for (h,) in db.query(Document.text_hash).filter(Document.book_id == book_id).distinct()}
    cached = _cache_rows(db, key, hashes)
    ready = sum(bool(r.vector) for r in cached.values())
    failed = sum(not r.vector and bool(r.error) for r in cached.values())
    return {"available": available(), "worker_running": bool(_thread and _thread.is_alive()),
        "model_key":key, "segments":len(hashes), "ready":ready, "failed":failed,
        "pending":len(hashes)-ready-failed,
        "unprepared_revisions":db.query(Revision).filter(Revision.book_id==book_id,
            ~exists().where(Document.revision_id==Revision.id)).count()}
