"""Full-main-text reads exclusively for role preparation, never for companion tools."""

import hashlib
import json
import uuid

from sqlalchemy import func, literal

from app.common.exceptions import BusinessException
from app.config import UPLOADS_DIR
from app.models.book import Book
from app.models.chapter import Chapter
from app.models.character import Character
from app.models.character_alias import CharacterAlias
from app.models.role_preparation import RoleCharacter
from app.schemas.role_preparation import RoleScene, RoleTarget
from app.services.chunk_service import strip_title_prefix

SCENE_VERSION = "role_chapter_windows_v1"


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def require_book(db, book_id):
    book = db.get(Book, book_id)
    if not book:
        raise BusinessException("书籍不存在或已被删除", code=4040)
    return book


def resolve_target(db, book_id, name):
    require_book(db, book_id)
    character = db.query(Character).filter_by(book_id=book_id, canonical_name=name).first()
    if character is None:
        candidates = db.query(Character).join(CharacterAlias, CharacterAlias.character_id == Character.id).filter(
            Character.book_id == book_id, CharacterAlias.book_id == book_id,
            CharacterAlias.alias == name).all()
        if len(candidates) > 1:
            raise BusinessException("称呼对应多个人物，请选择规范名：" + "、".join(
                c.canonical_name for c in candidates), code=4090)
        character = candidates[0] if candidates else None
    canonical = character.canonical_name if character else name
    aliases = []
    if character:
        unambiguous = db.query(CharacterAlias.alias).filter(
            CharacterAlias.book_id == book_id).group_by(CharacterAlias.alias).having(
                func.count(func.distinct(CharacterAlias.character_id)) == 1).subquery()
        aliases = [row.alias for row in db.query(CharacterAlias).filter(
            CharacterAlias.book_id == book_id, CharacterAlias.character_id == character.id,
            CharacterAlias.alias.in_(db.query(unambiguous.c.alias))).order_by(
                CharacterAlias.occurrence_count.desc(), CharacterAlias.alias).all()
                   if len(row.alias) <= 100][:30]
    if len(canonical) > 100:
        raise BusinessException("人物名称过长", code=4000)
    return RoleTarget(book_key=f"book:{book_id}", name=canonical, aliases=aliases)


def get_role(db, book_id, role_id):
    require_book(db, book_id)
    role = db.query(RoleCharacter).filter_by(id=role_id, book_id=book_id).first()
    if not role:
        raise BusinessException("角色不存在", code=4040)
    return role


def find_or_create_role(db, book_id, target):
    role = db.query(RoleCharacter).filter_by(book_id=book_id, canonical_name=target.name).first()
    if not role:
        role = RoleCharacter(book_id=book_id, role_key="role_" + uuid.uuid4().hex,
                             canonical_name=target.name)
        db.add(role)
        db.flush()
    return role


def characters(db, book_id, query="", offset=0, limit=20):
    require_book(db, book_id)
    names = db.query(Character.canonical_name.label("name"), Character.mention_count.label("chapters")).filter(Character.book_id == book_id)
    stored = db.query(RoleCharacter.canonical_name.label("name"), literal(0).label("chapters")).filter(RoleCharacter.book_id == book_id)
    combined = names.union_all(stored).subquery()
    # Chapter mentions approximate available material without reading book text or
    # invoking a model. Group before pagination to deduplicate prepared roles.
    richness = func.max(combined.c.chapters).label("mention_count")
    rows = db.query(combined.c.name, richness).group_by(combined.c.name)
    if query:
        rows = rows.filter(combined.c.name.contains(query, autoescape=True))
    total = rows.count()
    return {"items": [{"name": name, "mention_count": count} for name, count in rows.order_by(
                richness.desc(), combined.c.name).offset(offset).limit(limit)],
            "total": total, "offset": offset, "limit": limit, "manual_name_allowed": True,
            "scope": "full_main_text"}


def read_source(db, book_id):
    require_book(db, book_id)
    path = UPLOADS_DIR / str(book_id) / "source.txt"
    try:
        # Read afresh: cache mtimes alone cannot detect replacement with the same timestamp.
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise BusinessException("正文文件不可读，请检查书籍源文件", code=4000) from None
    chapters = db.query(Chapter).filter_by(book_id=book_id).order_by(Chapter.idx).all()
    metadata = [(c.idx, c.title, c.kind, c.char_start, c.char_end) for c in chapters]
    previous = 0
    for chapter in chapters:
        if not previous <= chapter.char_start < chapter.char_end <= len(source):
            raise BusinessException("章节范围无效，请先核对章节划分", code=4000)
        previous = chapter.char_end
    stamp = digest({"source": hashlib.sha256(source.encode()).hexdigest(), "chapters": metadata})
    return source, chapters, stamp


def source_hash(db, book_id):
    return read_source(db, book_id)[2]


def load_scenes(db, book_id, batch_chars=8000):
    source, chapters, stamp = read_source(db, book_id)
    window = min(4000, batch_chars)
    overlap = min(300, window // 4)
    scenes = []
    for chapter in chapters:
        if chapter.kind != "main":
            continue
        raw = source[chapter.char_start:chapter.char_end]
        body, prefix = strip_title_prefix(raw, chapter.title)
        cursor = 0
        while cursor < len(body):
            end = min(cursor + window, len(body))
            if end < len(body):
                boundary = body.rfind("\n", cursor + window // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            text = body[cursor:end]
            if text.strip():
                start = chapter.char_start + prefix + cursor
                scenes.append(RoleScene(
                    book_key=f"book:{book_id}", scene_id=f"ch{chapter.idx + 1}:{start}:{start + len(text)}",
                    chapter_number=chapter.idx + 1, char_start=start, text=text,
                ))
            if end == len(body):
                break
            cursor = max(cursor + 1, end - overlap)
    if not scenes:
        raise BusinessException("没有可用于角色准备的正文", code=4000)
    return scenes, stamp
