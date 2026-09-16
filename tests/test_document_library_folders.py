"""Library folder persistence, routing, and ownership checks."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.database as database
import routes.document_routes as routes
import src.document_library as library
from core.database import Document


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    yield factory
    engine.dispose()


@pytest.mark.parametrize("path", ["/outside", "../secret", "a/../b", "a//b", "a/./b", "C:/bad", "bad.", "a/NUL.txt", "a/COM1", "a/COM\u00b9", "bad ", "a\x00b", None])
def test_folder_rejects_unsafe_paths(path):
    with pytest.raises(ValueError):
        library.validate_folder_path(path)


def test_nested_folders_persist_and_moves_are_owner_scoped(session_factory):
    with session_factory() as db:
        doc = Document(id="alice-doc", owner="alice", title="Note", current_content="body")
        db.add(doc)
        db.commit()
        assert library.create_folder(db, "alice", "Projects\\Research") == "Projects/Research"
        assert library.create_folder(db, "alice", "projects/research") == "Projects/Research"
        library.create_folder(db, "bob", "Private")
        library.move_document(db, "alice", doc.id, "Projects/Research/Drafts")
        db.commit()
        assert doc.folder == "Projects/Research/Drafts"
        with pytest.raises(HTTPException) as exc:
            library.move_document(db, "bob", doc.id, "Stolen")
        assert exc.value.status_code == 404
        assert library.list_folders(db, "alice") == [
            {"path": "Projects"}, {"path": "Projects/Research"}, {"path": "Projects/Research/Drafts"},
        ]
        library.move_document(db, "alice", doc.id, "")
        db.commit()
        assert doc.folder == ""
        assert library.list_folders(db, "bob") == [{"path": "Private"}]


def test_folder_routes_create_filter_and_move(session_factory, monkeypatch):
    monkeypatch.setattr(routes, "SessionLocal", session_factory)
    monkeypatch.setattr(routes, "get_current_user", lambda request: "alice")
    monkeypatch.setattr("src.auth_helpers.require_privilege", lambda request, privilege: "alice")
    app = FastAPI()
    app.include_router(routes.setup_document_routes(None))
    with session_factory() as db:
        db.add_all([
            Document(id="root", owner="alice", title="Root", language="markdown"),
            Document(id="nested", owner="alice", title="Nested", folder="Work/Notes", language="text"),
            Document(id="foreign", owner="bob", title="Private", folder="Work/Notes"),
        ])
        db.commit()
    with TestClient(app) as client:
        made = client.post("/api/documents/folders", json={"path": "Empty/Subfolder"})
        assert made.status_code == 200
        assert made.json()["path"] == "Empty/Subfolder"
        assert client.post("/api/documents/folders", json={"path": "empty/subfolder"}).json()["path"] == "Empty/Subfolder"
        assert {row["path"] for row in client.get("/api/documents/folders").json()["folders"]} == {
            "Empty", "Empty/Subfolder", "Work", "Work/Notes",
        }
        assert client.get("/api/documents/library").json()["total"] == 2
        root = client.get("/api/documents/library", params={"folder": ""}).json()
        assert [doc["id"] for doc in root["documents"]] == ["root"]
        nested = client.get("/api/documents/library", params={"folder": "Work/Notes"}).json()
        assert [doc["id"] for doc in nested["documents"]] == ["nested"]
        assert nested["languages"] == {"text": 1}
        moved = client.patch("/api/document/nested", json={"folder": "New/Subfolder"})
        assert moved.status_code == 200
        assert moved.json()["folder"] == "New/Subfolder"
        assert client.patch("/api/document/foreign", json={"folder": ""}).status_code == 404
        assert client.patch("/api/document/root", json={"folder": "../bad"}).status_code == 400
        assert client.get("/api/documents/library", params={"folder": "../bad"}).status_code == 400
        created = client.post("/api/document", json={"title": "New", "language": "markdown", "content": "# New", "folder": "Created/Subfolder"})
        assert created.status_code == 200, created.text
        assert created.json()["folder"] == "Created/Subfolder"


def test_old_document_database_migration_is_idempotent(monkeypatch):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE documents (id VARCHAR PRIMARY KEY)"))
        conn.execute(text("INSERT INTO documents (id) VALUES ('old')"))
    monkeypatch.setattr(database, "engine", engine)
    database._migrate_add_document_folder_column()
    database._migrate_add_document_folder_column()
    with engine.connect() as conn:
        assert conn.execute(text("SELECT folder FROM documents WHERE id='old'")).scalar() == ""
        assert "folder" in {column["name"] for column in inspect(conn).get_columns("documents")}
    engine.dispose()


def test_pdf_import_uses_requested_folder(session_factory, monkeypatch, tmp_path):
    upload_id = "0123456789abcdef0123456789abcdef.pdf"
    pdf = tmp_path / upload_id
    pdf.write_bytes(b"%PDF-1.7")
    monkeypatch.setattr(routes, "SessionLocal", session_factory)
    monkeypatch.setattr("src.auth_helpers.require_privilege", lambda request, privilege: "alice")
    monkeypatch.setattr("src.pdf_forms.has_form_fields", lambda path: False)
    monkeypatch.setattr("src.document_processor._process_pdf", lambda path, owner: "PDF body")

    def create_pdf(**kwargs):
        with session_factory() as db:
            db.add(Document(id="new-pdf", title=kwargs["title"], current_content="PDF wrapper"))
            db.commit()
        return "new-pdf"

    monkeypatch.setattr("src.pdf_form_doc.create_plain_pdf_document", create_pdf)
    uploads = SimpleNamespace(
        upload_dir=str(tmp_path),
        save_upload=lambda file, client_ip, owner: {"id": upload_id, "original_name": "Packet.pdf"},
        resolve_upload=lambda upload_id, owner, auth_manager: {"path": str(pdf)},
    )
    app = FastAPI()
    app.include_router(routes.setup_document_routes(None, uploads))
    with TestClient(app) as client:
        result = client.post("/api/documents/import-pdf", data={"folder": "Forms/Incoming"}, files={"file": ("Packet.pdf", b"%PDF-1.7", "application/pdf")})
    assert result.status_code == 200, result.text
    assert result.json()["folder"] == "Forms/Incoming"
    with session_factory() as db:
        assert db.get(Document, "new-pdf").owner == "alice"


def test_folder_endpoints_reject_unauthenticated_requests(session_factory, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("LOCALHOST_BYPASS", "false")
    monkeypatch.setattr(routes, "SessionLocal", session_factory)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(routes.setup_document_routes(None))
    with TestClient(app) as client:
        assert client.get("/api/documents/folders").status_code == 401
        assert client.post("/api/documents/folders", json={"path": "Blocked"}).status_code == 401
    with session_factory() as db:
        assert db.query(database.DocumentFolder).count() == 0
