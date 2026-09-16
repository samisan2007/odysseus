"""Exercise real folder tool calls, parsing and owner boundaries."""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import src.database as legacy_database
from src.agent_tools.document_tools import CreateDocumentTool, ManageDocumentTool, set_active_document
from src.tool_schemas import function_call_to_tool_block
from src.tool_capabilities import capabilities_for_action, ToolEffect


def test_agent_creates_reads_and_moves_nested_library_documents(monkeypatch):
    engine = create_engine("sqlite://")
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(legacy_database, "SessionLocal", factory)
    monkeypatch.setattr("src.agent_tools.document_tools._missing_document_upload", lambda *a: None)
    with factory() as db:
        db.add(database.Session(id="chat", owner="alice", name="Chat", endpoint_url="http://localhost:11434", model="test"))
        db.commit()
    ctx = {"owner": "alice", "session_id": "chat"}
    manage = ManageDocumentTool()
    def call(args, context=ctx):
        return asyncio.run(manage.execute(json.dumps(args), context))
    try:
        assert call({"action": "create_folder", "folder": "Work/Notes"})["exit_code"] == 0
        assert call({"action": "list_folders"})["folders"] == [{"path": "Work"}, {"path": "Work/Notes"}]
        body = "# Sample\n<content>keep these literal tags</content>"
        block = function_call_to_tool_block("create_document", json.dumps({"title": "Sample", "language": "markdown", "content": body, "folder": "Work/Notes"}))
        result = asyncio.run(CreateDocumentTool().execute(block.content, ctx))
        assert result["folder"] == "Work/Notes", result
        doc_id = result["doc_id"]
        assert call({"action": "list", "folder": ""})["documents"] == []
        assert call({"action": "list", "folder": "Work/Notes"})["documents"][0]["id"] == doc_id
        read = call({"action": "read", "document_id": doc_id})
        assert read["document"]["content"] == body
        assert call({"action": "move", "document_id": doc_id, "folder": "Elsewhere"}, {"owner": "bob"})["exit_code"] == 1
        assert call({"action": "move", "document_id": doc_id, "folder": "../outside"})["exit_code"] == 1
        assert call({"action": "move", "document_id": doc_id, "folder": ""})["folder"] == ""
        assert call({"action": "list", "folder": ""})["documents"][0]["id"] == doc_id
        assert call({"action": "list_folders"}, {"owner": "bob"})["folders"] == []
        assert call({"action": "list_folders"}, {"owner": None})["exit_code"] == 1
    finally:
        set_active_document(None)
        engine.dispose()


@pytest.mark.parametrize("action,effect", [("list_folders", ToolEffect.READ_PRIVATE), ("create_folder", ToolEffect.WRITE_PRIVATE), ("move", ToolEffect.WRITE_PRIVATE)])
def test_folder_actions_have_correct_capabilities(action, effect):
    caps = capabilities_for_action("manage_documents", json.dumps({"action": action, "folder": "Work"}))
    assert effect in caps.effects
    assert ToolEffect.EXECUTE_CODE not in caps.effects
