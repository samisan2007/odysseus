"""Agent-facing reusable document tag behavior."""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import core.database as database
import src.database as legacy_database
from src.agent_tools.document_tools import CreateDocumentTool, ManageDocumentTool, set_active_document
from src.tool_capabilities import ToolEffect, capabilities_for_action
from src.tool_schemas import function_call_to_tool_block


def test_agent_manages_and_assigns_reusable_tags(monkeypatch):
    engine = create_engine("sqlite://")
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setattr(legacy_database, "SessionLocal", factory)
    monkeypatch.setattr("src.agent_tools.document_tools._missing_document_upload", lambda *args: None)
    with factory() as db:
        db.add(database.Session(
            id="chat", owner="alice", name="Chat",
            endpoint_url="http://localhost:11434", model="test",
        ))
        db.commit()

    ctx = {"owner": "alice", "session_id": "chat"}
    manage = ManageDocumentTool()
    call = lambda args, context=ctx: asyncio.run(manage.execute(json.dumps(args), context))
    try:
        research = call({"action": "create_tag", "name": "Research"})["tag"]
        urgent = call({"action": "create_tag", "name": "Urgent"})["tag"]
        assert [tag["name"] for tag in call({"action": "list_tags"})["tags"]] == ["Research", "Urgent"]

        block = function_call_to_tool_block("create_document", json.dumps({
            "title": "Tagged", "language": "markdown", "content": "# Tagged",
            "tag_ids": [research["id"], urgent["id"]],
        }))
        created = asyncio.run(CreateDocumentTool().execute(block.content, ctx))
        doc_id = created["doc_id"]
        assert {tag["name"] for tag in created["tags"]} == {"Research", "Urgent"}
        assert call({"action": "list", "tag_ids": [research["id"], urgent["id"]]})["documents"][0]["id"] == doc_id
        assert call({"action": "set_tags", "document_id": doc_id, "tag_ids": [research["id"]]})["tags"] == [research]
        assert call({"action": "set_tags", "document_id": doc_id, "tag_ids": [urgent["id"]]}, {"owner": "bob"})["exit_code"] == 1
        assert call({"action": "delete_tag", "tag_id": research["id"]})["exit_code"] == 0
        read = call({"action": "read", "document_id": doc_id})
        assert read["document"]["id"] == doc_id
        assert read["document"]["tags"] == []
        assert call({"action": "list_tags"}, {"owner": "bob"})["tags"] == []
    finally:
        set_active_document(None)
        engine.dispose()


@pytest.mark.parametrize("action,effect", [
    ("list_tags", ToolEffect.READ_PRIVATE),
    ("create_tag", ToolEffect.WRITE_PRIVATE),
    ("rename_tag", ToolEffect.WRITE_PRIVATE),
    ("set_tags", ToolEffect.WRITE_PRIVATE),
])
def test_tag_actions_have_private_capabilities(action, effect):
    caps = capabilities_for_action("manage_documents", json.dumps({"action": action}))
    assert effect in caps.effects
    assert ToolEffect.EXECUTE_CODE not in caps.effects


def test_delete_tag_is_destructive_private_write():
    caps = capabilities_for_action("manage_documents", '{"action":"delete_tag"}')
    assert ToolEffect.WRITE_PRIVATE in caps.effects
    assert ToolEffect.DESTRUCTIVE in caps.effects
