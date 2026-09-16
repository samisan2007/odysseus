"""Owner-scoped library folders and reusable document tags."""

import re
import uuid

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

from core.database import Document, DocumentFolder, DocumentTag, document_tag_links


def validate_folder_path(path: str) -> str:
    """Accept portable relative folder paths; the empty string is library root."""
    if not isinstance(path, str):
        raise ValueError("Folder must be a relative path")
    path = path.replace("\\", "/")
    if not path:
        return ""
    if len(path) > 500:
        raise ValueError("Folder path is too long")
    for part in path.split("/"):
        if (not part or part in {".", ".."} or part != part.strip()
                or part.endswith(".") or len(part) > 100
                or re.search(r'[<>:"|?*\x00-\x1f]', part)
                or re.match(r"(?i)^(con|prn|aux|nul|com[1-9\u00b9\u00b2\u00b3]|lpt[1-9\u00b9\u00b2\u00b3])(?:\.|$)", part)):
            raise ValueError("Use folder names without traversal, reserved names, or unsafe characters")
    return path


def _folder_query(db, user):
    from src.auth_helpers import _auth_disabled

    query = db.query(DocumentFolder)
    if user:
        return query.filter(DocumentFolder.owner == user)
    if user != "" and not _auth_disabled():
        raise HTTPException(403, "Authentication required")
    return query


def list_folders(db, user) -> list[dict]:
    from routes.document_helpers import _owner_session_filter

    paths = {row.path for row in _folder_query(db, user).all()}
    rows = _owner_session_filter(db.query(Document.folder), user).distinct().all()
    for (folder,) in rows:
        if folder:
            parts = validate_folder_path(folder).split("/")
            paths.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
    return [{"path": path} for path in sorted(paths, key=lambda path: (path.casefold(), path))]


def create_folder(db, user, path: str) -> str:
    """Add a folder and its ancestors. The caller commits the transaction."""
    path = validate_folder_path(path)
    _folder_query(db, user)
    if not path:
        return ""
    # Keep one canonical spelling for case-insensitive folder names.
    existing = {row["path"].casefold(): row["path"] for row in list_folders(db, user)}
    canonical = ""
    for part in path.split("/") if path else []:
        ancestor = f"{canonical}/{part}" if canonical else part
        canonical = existing.get(ancestor.casefold(), ancestor)
        if ancestor.casefold() not in existing:
            db.add(DocumentFolder(owner=user or "", path=canonical))
            existing[ancestor.casefold()] = canonical
    if path:
        db.flush()
    return canonical


def move_document(db, user, document_id: str, folder: str) -> Document:
    from routes.document_helpers import _verify_doc_owner

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(404, "Document not found")
    _verify_doc_owner(db, doc, user)
    doc.folder = create_folder(db, user, folder)
    return doc


def _tag_query(db, user):
    from src.auth_helpers import _auth_disabled

    if user is None and not _auth_disabled():
        raise HTTPException(403, "Authentication required")
    return db.query(DocumentTag).filter(DocumentTag.owner == (user or ""))


def _tag_name(name):
    if not isinstance(name, str):
        raise HTTPException(400, "Tag name must be text")
    name = " ".join(name.split())
    if not 1 <= len(name) <= 64 or re.search(r"[\x00-\x1f\x7f]", name):
        raise HTTPException(400, "Tag names must contain 1 to 64 characters without control characters")
    return name


def list_tags(db, user) -> list[dict]:
    """List reusable tags and their active, unarchived document counts."""
    from routes.document_helpers import _owner_session_filter

    counts = _owner_session_filter(
        db.query(document_tag_links.c.tag_id, func.count(Document.id).label("document_count"))
        .join(Document, Document.id == document_tag_links.c.document_id)
        .filter(Document.is_active.is_(True), or_(Document.archived.is_(False), Document.archived.is_(None))),
        user,
    ).group_by(document_tag_links.c.tag_id).subquery()
    rows = (_tag_query(db, user)
            .outerjoin(counts, counts.c.tag_id == DocumentTag.id)
            .add_columns(func.coalesce(counts.c.document_count, 0))
            .order_by(DocumentTag.name_key, DocumentTag.id).all())
    return [{"id": tag.id, "name": tag.name, "document_count": count} for tag, count in rows]


def create_tag(db, user, name) -> DocumentTag:
    """Reuse case-insensitive names. The caller commits the transaction."""
    name = _tag_name(name)
    query = _tag_query(db, user).filter(DocumentTag.name_key == name.casefold())
    existing = query.first()
    if existing:
        return existing
    tag = DocumentTag(id=str(uuid.uuid4()), owner=user or "", name=name, name_key=name.casefold())
    try:
        with db.begin_nested():
            db.add(tag)
            db.flush()
    except IntegrityError:
        # Concurrent creators still return the same owner-scoped label.
        existing = query.first()
        if existing:
            return existing
        raise
    return tag


def _get_tag(db, user, tag_id) -> DocumentTag:
    tag = _tag_query(db, user).filter(DocumentTag.id == tag_id).first()
    if not tag:
        raise HTTPException(404, "Tag not found")
    return tag


def rename_tag(db, user, tag_id, name) -> DocumentTag:
    tag = _get_tag(db, user, tag_id)
    name = _tag_name(name)
    duplicate = (_tag_query(db, user).filter(
        DocumentTag.name_key == name.casefold(), DocumentTag.id != tag.id,
    ).first())
    if duplicate:
        raise HTTPException(409, "A tag with that name already exists")
    try:
        with db.begin_nested():
            tag.name, tag.name_key = name, name.casefold()
            db.flush()
    except IntegrityError as exc:
        raise HTTPException(409, "A tag with that name already exists") from exc
    return tag


def delete_tag(db, user, tag_id) -> None:
    db.delete(_get_tag(db, user, tag_id))
    db.flush()


def resolve_tag_ids(db, user, tag_ids) -> list[DocumentTag]:
    """Validate the entire selection before changing any assignment."""
    if not isinstance(tag_ids, list) or any(not isinstance(tag_id, str) or not tag_id for tag_id in tag_ids):
        raise HTTPException(400, "tag_ids must be a list of tag IDs")
    ids = set(tag_ids)
    tags = _tag_query(db, user).filter(DocumentTag.id.in_(ids)).all() if ids else []
    if len(tags) != len(ids):
        raise HTTPException(404, "One or more tags were not found")
    return tags


def set_document_tags(db, user, doc, tag_ids) -> None:
    from routes.document_helpers import _verify_doc_owner

    _verify_doc_owner(db, doc, user or None)
    doc.tags = resolve_tag_ids(db, user, tag_ids)


def document_tags(doc) -> list[dict]:
    return [{"id": tag.id, "name": tag.name} for tag in sorted(
        getattr(doc, "tags", None) or [], key=lambda tag: (tag.name.casefold(), tag.id),
    )]
