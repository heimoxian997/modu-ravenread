"""上传草稿业务。

草稿源文件放在 data/drafts/{token}/source.txt，确认入库后复制到
data/uploads/{book_id}/source.txt 并删除草稿目录。
"""

from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.common.exceptions import BusinessException
from app.config import DATA_DIR, DRAFTS_DIR, MAX_FILE_SIZE, UPLOADS_DIR
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.draft import Draft
from app.schemas.draft import ConfirmRequest, DraftUploadResponse, ParseRequest
from app.services import boundary_service, parser_service, text_io_service

_TOKEN_RE = re.compile(r"^[a-f0-9]{32}$")
_CHUNK_SIZE = 1024 * 1024


def _validate_token(token: str) -> str:
    if not _TOKEN_RE.fullmatch(token or ""):
        raise BusinessException(message="草稿令牌无效", code=4041)
    return token


def _draft_dir(token: str) -> Path:
    _validate_token(token)
    path = (DRAFTS_DIR / token).resolve()
    if DRAFTS_DIR.resolve() not in path.parents:
        raise BusinessException(message="草稿路径无效", code=4041)
    return path


def _get_draft(db: Session, token: str) -> Draft:
    _validate_token(token)
    draft = db.query(Draft).filter(Draft.token == token).first()
    if not draft:
        raise BusinessException(message="草稿不存在或已过期，请重新上传", code=4040)
    return draft


def _draft_source_path(draft: Draft) -> Path:
    path = (DATA_DIR / draft.raw_path).resolve()
    if path != (DATA_DIR / "drafts" / draft.token / "source.txt").resolve():
        raise BusinessException(message="草稿文件路径异常", code=4041)
    return path


def create_draft(db: Session, file: UploadFile):
    """接收 .txt 文件，流式落盘、探测编码、规范化后写入草稿目录。"""
    if not file.filename:
        raise BusinessException(message="文件名不能为空", code=4000)

    raw_name = file.filename.replace("\\", "/").rsplit("/", 1)[-1]
    if Path(raw_name).suffix.lower() != ".txt":
        raise BusinessException(message="只支持 .txt 文件", code=4000)
    filename = text_io_service.safe_filename(raw_name)
    if file.size and file.size > MAX_FILE_SIZE:
        raise BusinessException(
            message=f"文件不能超过 {MAX_FILE_SIZE // 1024 // 1024}MB", code=4000
        )

    token = uuid.uuid4().hex
    draft_dir = _draft_dir(token)
    draft_dir.mkdir(parents=True, exist_ok=False)
    temp_path = draft_dir / "upload.tmp"
    source_path = draft_dir / "source.txt"

    try:
        written = 0
        with temp_path.open("wb") as target:
            while True:
                chunk = file.file.read(_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_FILE_SIZE:
                    raise BusinessException(
                        message=f"文件不能超过 {MAX_FILE_SIZE // 1024 // 1024}MB",
                        code=4000,
                    )
                target.write(chunk)

        if written == 0:
            raise BusinessException(message="文件内容为空，请选择有效的 txt", code=4001)

        raw = temp_path.read_bytes()
        text, encoding = text_io_service.prepare_text(raw)
        source_path.write_bytes(text.encode("utf-8"))
        temp_path.unlink(missing_ok=True)

        draft = Draft(
            token=token,
            filename=filename,
            encoding=encoding,
            char_count=len(text),
            raw_path=f"drafts/{token}/source.txt",
        )
        db.add(draft)
        db.commit()
        db.refresh(draft)
    except Exception:
        shutil.rmtree(draft_dir, ignore_errors=True)
        raise

    return DraftUploadResponse(
        draft_token=draft.token,
        filename=draft.filename,
        encoding=draft.encoding,
        char_count=draft.char_count,
        head=text[:500],
    )


def parse_draft(db: Session, token: str, payload: ParseRequest):
    """推导章节规则并解析草稿全文。"""
    draft = _get_draft(db, token)
    source_path = _draft_source_path(draft)
    if not source_path.exists():
        raise BusinessException(message="草稿源文件不存在，请重新上传", code=4042)
    text = source_path.read_text(encoding="utf-8")

    result = parser_service.parse_novel(
        text,
        chapter_samples=payload.chapter_samples,
        extra_samples=payload.extra_samples,
        fallback=payload.fallback,
    )

    draft.chapter_samples = payload.chapter_samples
    draft.extra_samples = payload.extra_samples
    draft.parsed = result
    db.commit()
    return result


def get_draft_content(db: Session, token: str, start: int, end: int):
    """读取草稿源文件片段。"""
    draft = _get_draft(db, token)
    if start >= end:
        raise BusinessException(message="start 必须小于 end", code=4006)
    if end > draft.char_count:
        raise BusinessException(
            message=f"读取范围越界，草稿共 {draft.char_count} 个字符", code=4006
        )

    source_path = _draft_source_path(draft)
    if not source_path.exists():
        raise BusinessException(message="草稿源文件不存在，请重新上传", code=4042)
    text = source_path.read_text(encoding="utf-8")
    return {"text": text[start:end]}


def confirm_draft(db: Session, token: str, payload: ConfirmRequest):
    """校验边界、复制源文件并写入正式书库。"""
    draft = _get_draft(db, token)
    source_path = _draft_source_path(draft)
    if not source_path.exists():
        raise BusinessException(message="草稿源文件不存在，请重新上传", code=4042)
    text = source_path.read_text(encoding="utf-8")

    prepared = boundary_service.validate_boundaries(payload.chapters, len(text))
    book_meta = payload.book

    book = Book(
        title=book_meta.title,
        author=book_meta.author,
        synopsis=book_meta.synopsis,
        protagonists=book_meta.protagonists,
        front_matter=boundary_service.front_matter_of(text, prepared[0]["start"]),
        filename=draft.filename,
        encoding=draft.encoding,
        char_count=len(text),
        chapter_count=len(prepared),
        main_chapter_count=sum(1 for item in prepared if item["kind"] == "main"),
    )
    db.add(book)
    db.flush()  # 取得 book.id，用于上传目录

    upload_dir = UPLOADS_DIR / str(book.id)
    upload_dir.mkdir(parents=True, exist_ok=True)
    book_source = upload_dir / "source.txt"
    try:
        shutil.copyfile(source_path, book_source)
        for index, item in enumerate(prepared):
            db.add(
                Chapter(
                    book_id=book.id,
                    idx=index,
                    title=item["title"],
                    kind=item["kind"],
                    char_start=item["start"],
                    char_end=item["end"],
                    word_count=item["word_count"],
                )
            )
        db.commit()
    except Exception:
        db.rollback()
        shutil.rmtree(upload_dir, ignore_errors=True)
        raise

    db.refresh(book)

    # 入库成功后清理草稿；清理失败不影响已经保存的书籍。
    try:
        db.delete(draft)
        db.commit()
        shutil.rmtree(_draft_dir(token), ignore_errors=True)
    except Exception:
        db.rollback()

    return {"book_id": book.id}


def cleanup_expired_drafts(db: Session, ttl_hours: int = 24) -> int:
    """启动时清理超过 24 小时的草稿，避免本机数据目录无限增长。"""
    cutoff = datetime.now() - timedelta(hours=ttl_hours)
    expired = db.query(Draft).filter(Draft.create_time < cutoff).all()
    count = 0
    for draft in expired:
        token = draft.token
        db.delete(draft)
        db.commit()
        shutil.rmtree(_draft_dir(token), ignore_errors=True)
        count += 1
    return count
