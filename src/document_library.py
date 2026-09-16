"""Owner-scoped nested library folders."""

import re

from fastapi import HTTPException

from core.database import Document, DocumentFolder


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
