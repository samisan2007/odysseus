'''Reusable document tags: CRUD, assignment, filtering, and owner scope.'''

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import core.database as database
import routes.document_routes as routes
from core.database import Document


@pytest.fixture
def session_factory():
    engine = create_engine(
        'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool,
    )
    database.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    yield factory
    engine.dispose()


@pytest.fixture
def tag_client(session_factory, monkeypatch):
    identity = {'user': 'alice'}
    monkeypatch.setattr(routes, 'SessionLocal', session_factory)
    monkeypatch.setattr(routes, 'get_current_user', lambda request: identity['user'])
    monkeypatch.setattr(
        'src.auth_helpers.require_privilege',
        lambda request, privilege: identity['user'],
    )
    app = FastAPI()
    app.include_router(routes.setup_document_routes(None))
    with TestClient(app) as client:
        yield client, identity


def test_tag_crud_assignment_and_all_match_filter(tag_client, session_factory):
    client, _ = tag_client
    research = client.post('/api/documents/tags', json={'name': ' Research '})
    assert research.status_code == 200, research.text
    research = research.json()
    urgent = client.post('/api/documents/tags', json={'name': 'Urgent'}).json()

    reused = client.post('/api/documents/tags', json={'name': 'research'})
    assert reused.status_code == 200
    assert reused.json()['id'] == research['id']

    both = client.post('/api/document', json={
        'title': 'Both tags', 'content': '# Both',
        'tag_ids': [research['id'], urgent['id']],
    })
    assert both.status_code == 200, both.text
    assert {tag['id'] for tag in both.json()['tags']} == {research['id'], urgent['id']}
    one = client.post('/api/document', json={
        'title': 'Research only', 'content': '# One', 'tag_ids': [research['id']],
    })
    assert one.status_code == 200, one.text
    one_id = one.json()['id']
    urgent_id = urgent['id']
    research_id = research['id']

    filtered = client.get('/api/documents/library', params=[
        ('tag_id', research['id']), ('tag_id', urgent['id']), ('search', 'Both'),
    ])
    assert filtered.status_code == 200, filtered.text
    assert [doc['id'] for doc in filtered.json()['documents']] == [both.json()['id']]
    assert {tag['name'] for tag in filtered.json()['documents'][0]['tags']} == {'Research', 'Urgent'}

    patched = client.patch(
        f'/api/document/{one_id}', json={'tag_ids': [urgent_id]},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()['tags'] == [{'id': urgent['id'], 'name': 'Urgent'}]

    renamed = client.patch(
        f'/api/documents/tags/{urgent_id}', json={'name': 'Priority'},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()['name'] == 'Priority'
    tags = client.get('/api/documents/tags').json()['tags']
    assert {tag['name']: tag['document_count'] for tag in tags} == {
        'Priority': 2, 'Research': 1,
    }

    deleted = client.delete(f'/api/documents/tags/{research_id}')
    assert deleted.status_code == 200
    with session_factory() as db:
        assert db.get(Document, both.json()['id']) is not None
        assert [tag.name for tag in db.get(Document, both.json()['id']).tags] == ['Priority']


def test_tag_validation_and_owner_scope(tag_client):
    client, identity = tag_client
    alice_tag = client.post('/api/documents/tags', json={'name': 'Alice'}).json()
    other = client.post('/api/documents/tags', json={'name': 'Other'}).json()
    alice_tag_id = alice_tag['id']
    other_id = other['id']

    assert client.post('/api/documents/tags', json={'name': '   '}).status_code == 400
    conflict = client.patch(
        f'/api/documents/tags/{other_id}', json={'name': 'alice'},
    )
    assert conflict.status_code == 409
    unknown = client.post('/api/document', json={
        'title': 'Invalid', 'content': 'body', 'tag_ids': ['missing'],
    })
    assert unknown.status_code == 404

    identity['user'] = 'bob'
    assert client.get('/api/documents/tags').json() == {'tags': []}
    assert client.patch(
        f'/api/documents/tags/{alice_tag_id}', json={'name': 'Stolen'},
    ).status_code == 404
    assert client.delete(f'/api/documents/tags/{alice_tag_id}').status_code == 404
    foreign_assignment = client.post('/api/document', json={
        'title': 'Bob', 'content': 'body', 'tag_ids': [alice_tag['id']],
    })
    assert foreign_assignment.status_code == 404


def test_pdf_import_accepts_repeated_tag_ids(session_factory, monkeypatch, tmp_path):
    upload_id = '0123456789abcdef0123456789abcdef.pdf'
    pdf = tmp_path / upload_id
    pdf.write_bytes(b'%PDF-1.7')
    monkeypatch.setattr(routes, 'SessionLocal', session_factory)
    monkeypatch.setattr(
        'src.auth_helpers.require_privilege', lambda request, privilege: 'alice',
    )
    monkeypatch.setattr('src.pdf_forms.has_form_fields', lambda path: False)
    monkeypatch.setattr(
        'src.document_processor._process_pdf', lambda path, owner: 'PDF body',
    )

    def create_pdf(**kwargs):
        with session_factory() as db:
            db.add(Document(id='new-pdf', title=kwargs['title'], current_content='PDF wrapper'))
            db.commit()
        return 'new-pdf'

    monkeypatch.setattr('src.pdf_form_doc.create_plain_pdf_document', create_pdf)
    uploads = SimpleNamespace(
        upload_dir=str(tmp_path),
        save_upload=lambda file, client_ip, owner: {
            'id': upload_id, 'original_name': 'Packet.pdf',
        },
        resolve_upload=lambda upload_id, owner, auth_manager: {'path': str(pdf)},
    )
    app = FastAPI()
    app.include_router(routes.setup_document_routes(None, uploads))
    with TestClient(app) as client:
        first = client.post('/api/documents/tags', json={'name': 'Forms'}).json()
        second = client.post('/api/documents/tags', json={'name': 'Incoming'}).json()
        result = client.post(
            '/api/documents/import-pdf',
            data={'tag_ids': [first['id'], second['id']]},
            files={'file': ('Packet.pdf', b'%PDF-1.7', 'application/pdf')},
        )
    assert result.status_code == 200, result.text
    assert {tag['name'] for tag in result.json()['tags']} == {'Forms', 'Incoming'}


def test_tag_endpoints_require_document_privilege(session_factory, monkeypatch):
    monkeypatch.setenv('AUTH_ENABLED', 'true')
    monkeypatch.setenv('LOCALHOST_BYPASS', 'false')
    monkeypatch.setattr(routes, 'SessionLocal', session_factory)
    app = FastAPI()
    app.state.auth_manager = SimpleNamespace(is_configured=True)
    app.include_router(routes.setup_document_routes(None))
    with TestClient(app) as client:
        assert client.get('/api/documents/tags').status_code == 401
        assert client.post('/api/documents/tags', json={'name': 'Blocked'}).status_code == 401
